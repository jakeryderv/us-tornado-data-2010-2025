"""Independently verify saved files and selection completeness; never downloads.

Checks NOAA and Census manifests and exits nonzero on errors.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import argparse
import csv
import gzip
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def verify(root, start=2010, end=2025, sources="all"):
    root = Path(root).resolve()
    report = dict(checked_at=datetime.now(timezone.utc).isoformat(),
                  scope=sources,
                  start_year=start, end_year=end, errors=[], checks={}, counts={})
    checked = {}

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def path(relative):
        p = (root/relative).resolve()
        require(p.is_relative_to(root), f'Unsafe manifest path: {relative}')
        require(p.is_file(), f'Missing file: {relative}')
        return p

    def sha(p):
        if p not in checked:
            with p.open('rb') as f:
                checked[p] = hashlib.file_digest(f, 'sha256').hexdigest()
        return checked[p]

    def artifact(a):
        p = path(a['path'])
        require(sha(p) == a['sha256'], f'Checksum mismatch: {p}')
        if 'bytes' in a:
            require(p.stat().st_size == a['bytes'], f'Size mismatch: {p}')

    def walk(value):
        if isinstance(value, dict):
            if 'path' in value and 'sha256' in value:
                artifact(value)
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    def read_rows(p):
        opener = gzip.open if p.suffix == '.gz' else open
        with opener(p, 'rt', newline='', encoding='utf-8-sig') as f:
            return list(csv.DictReader(f))

    try:
        require(sources in ('all', 'noaa', 'census'), 'Invalid verification scope')
        if sources != 'census':
            manifest_path = path(f'download_manifest_{start}_{end}.json')
            manifest = json.loads(manifest_path.read_text())
            require(manifest['requested_archives_available'] and manifest['date_validation_passed'],
                    'Original sources are incomplete')
            require((manifest['start_year'], manifest['end_year']) == (start, end), 'Period mismatch')
            years = set(range(start, end+1))
            outputs = manifest['outputs']
            require(len(outputs) == 1+4*len(years), 'Unexpected number of original source outputs')
            walk(manifest)
            spc = read_rows(path(f'spc/tornadoes_{start}_{end}.csv'))
            require(all(int(r['yr']) in years and r['date'].startswith(r['yr']) for r in spc), 'SPC date mismatch')
            require(len({(r['yr'], r['om']) for r in spc}) == len(spc), 'SPC duplicate tornado identifiers')
            report['counts']['spc_tracks'] = len(spc)
            report['counts']['spc_ratings'] = dict(Counter(r['mag'] for r in spc))
            raw_totals, tornado_totals = Counter(), Counter()
            for year in sorted(years):
                entries = {o['table']: o for o in outputs if o['source'] == 'NCEI' and o['year'] == year}
                require(set(entries) == {'details','fatalities','locations'}, f'NCEI missing table: {year}')
                details = read_rows(path(entries['details']['input']))
                ids = {r['EVENT_ID'] for r in details if r['EVENT_TYPE'] == 'Tornado'}
                for table, entry in entries.items():
                    raw_path = path(entry['input'])
                    require(sha(raw_path) == entry['input_sha256'], f'NCEI input hash mismatch: {year}/{table}')
                    raw = details if table == 'details' else read_rows(raw_path)
                    expected = [r for r in raw if r['EVENT_TYPE'] == 'Tornado'] if table == 'details' else [r for r in raw if r['EVENT_ID'] in ids]
                    actual = read_rows(path(entry['path']))
                    require(actual == expected, f'NCEI extraction is not an exact tornado subset: {year}/{table}')
                    require(len(actual) == entry['rows'] and len(raw) == entry['raw_rows_validated'], 'NCEI row count mismatch')
                    require(all(int(r[entry['date_field']][:4]) == year for r in raw), f'NCEI date mismatch: {year}/{table}')
                    raw_totals[table] += len(raw)
                    tornado_totals[table] += len(actual)
            from footprint_data import validate_collection, verify_object, PREFIX, BUCKET, INVENTORY_URL
            footprints = manifest['footprints']
            require(footprints['status'] == 'complete', 'Footprint collection incomplete')
            require((footprints['start_year'], footprints['end_year']) == (start,end), 'Footprint period mismatch')
            inv_path = path(footprints['inventory']['path'])
            inv = json.loads(inv_path.read_text())
            require(not inv.get('nextPageToken'), 'Incomplete EFC inventory')
            inv_meta = json.loads(path(str(inv_path.relative_to(root))+'.metadata.json').read_text())
            require(inv_meta['url'] == INVENTORY_URL, 'EFC inventory URL mismatch')
            inventory = {i['name']:i for i in inv['items']}
            entries = [o for o in outputs if o['source'] == 'EFC']
            require(entries == footprints['outputs'], 'Footprint manifests disagree')
            require(len(entries) == len(years) and {o['year'] for o in entries} == years, 'Missing/duplicate EFC year')
            totals = Counter()
            for entry in entries:
                name = f"{PREFIX}tornado/{entry['year']}_tornado_footprint.geojson"
                item = inventory[name]
                expected_url = f"https://storage.googleapis.com/{BUCKET}/{name}?generation={item['generation']}"
                require(entry['object_name'] == name and entry['url'] == expected_url
                        and entry['generation'] == item['generation'] and entry['md5_base64'] == item['md5Hash'],
                        'EFC generation/source mismatch')
                p = path(entry['path']); verify_object(p,item)
                require(json.loads(path(entry['path']+'.metadata.json').read_text())['url'] == expected_url,
                        'EFC cached URL mismatch')
                actual = validate_collection(json.loads(p.read_text()),entry['year'])
                require(all(actual[k] == entry[k] for k in actual), 'EFC counts/quality mismatch')
                totals.update(actual['source_counts'])
            report['counts'].update(ncei_raw=dict(raw_totals), ncei_tornado=dict(tornado_totals),
                                    footprints=dict(totals), footprint_features=sum(totals.values()))
            report['checks']['efc_annual_inventory_generations_md5_and_counts'] = 'passed'
            report['checks']['original_sources'] = 'passed'
            report['manifest'] = dict(path=str(manifest_path.relative_to(root)), sha256=sha(manifest_path))

        if sources != 'noaa':
            # Share file-format readers, independently reconcile persisted outputs
            # against all selected raw sources and the pinned source inventory.
            from census_data import SOURCES, selected_sources, county_geojson, context_rows
            census_path = path(f'census_manifest_{start}_{end}.json')
            census = json.loads(census_path.read_text())
            require(census['status'] == 'complete', 'Census collection incomplete')
            require((census['start_year'], census['end_year']) == (start, end), 'Census period mismatch')
            expected_sources = selected_sources(start, end)
            require(len(census['sources']) == len(expected_sources), 'Census source count mismatch')
            require({a['id']: (a['path'], a['url']) for a in census['sources']} == expected_sources,
                    'Census source inventory differs from pinned releases')
            walk(census)
            for entry in census['sources']:
                meta = json.loads(path(entry['path'] + '.metadata.json').read_text())
                require(meta['url'] == entry['url'], 'Census source URL mismatch')
            expected_map = county_geojson(path(SOURCES['counties_2020'][0]))
            actual_map = json.loads(path(census['boundaries']['path']).read_text())
            require(actual_map == expected_map, 'County GeoJSON differs from source KML')
            codes = {f['properties']['GEOID'] for f in expected_map['features']}
            expected_rows = [{k: str(v) for k, v in r.items()}
                             for r in context_rows(root, start, end, codes)]
            actual_rows = read_rows(path(census['context']['path']))
            require(actual_rows == expected_rows, 'County context differs from source estimates')
            require(len(actual_rows) == census['context']['rows'], 'Census row count mismatch')
            require(len(codes) == census['boundaries']['features'], 'County map feature count mismatch')
            require(dict(Counter(r['year'] for r in actual_rows)) == census['rows_by_year'],
                    'Census annual coverage mismatch')
            missing = sorted({r['county_fips'] for r in actual_rows if r['map_2020_fips_present'] == 'false'})
            require(missing == census['map_unmatched_fips'], 'Census map coverage flags differ')
            report['census_manifest'] = dict(path=str(census_path.relative_to(root)), sha256=sha(census_path))
            report['counts'].update(census_county_years=len(actual_rows), county_map_features=len(codes),
                                    census_map_unmatched_fips=missing)
            report['checks']['census_sources_and_derivations'] = 'passed'

        folders = (['spc', 'ncei_storm_events', 'event_footprints'] if sources == 'noaa' else
                   ['census_population', 'census_boundaries'] if sources == 'census' else
                   ['spc', 'ncei_storm_events', 'event_footprints', 'census_population', 'census_boundaries'])
        sidecars = [p for folder in folders for p in (root / folder).rglob('*.metadata.json')]
        for p in sidecars:
            relative = p.relative_to(root)
            meta = json.loads(p.read_text())
            data = path(str(relative).removesuffix('.metadata.json'))
            require(meta.get('sha256') == sha(data), f'Cached file hash mismatch: {data}')
            if 'bytes' in meta:
                require(meta['bytes'] == data.stat().st_size, f'Cached file length mismatch: {data}')
        report['checks']['hashes_sizes_and_payloads'] = 'passed'
        report['checks']['unique_files_hashed'] = len(checked)
        require(not [p for folder in folders for p in (root / folder).rglob('*.part')],
                'Unfinished download parts remain')
        report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['errors'].append(f'{type(exc).__name__}: {exc}')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT/'data')
    parser.add_argument('--start-year', type=int, default=2010)
    parser.add_argument('--end-year', type=int, default=2025)
    parser.add_argument('--sources', choices=('all', 'noaa', 'census'), default='all')
    parser.add_argument('--output', type=Path, default=ROOT/'reports'/'verification'/'latest.json')
    args = parser.parse_args()
    result = verify(args.data_dir, args.start_year, args.end_year, sources=args.sources)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
