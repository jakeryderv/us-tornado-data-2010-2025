"""Resumable optional collection. Existing backbone tables are read-only inputs."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import deque
from threading import Event
import time
from dataclasses import asdict
import json
from pathlib import Path
import sys

import geopandas as gpd
import pandas as pd
from shapely import wkt
from shapely.geometry import Point
from shapely.ops import unary_union

from scripts.build_crosswalk import Rules, prepare_spc
from .common import (BudgetExceeded, Cache, Config, Unavailable, atomic_json,
                     digest, now, project, stable_id)
from .land import collect_acs, collect_nlcd, collect_tracts
from .weather import collect_radar, collect_warnings
from .era5 import collect_era5, group_key, plan_requests
from .storage import verify_asset
from .checkpoints import BatchedRows, Checkpoints, batches, references, load as load_checkpoint
from .batching import radar_plans, nlcd_plans, warning_plans
from .layout import table_directory, table_path, new_table_directory

ROOT = Path(__file__).resolve().parents[1]
COLLECTORS = {'radar': collect_radar, 'warnings': collect_warnings,
              'era5': collect_era5, 'nlcd': collect_nlcd,
              'acs': collect_acs, 'tiger': collect_tracts}
DEFAULT_SOURCES = ('radar', 'warnings', 'nlcd')
OPTIONAL_TABLE_SOURCES = {'era5_samples': 'era5', 'acs_tracts': 'acs',
                          'tract_boundaries': 'tiger', 'tornado_tracts': 'tiger'}
TABLES = {'radar_detections': ['record_id'], 'tornado_radar': ['tornado_id','record_id'],
          'warning_updates': ['record_id'], 'tornado_warnings': ['tornado_id','record_id'],
          'era5_samples': ['record_id'], 'acs_tracts': ['record_id'],
          'tract_boundaries': ['record_id'], 'tornado_tracts': ['tornado_id','tract_id'],
          'nlcd_samples': ['record_id']}


def input_hashes(data):
    names = ['tornadoes', 'source_crosswalk', 'tornado_footprints', 'tornado_counties']
    hashes = {f'analysis/{n}.parquet': digest(data/'analysis'/f'{n}.parquet') for n in names}
    # Verify the frozen input table digests against the canonical analysis manifest.
    manifest = json.loads((data/'analysis/manifest.json').read_text())
    for record in manifest['files']:
        name = 'analysis/'+Path(record['path']).name
        if name in hashes and hashes[name] != record['sha256']:
            raise ValueError(f'Stale backbone table: {name}; rebuild analysis first')
    return hashes


def event_records(data, config):
    tornadoes = pd.read_parquet(data/'analysis/tornadoes.parquet')
    prepared = prepare_spc(tornadoes, Rules())
    footprints = gpd.read_parquet(data/'analysis/tornado_footprints.parquet').set_index('footprint_id')
    crosswalk = pd.read_parquet(data/'analysis/source_crosswalk.parquet')
    links = crosswalk.loc[crosswalk.accepted & crosswalk.source_table.eq('tornado_footprints')]
    footprint_links = links.groupby('tornado_id').source_id.agg(list).to_dict()
    counties = pd.read_parquet(data/'analysis/tornado_counties.parquet')
    county_links = counties.groupby('tornado_id').county_fips.agg(lambda x:sorted(set(x.dropna()))).to_dict()
    records = []
    for source, record in zip(tornadoes.to_dict('records'), prepared):
        geometry = record['geometry']
        usable = geometry is not None and pd.notna(record['start'])
        area, area_method, footprint_ids = None, None, []
        lon, lat = source['start_longitude'], source['start_latitude']
        if usable:
            footprint_ids = footprint_links.get(source['tornado_id'], [])
            polys = [footprints.loc[k].geometry for k in footprint_ids]
            if polys and all(p is not None and p.is_valid and p.area > 0 for p in polys):
                area = unary_union(polys)
                area_method = 'union_of_accepted_footprints'
            else:
                area = project(project(geometry, lon, lat).buffer(config.fallback_buffer_m), lon, lat, inverse=True)
                area_method = 'fixed_buffer_of_reported_endpoint_track'
        county_ids = county_links.get(source['tornado_id'], [])
        states = sorted(set([source['state_fips']]+[v[:2] for v in county_ids]))
        records.append(dict(tornado_id=source['tornado_id'], year=int(source['year']),
            state_fips=source['state_fips'], area_states=states, county_fips=county_ids,
            start_utc=None if pd.isna(record['start']) else record['start'].isoformat(),
            end_utc=None if pd.isna(record['end']) else record['end'].isoformat(),
            longitude=None if pd.isna(lon) else float(lon), latitude=None if pd.isna(lat) else float(lat),
            usable=usable, area_id=stable_id('area',[source['tornado_id'], area_method, area.wkt if area else None]),
            area_wkt=area.wkt if area else None, area_method=area_method,
            footprint_ids=footprint_ids, retrospective=True))
    return records


def write_table(path, rows, keys):
    frame = pd.DataFrame(rows) if rows else pd.DataFrame({k:pd.Series(dtype='string') for k in keys})
    if rows:
        # A warning observed in multiple queries may have its original text
        # resolved by only one. Prefer the row carrying that extra provenance.
        if 'text_asset_id' in frame:
            frame = frame.sort_values('text_asset_id', na_position='first')
        frame = frame.drop_duplicates(keys, keep='last').sort_values(keys).reset_index(drop=True)
    if 'geometry_wkt' in frame:
        geometry = gpd.GeoSeries.from_wkt(frame.pop('geometry_wkt'), crs='EPSG:4326')
        frame = gpd.GeoDataFrame(frame, geometry=geometry)
    temp = path.with_suffix('.tmp.parquet')
    frame.to_parquet(temp, index=False)
    check = gpd.read_parquet(temp) if isinstance(frame,gpd.GeoDataFrame) else pd.read_parquet(temp)
    pd.testing.assert_frame_equal(frame, check)
    temp.replace(path)
    return dict(path=path.name, rows=len(frame), columns=list(frame.columns), sha256=digest(path), bytes=path.stat().st_size)


def save_job(path, result, output):
    Checkpoints(output).save(path, result)


def load_job(path, output):
    return load_checkpoint(path, output)


def prune_checkpoints(output, paths):
    """Only retire this completed run's checkpoints and now-unreferenced blobs."""
    retired=set()
    for path in paths:
        if not path.exists():continue
        saved=json.loads(path.read_text())
        retired.update(r['path'] for r in references(saved))
        path.unlink()
    live=set()
    for path in (output/'jobs').glob('job_*.json'):
        live.update(r['path'] for r in references(json.loads(path.read_text())))
    for relative in retired-live:
        path=(output/relative).resolve()
        if not path.is_relative_to((output/'job_tables').resolve()):raise ValueError('Unsafe checkpoint path')
        path.unlink(missing_ok=True)


