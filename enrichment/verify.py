"""Read-only checksum, source-link, cohort and ML-cutoff verification."""
from .layout import table_path
import argparse
from collections import Counter
import json
from pathlib import Path

import pandas as pd

from .common import atomic_json, digest, now, utc, stable_id
from .features import validate_sources
from .pipeline import DEFAULT_SOURCES, ROOT, input_hashes
from .storage import verify_asset


def verify(data, enrichment, ml, *, require_full=False):
    manifest=json.loads((enrichment/'manifest.json').read_text())
    if manifest['status']=='in_progress':raise ValueError('Collection is still running')
    if require_full and (manifest['status']!='complete'
            or manifest['event_count']!=manifest['full_backbone_count']
            or not set(DEFAULT_SOURCES)<=set(manifest['requested_sources'])):
        raise ValueError('Full radar/warnings/NLCD collection required; this is a pilot, partial run, or incomplete source selection')
    if input_hashes(data)!=manifest['definition']['backbone']:
        raise ValueError('Enrichment backbone is stale')
    migration=manifest.get('layout_migration')
    if migration:
        reusable=migration['reusable_definition']
        if (stable_id('definition',reusable)!=migration['reusable_definition_id'] or
                any(reusable.get(k)!=manifest['definition'].get(k) for k in ('config','backbone','acquisition'))):
            raise ValueError('Layout migration changed extraction settings')
    asset_status=Counter();retained_bytes=0
    for asset in manifest['assets']:
        state=verify_asset(enrichment,asset);asset_status[state]+=1
        if state=='bytes_verified':retained_bytes+=asset['bytes']
    tables={}
    for item in manifest['outputs']:
        path=table_path(enrichment,manifest,item)
        if path.stat().st_size!=item['bytes'] or digest(path)!=item['sha256']:
            raise ValueError(f'Source table differs: {item["path"]}')
        tables[path.stem]=pd.read_parquet(path)
    base=pd.read_parquet(data/'analysis/tornadoes.parquet')
    validate_sources(tables,manifest,base)
    coverage = tables['source_coverage']
    if 'extraction_definition_id' in coverage:
        definitions = dict(manifest.get('inherited_definitions', {}))
        definitions[manifest['definition_id']] = manifest['definition']
        for identity, definition in definitions.items():
            if stable_id('definition', definition) != identity:
                raise ValueError('Invalid extraction definition identity')
            if any(definition.get(k) != manifest['definition'].get(k) for k in ('config','backbone','acquisition')):
                raise ValueError('Inherited extraction changes scientific settings')
        for row in coverage.itertuples(index=False):
            if (row.extraction_definition_id not in definitions or row.job_id !=
                    stable_id('job',[row.extraction_definition_id,row.tornado_id,row.source])):
                raise ValueError('Source job extraction provenance differs')
        if manifest.get('repair') and dict(Counter(coverage.extraction_definition_id)) != manifest['repair']['jobs_by_extraction_definition']:
            raise ValueError('Repair job accounting differs')
    if require_full and (manifest['event_count']!=len(base)
            or not tables['source_coverage'].status.isin(['complete','unavailable']).all()):
        raise ValueError('Full collection has missing events or unfinished source jobs')
    ml_manifest=json.loads((ml/'manifest.json').read_text())
    if ml_manifest['input_enrichment_manifest_sha256']!=digest(enrichment/'manifest.json'):
        raise ValueError('ML views are stale')
    if digest(ml/'feature_dictionary.json')!=ml_manifest['feature_dictionary_sha256']:
        raise ValueError('Feature dictionary differs')
    spec=json.loads((ml/'feature_dictionary.json').read_text())
    for item in ml_manifest['files']:
        path=ml/item['path']
        if digest(path)!=item['sha256']:raise ValueError(f'ML table differs: {item["path"]}')
        frame=pd.read_parquet(path)
        if frame.tornado_id.duplicated().any():raise ValueError('Duplicate ML event')
        pd.testing.assert_series_equal(frame.tornado_id,base.tornado_id,check_dtype=False)
        pd.testing.assert_series_equal(frame.target_ef_rating,base.ef_rating,check_names=False)
        for source in ('era5','acs','tiger'):
            if source not in manifest['requested_sources'] and source+'_source_status' in frame:
                raise ValueError(f'Unrequested source status in ML view: {source}')
        if not {'acs','tiger'} <= set(manifest['requested_sources']) and any(c.startswith('post_exposure_') for c in frame):
            raise ValueError('Deferred tract-exposure features in ML view')
        if 'era5' not in manifest['requested_sources'] and any(c.startswith('post_era5_') for c in frame):
            raise ValueError('Deferred ERA5 features in ML view')
    onset=pd.read_parquet(ml/'events_onset.parquet')
    if any(c.startswith('post_') or c.startswith('env_') for c in onset):raise ValueError('Post-event fields in onset view')
    if any(c.startswith('post_') or c==spec['target'] for c in spec['onset_predictor_columns']):
        raise ValueError('Invalid onset predictor allowlist')
    env=tables.get('era5_samples',pd.DataFrame())
    if len(env):
        checked=env.merge(onset[['tornado_id','prediction_cutoff_utc']],on='tornado_id',validate='many_to_one')
        if not utc(checked.valid_at).eq(checked.prediction_cutoff_utc.dt.floor('h')).all():
            raise ValueError('ERA5 sample does not match the reported-onset hour')
        if not checked.retrospective.eq(True).all() or not checked.available_by_onset.eq(False).all():
            raise ValueError('ERA5 source availability must remain retrospective')
    return dict(verified_at=now(),status='passed',collection_status=manifest['status'],
                full_cohort_processed=(manifest['event_count']==len(base) and manifest['status']=='complete'
                                       and set(DEFAULT_SOURCES)<=set(manifest['requested_sources'])),
                selected_events=manifest['event_count'],backbone_events=len(base),
                source_status_counts=manifest['status_counts'],source_assets=len(manifest['assets']),
                source_bytes=sum(a['bytes'] for a in manifest['assets']),
                retained_source_bytes=retained_bytes,raw_asset_status_counts=dict(asset_status),
                storage=manifest.get('storage',{'mode':'archive'}),
                reproducibility=('offline ML regeneration from retained source tables; raw re-extraction requires upstream downloads'
                    if asset_status['intentionally_not_retained'] else 'all referenced raw source bytes verified locally'),
                source_tables={v['path']:v['rows'] for v in manifest['outputs']},
                ml_tables={v['path']:v['rows'] for v in ml_manifest['files']},
                enrichment_manifest_sha256=digest(enrichment/'manifest.json'),
                ml_manifest_sha256=digest(ml/'manifest.json'),
                checks=['source/table digests','keys and source links','requested cohort coverage',
                        'all backbone IDs/targets preserved','onset allowlist']+
                       (['per-job extraction definitions'] if 'extraction_definition_id' in coverage else [])+
                       (['ERA5 time cutoff'] if 'era5' in manifest['requested_sources'] else []))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',type=Path,default=ROOT/'data')
    p.add_argument('--enrichment-dir',type=Path)
    p.add_argument('--ml-dir',type=Path)
    p.add_argument('--report',type=Path)
    p.add_argument('--require-full',action='store_true',help='Reject pilots or incomplete collection before release preparation')
    a=p.parse_args(argv)
    result=verify(a.data_dir,a.enrichment_dir or a.data_dir/'enrichment',a.ml_dir or a.data_dir/'ml',require_full=a.require_full)
    if a.report:atomic_json(a.report,result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