def job_order(event, source, config, cache=None):
    if cache is not None and source == 'nlcd' and event['tornado_id'] in cache.nlcd_plans:
        return (event['year'], cache.nlcd_plans[event['tornado_id']], event['tornado_id'])
    if cache is not None and source == 'radar' and event['tornado_id'] in cache.radar_plans:
        begin, _, bounds = cache.radar_plans[event['tornado_id']]
        return (begin.isoformat(), bounds, event['tornado_id'])
    if source in ('tiger','acs'):
        return (event['year'],tuple(event['area_states']),event['tornado_id'])
    if source=='era5':
        return (*(group_key(event,config) or ('',0,0)),event['tornado_id'])
    return (event['start_utc'] or '',event['tornado_id'])


def repair_snapshot(path, definition, selected, sources, output):
    """Explicitly inherit verified jobs after a reviewed error-only code repair.

    This is not automatic code compatibility: the caller must supply a frozen
    pre-repair manifest. Original job IDs and extraction definitions survive.
    """
    path = Path(path)
    if path.resolve() == (output/'manifest.json').resolve():
        raise ValueError('Repair requires a separate frozen manifest copy')
    prior = json.loads(path.read_text())
    if prior['definition_id'] != stable_id('definition', prior['definition']):
        raise ValueError('Invalid repair definition identity')
    for field in ('config', 'backbone', 'acquisition'):
        if prior['definition'].get(field) != definition.get(field):
            raise ValueError(f'Repair cannot change {field}')
    if (set(prior['selected_event_ids']) != {e['tornado_id'] for e in selected}
            or set(prior['requested_sources']) != set(sources)):
        raise ValueError('Repair cannot change the event or source selection')
    if prior.get('checkpoints_retired'):
        raise ValueError('Repair requires the original per-job checkpoints')
    ids = set(prior.get('job_ids', []))
    expected = {stable_id('job', [prior['definition_id'], e['tornado_id'], s])
                for e in selected for s in sources}
    if ids != expected:
        raise ValueError('Repair snapshot must contain one original job per event/source')
    if any(not (output/'jobs'/(j.replace(':','_')+'.json')).exists() for j in ids):
        raise ValueError('Repair snapshot checkpoints are missing')
    return prior


def collect(data, output, selected, sources, config, *, max_bytes, timeout,
            storage='compact', max_cache_bytes=5_000_000_000, workers=4, per_host=2, acquisition='batch', parallel_sources=False,
            repair_from=None):
    if workers < 1 or per_host < 1:
        raise ValueError('Worker and per-host limits must be positive')
    if acquisition not in ('event', 'batch'):
        raise ValueError('Unknown acquisition strategy')
    if parallel_sources and 'era5' in sources:
        raise ValueError('ERA5 must run separately from parallel source queues')
    if parallel_sources and workers < len(sources):
        raise ValueError('Parallel source queues need at least one worker per requested source')
    # Prevent another collector or cleanup from changing in-use artifacts.
    import fcntl
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with (output/'collection.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another collector is using this output directory') from None
        return _collect(data,output,selected,sources,config,max_bytes=max_bytes,timeout=timeout,
                        storage=storage,max_cache_bytes=max_cache_bytes,workers=workers,per_host=per_host,
                        acquisition=acquisition,parallel_sources=parallel_sources,repair_from=repair_from)


def extraction_definition(data, config, acquisition):
    hashes = input_hashes(data)
    code = {name:digest(Path(__file__).parent/name) for name in
            ['common.py','cache.py','transport.py','checkpoints.py','storage.py','pipeline.py','land.py','weather.py','batching.py','era5.py','layout.py']}
    code['build_crosswalk.py']=digest(ROOT/'scripts/build_crosswalk.py')
    return dict(config=json.loads(json.dumps(asdict(config))), backbone=hashes, code=code, acquisition=acquisition)


def _collect(data, output, selected, sources, config, *, max_bytes, timeout, storage, max_cache_bytes, workers, per_host, acquisition, parallel_sources, repair_from=None):
    if not selected:
        raise ValueError('Select at least one tornado')
    definition = extraction_definition(data, config, acquisition)
    fingerprint = stable_id('definition', definition)
    output.mkdir(parents=True, exist_ok=True)
    old_manifest = output/'manifest.json'
    previous={}
    if old_manifest.exists():
        previous = json.loads(old_manifest.read_text())
        old_config={k:v for k,v in previous['definition']['config'].items() if not k.startswith('hrrr_')}
        new_config={k:v for k,v in definition['config'].items() if not k.startswith('era5_')}
        migrating='hrrr' in previous.get('requested_sources',[]) and old_config==new_config
        if previous['definition']['config'] != definition['config'] and not migrating:
            raise ValueError('Different extraction settings require a separate --output directory')
    if (previous.get('status')=='complete' and (previous.get('definition_id')==fingerprint or
                 previous.get('layout_migration', {}).get('reusable_definition') == definition)
            and set(previous['selected_event_ids'])=={e['tornado_id'] for e in selected}
            and set(previous['requested_sources'])==set(sources)
            and previous.get('storage',{}).get('mode')==storage):
        for item in previous['outputs']:
            if digest(table_path(output,previous,item))!=item['sha256']:raise ValueError('Completed table changed')
        for asset in previous['assets']:verify_asset(output,asset)
        completed_cache=Cache(output/'raw',timeout=timeout,max_bytes=max_bytes,
                              storage=storage,max_cache_bytes=max_cache_bytes)
        completed_cache.finish()
        if previous.get('checkpoints_retired'):
            prune_checkpoints(output,[output/'jobs'/(v.replace(':','_')+'.json')
                                     for v in previous.get('job_ids',[])])
        print('Completed selection verified; no downloads or extraction needed.',flush=True)
        return dict(previous,resumed_complete=True,new_downloaded_bytes=0)
    inherited = repair_snapshot(repair_from, definition, selected, sources, output) if repair_from else None
    cache = Cache(output/'raw', timeout=timeout, max_bytes=max_bytes,
                  storage=storage,max_cache_bytes=max_cache_bytes,per_host=per_host)
    cache.acquisition = acquisition
    cache.radar_plans = radar_plans(selected, config) if acquisition == 'batch' and 'radar' in sources else {}
    cache.nlcd_plans = nlcd_plans(selected, config) if acquisition == 'batch' and 'nlcd' in sources else {}
    cache.warning_plans, cache.warning_dates, cache.bulk_text_days = (warning_plans(selected)
        if acquisition == 'batch' and 'warnings' in sources else ({}, set(), set()))
    cache.era5_plans=plan_requests(selected,config) if 'era5' in sources else {}
    # Previous verified extraction snapshots permit adoption of legacy caches.
    if previous.get('outputs'):
        for item in previous['outputs']:
            if digest(table_path(output,previous,item))!=item['sha256']:raise ValueError('Previous source table changed')
        for asset in previous.get('assets',[]):
            verify_asset(output,asset)
        cache.commit(previous.get('assets',[]))
    # Deduplicate during ingestion: all state ACS tracts must not be repeated
    # in memory once per tornado during a 20,000-event collection.
    frames = {name:{} for name in TABLES
              if name not in OPTIONAL_TABLE_SOURCES or OPTIONAL_TABLE_SOURCES[name] in sources}
    provenance_records = {}
    coverage, asset_records = [], {}
    manifest = dict(schema_version=2, created_at=now(), status='in_progress',
                    definition=definition, definition_id=fingerprint,
                    table_directory=new_table_directory(data,output),
                    selected_event_ids=[x['tornado_id'] for x in selected], requested_sources=sources,
                    storage=dict(mode=storage,max_cache_bytes=max_cache_bytes),
                    event_count=len(selected), full_backbone_count=len(pd.read_parquet(data/'analysis/tornadoes.parquet')))
    if inherited:
        manifest['inherited_definitions'] = {inherited['definition_id']: inherited['definition']}
        manifest['repair'] = dict(input_manifest_sha256=digest(Path(repair_from)),
            original_definition_id=inherited['definition_id'],
            original_status_counts=inherited['status_counts'],
            basis='Explicit reuse after reviewed failure-handling fixes; scientific settings and backbone unchanged')
    atomic_json(old_manifest, manifest)
    jobs = output/'jobs'
    jobs.mkdir(exist_ok=True)
    budget_reached = Event()
    checkpoints = Checkpoints(output)
    seen_batches = set()
    paths=[];statuses=Counter();durations=Counter();finished=Counter()
    source_wall = Counter()
    source_started, source_finished = {}, Counter()
    total_jobs = len(selected) * len(sources)
    run_started = time.monotonic()

    def run_job(event, name):
        started = time.monotonic()
        job_id = stable_id('job', [fingerprint, event['tornado_id'], name])
        job_path = jobs / (job_id.replace(':', '_') + '.json')
        original_path = None
        if inherited:
            original_id = stable_id('job', [inherited['definition_id'], event['tornado_id'], name])
            original_path = jobs / (original_id.replace(':', '_') + '.json')
        owner = cache.fork()
        result = None
        policy_changed = False
        for candidate_path in dict.fromkeys(p for p in (job_path, original_path) if p is not None):
            if not candidate_path.exists():
                continue
            candidate = load_checkpoint(candidate_path, output, memo=cache.memo)
            expected_id = stable_id('job', [fingerprint if candidate_path == job_path else inherited['definition_id'], event['tornado_id'], name])
            if (candidate['coverage']['job_id'] != expected_id
                    or candidate['coverage']['tornado_id'] != event['tornado_id']
                    or candidate['coverage']['source'] != name):
                raise ValueError('Checkpoint identity does not match requested job')
            if candidate['coverage']['status'] in ('complete', 'unavailable'):
                reusable = True
                for asset in candidate['assets']:
                    old_policy = asset.get('retention')
                    asset['retention'] = cache.retention(asset['url'], Path(asset['path']).suffix)
                    policy_changed |= old_policy != asset['retention']
                    if storage == 'archive' and old_policy == 'optional' and not (output / asset['path']).exists():
                        reusable = False
                        continue
                    # Cache publication replaces body+metadata while holding this
                    # lock; do not inspect a half-published replacement version.
                    with cache._state:
                        verify_asset(output, asset)
                if reusable:
                    result = candidate
                    job_path = candidate_path
                    result['coverage']['extraction_definition_id'] = (fingerprint if expected_id == job_id else inherited['definition_id'])
                    break
        new = result is None
        if new:
            status, reason, tables = 'complete', None, {}
            try:
                if budget_reached.is_set():
                    raise BudgetExceeded('Not attempted after this run reached download budget')
                if not event['usable']:
                    raise Unavailable('Invalid or unknown reported start time/location')
                tables = {table: rows if isinstance(rows, BatchedRows) else BatchedRows(rows)
                          for table, rows in COLLECTORS[name](event, owner, config).items()}
            except Unavailable as exc:
                status, reason = 'unavailable', str(exc)
            except BudgetExceeded as exc:
                budget_reached.set()
                status, reason = 'budget_exceeded', str(exc)
            except Exception as exc:
                status, reason = 'failed', str(exc)
                secret = __import__('os').environ.get('CENSUS_API_KEY', '')
                if secret:
                    reason = reason.replace(secret, '[redacted]')
            assets = [dict(cache.assets[k], path='raw/' + Path(cache.assets[k]['path']).name) for k in sorted(owner.touched)]
            cov = dict(tornado_id=event['tornado_id'], source=name, status=status, reason=reason, job_id=job_id,
                       extraction_definition_id=fingerprint)
            result = dict(coverage=cov, tables=tables, assets=assets)
        return result, owner, job_path, new or policy_changed, time.monotonic() - started

    step = 0
    def phase_results(pool, phase):
        queues, events = {}, {}
        for name in phase:
            # Missing locations remain reportable jobs and sort after usable plans.
            events[name] = iter(sorted(selected, key=lambda e: (e['tornado_id'] not in
                (cache.radar_plans if name == 'radar' else cache.nlcd_plans if name == 'nlcd' else {}),
                job_order(e, name, config, cache))))
            queues[name] = deque()
        def submit_next(name):
            event = next(events[name], None)
            if event is not None:
                source_started.setdefault(name, time.monotonic())
                queues[name].append(pool.submit(run_job, event, name))
        for i in range(1 if phase == ['era5'] else workers):
            submit_next(phase[i % len(phase)])
        while any(queues.values()):
            # Wait for any source's next ordered result, not a global FIFO. A
            # slow warning export must not stall NOAA and USGS work queues.
            wait([q[0] for q in queues.values() if q], return_when=FIRST_COMPLETED)
            for name in phase:
                q = queues[name]
                if q and q[0].done():
                    yield q.popleft().result()
                    submit_next(name)

    # Bounded submission: at most workers results/active jobs, never 100k futures.
    # Commit in stable event order within each independent source table family.
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for phase in ([sources] if parallel_sources else [[name] for name in sources]):
                for result, owner, job_path, needs_save, seconds in phase_results(pool, phase):
                    if needs_save:
                        checkpoints.save(job_path, result)
                    cache.commit(result['assets'], owner=owner)
                    paths.append(job_path)
                    coverage.append(result['coverage'])
                    asset_records.update({a['asset_id']:a for a in result['assets']})
                    for table, rows in result['tables'].items():
                        for part in batches(rows):
                            batch_key = (table, part.identity)
                            if batch_key in seen_batches:
                                continue
                            seen_batches.add(batch_key)
                            for row in part:
                                key = tuple(row[k] for k in TABLES[table])
                                previous_row = frames[table].get(key)
                                if not previous_row or not previous_row.get('text_asset_id') or row.get('text_asset_id'):
                                    frames[table][key] = row
                                for role, asset in row.items():
                                    if role.endswith('asset_id') and asset:
                                        record_id = row.get('record_id') or stable_id(table, row)
                                        provenance_records[(table,record_id,role,asset)] = dict(table=table,record_id=record_id,role=role,asset_id=asset)
                    cov = result['coverage']
                    name = cov['source']
                    statuses[cov['status']] += 1
                    finished[cov['tornado_id']] += 1
                    durations[name] += seconds
                    source_finished[name] += 1
                    if source_finished[name] == len(selected):
                        source_wall[name] = time.monotonic() - source_started[name]
                    step += 1
                    print(f"[{step}/{total_jobs}] {cov['tornado_id']} {name}: {cov['status']}", flush=True)
                    if step % 25 == 0 or step == total_jobs:
                        atomic_json(output / 'progress.json', dict(jobs_finished=step,jobs_requested=total_jobs,
                            events_finished=sum(v==len(sources) for v in finished.values()),events_requested=len(selected),
                            elapsed_seconds=time.monotonic()-run_started, source_seconds=dict(durations),
                            source_wall_seconds=dict(source_wall), status_counts=dict(statuses), workers=workers,
                            per_host=per_host, parallel_sources=parallel_sources, **cache.snapshot()))

    finally:
        cache.close_sessions()

    table_dir = table_directory(output,manifest)
    table_dir.mkdir(exist_ok=True)
    provenance = list(provenance_records.values())
    outputs = [write_table(table_dir/(name+'.parquet'), list(rows.values()), TABLES[name]) for name,rows in frames.items()]
    outputs += [write_table(table_dir/'source_coverage.parquet',coverage,['tornado_id','source']),
                write_table(table_dir/'record_provenance.parquet',provenance,['table','record_id','role','asset_id']),
                write_table(table_dir/'event_areas.parquet',[dict(**{k:v for k,v in e.items() if k!='area_wkt'},
                    geometry_wkt=e['area_wkt']) for e in selected],['tornado_id'])]
    statuses = pd.Series([x['status'] for x in coverage]).value_counts().to_dict()
    manifest.update(status='partial' if any(x in statuses for x in ['failed','budget_exceeded']) else 'complete',
                    completed_at=now(), status_counts=statuses, outputs=outputs,
                    assets=sorted(asset_records.values(),key=lambda x:x['asset_id']), downloaded_bytes=cache.downloaded)
    if inherited:
        manifest['repair']['jobs_by_extraction_definition'] = dict(Counter(r['extraction_definition_id'] for r in coverage))
    manifest.update(storage=dict(mode=storage,max_cache_bytes=max_cache_bytes),
                    source_seconds=dict(durations),source_wall_seconds=dict(source_wall),
                    performance=dict(workers=workers,per_host=per_host,parallel_sources=parallel_sources,
                        elapsed_seconds=time.monotonic()-run_started,transport_stats=dict(cache.stats)),job_ids=[r['job_id'] for r in coverage],
                    checkpoints_retired=(manifest['status']=='complete' and len(selected)==manifest['full_backbone_count']
                                         and storage=='compact' and set(DEFAULT_SOURCES)<=set(sources)))
    # Commit the validated consolidated tables before pruning their checkpoints.
    atomic_json(old_manifest,manifest)
    cache.finish()
    # Retire the old environmental table only after its replacement snapshot commits.
    if 'hrrr' in previous.get('requested_sources',[]):
        (table_dir/'hrrr_samples.parquet').unlink(missing_ok=True)
    for name, source in OPTIONAL_TABLE_SOURCES.items():
        if source in previous.get('requested_sources',[]) and source not in sources:
            (table_dir/(name+'.parquet')).unlink(missing_ok=True)
    if manifest['checkpoints_retired']:
        if inherited:
            paths += [jobs/(j.replace(':','_')+'.json') for j in inherited['job_ids']]
        prune_checkpoints(output,paths)
    manifest['storage'].update(evicted_bytes=cache.evicted_bytes,
                              cache_bytes=sum(e['bytes'] for e in cache.entries.values()))
    atomic_json(old_manifest,manifest)
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    parser.add_argument('--output',type=Path)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--all-events',action='store_true')
    group.add_argument('--limit',type=int)
    group.add_argument('--event-id',action='append')
    parser.add_argument('--sources',nargs='+',choices=list(COLLECTORS),default=list(DEFAULT_SOURCES),
                        help='Defaults to radar, warnings and NLCD; ACS, TIGER and ERA5 are deferred opt-in extensions')
    parser.add_argument('--start-year',type=int,default=2010)
    parser.add_argument('--end-year',type=int,default=2025)
    parser.add_argument('--max-download-gb',type=float,default=5)
    parser.add_argument('--storage',choices=['compact','cache','archive'],default='compact')
    parser.add_argument('--cache-gb',type=float,default=5,help='Bound on disposable source bodies, separate from download traffic')
    parser.add_argument('--timeout',type=int,default=90)
    parser.add_argument('--workers',type=int,default=4,help='Bounded concurrent extraction jobs; use 1 for serial comparison')
    parser.add_argument('--per-host',type=int,default=2,help='Maximum simultaneous HTTP requests per service')
    parser.add_argument('--acquisition', choices=['batch','event'], default='batch',
                        help='Batch shared queries (default); event keeps individual queries for comparison')
    parser.add_argument('--parallel-sources', action='store_true', help='Interleave independent source queues within the worker and host limits; ERA5 excluded')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--repair-from',type=Path,
                        help='Frozen pre-repair manifest: explicitly reuse completed checkpoints after reviewed error-only code fixes')
    args=parser.parse_args(argv)
    if not 2010<=args.start_year<=args.end_year<=2025 or args.max_download_gb<=0 or args.cache_gb<=0 or args.timeout<=0 or args.workers<1 or args.per_host<1 or (args.limit is not None and args.limit<=0):
        parser.error('Invalid years, limit, timeout or download budget')
    config=Config();config.validate()
    events=event_records(args.data_dir,config)
    events=[e for e in events if args.start_year<=e['year']<=args.end_year]
    if args.event_id:
        events=[e for e in events if e['tornado_id'] in args.event_id]
        if set(args.event_id)-{e['tornado_id'] for e in events}:parser.error('Unknown or out-of-range event ID')
    if args.limit:events=events[:args.limit]
    output=args.output or args.data_dir/'enrichment'
    if args.dry_run:
        print(json.dumps(dict(events=len(events),sources=args.sources,output=str(output),config=asdict(config),
                             max_download_gb=args.max_download_gb,storage=args.storage,cache_gb=args.cache_gb,workers=args.workers,per_host=args.per_host,acquisition=args.acquisition,parallel_sources=args.parallel_sources,
                             environment='ERA5; retrospective only' if 'era5' in args.sources else 'Deferred; ERA5 is not requested'),indent=2))
        return 0
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env',override=False)
    result=collect(args.data_dir,output,events,args.sources,config,
                   max_bytes=int(args.max_download_gb*1e9),timeout=args.timeout,
                   storage=args.storage,max_cache_bytes=int(args.cache_gb*1e9),workers=args.workers,per_host=args.per_host,
                   acquisition=args.acquisition,parallel_sources=args.parallel_sources,repair_from=args.repair_from)
    return 0 if result['status']=='complete' else 2


if __name__=='__main__':
    sys.exit(main())
