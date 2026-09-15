"""Build three convenient analysis tables from the retained source collection."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import sys
from tempfile import TemporaryDirectory

import pandas as pd
import pyarrow

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Selected fields only. Ambiguous source loss amounts and segment/county flags
# remain available in the original CSV instead of acquiring inferred meanings.
SPC_FIELDS = {
    'om': ('spc_event_id', 'string', 'Opaque SPC identifier; meaningful together with year.'),
    'yr': ('year', 'Int16', 'Source start year.'),
    'date': ('start_date', 'datetime64[ns]', 'Source calendar date; no UTC conversion.'),
    'time': ('start_time', 'string', 'Source clock time; interpret with spc_timezone_code.'),
    'edat': ('end_date', 'datetime64[ns]', 'Source end calendar date; no UTC conversion.'),
    'etime': ('end_time', 'string', 'Source end clock time; not a UTC timestamp.'),
    'tz': ('spc_timezone_code', 'Int16', 'Source code: 3=CST, 9=GMT, 0=unknown; retained as published.'),
    'st': ('state', 'string', 'Source state abbreviation; multi-state tracks are not split.'),
    'stf': ('state_fips', 'string', 'Two-character source state FIPS code.'),
    'mag': ('spc_rating_code', 'Int8', 'Original rating code, including -9 for unknown.'),
    'inj': ('injuries', 'Int32', 'Source track injury count; retained without imputation.'),
    'fat': ('fatalities', 'Int32', 'Source track fatality count; retained without imputation.'),
    'slat': ('start_latitude', 'Float64', 'Source start latitude, decimal degrees.'),
    'slon': ('start_longitude', 'Float64', 'Source start longitude, decimal degrees.'),
    'elat': ('end_latitude', 'Float64', 'Source end latitude, decimal degrees.'),
    'elon': ('end_longitude', 'Float64', 'Source end longitude, decimal degrees.'),
    'len': ('path_length_miles', 'Float64', 'Source path length in miles.'),
    'wid': ('path_width_yards', 'Int32', 'Source maximum path width in yards.'),
    'ns': ('states_affected', 'Int8', 'Source number of affected states.'),
}


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def tornado_table(source):
    """Keep every track, explicit opaque IDs, and nullable EF labels."""
    missing = set(SPC_FIELDS) - set(source.columns)
    if missing:
        raise ValueError(f'Missing SPC columns: {sorted(missing)}')
    if source[['yr', 'om']].isna().any().any() or source.duplicated(['yr', 'om']).any():
        raise ValueError('SPC year/identifier must be non-null and unique; no automatic deduplication')
    result = pd.DataFrame(index=source.index)
    for old, (new, dtype, _) in SPC_FIELDS.items():
        values = source[old]
        if dtype == 'datetime64[ns]':
            result[new] = pd.to_datetime(values, format='%Y-%m-%d', errors='raise').astype(dtype)
        else:
            result[new] = values.astype(dtype)
    result['state_fips'] = result['state_fips'].str.zfill(2)
    if not result.state_fips.str.fullmatch(r'\d{2}').fillna(False).all():
        raise ValueError('Invalid state FIPS')
    if not result.spc_rating_code.isin([-9, 0, 1, 2, 3, 4, 5]).all():
        raise ValueError('Unexpected SPC EF code; review source schema')
    result.insert(0, 'tornado_id', 'spc:' + result.year.astype('string') + ':' + result.spc_event_id)
    result['ef_rating'] = result.spc_rating_code.where(result.spc_rating_code.between(0, 5)).astype('Int8')
    result['ef_rating_known'] = result.ef_rating.notna().astype('boolean')
    for end in ['start', 'end']:
        lat, lon = result[f'{end}_latitude'], result[f'{end}_longitude']
        result[f'{end}_coordinates_valid'] = (lat.between(-90, 90) & lon.between(-180, 180)
                                             & lat.ne(0) & lon.ne(0)).fillna(False).astype('boolean')
    return result.sort_values(['year', 'spc_event_id']).reset_index(drop=True)


def county_table(source):
    types = {'year': 'Int16', 'county_fips': 'string', 'state_name': 'string', 'county_name': 'string',
             'population': 'Int64', 'housing_units': 'Int64', 'estimate_vintage': 'Int16',
             'map_2020_fips_present': 'boolean'}
    result = source[list(types)].astype(types)
    if result.year.isna().any() or not result.county_fips.str.fullmatch(r'\d{5}').fillna(False).all() or result.duplicated(['year', 'county_fips']).any():
        raise ValueError('County FIPS must be five-character strings, unique by year')
    return result.sort_values(['year', 'county_fips']).reset_index(drop=True)


def annual_table(tornadoes, counties, start, end, ncei_counts, dat_counts):
    rows = []
    for year in range(start, end + 1):
        tracks = tornadoes[tornadoes.year.eq(year)]
        county = counties[counties.year.eq(year)]
        row = {'year': year, 'spc_tracks': len(tracks), 'spc_ef_unknown': int(tracks.ef_rating.isna().sum())}
        row.update({f'spc_ef{i}': int(tracks.ef_rating.eq(i).sum()) for i in range(6)})
        row['spc_known_ef_fraction'] = tracks.ef_rating.notna().mean() if len(tracks) else float('nan')
        # Missing partitions raise rather than becoming fabricated zero counts.
        row.update({f'ncei_tornado_{table}_rows': ncei_counts[year, table] for table in ['details', 'fatalities', 'locations']})
        row.update({f'dat_{layer}': dat_counts[year, layer] for layer in ['points', 'lines', 'polygons']})
        row.update(census_county_rows=len(county), census_counties_without_2020_map=int(county.map_2020_fips_present.eq(False).sum()))
        rows.append(row)
    result = pd.DataFrame(rows)
    for column in result:
        if column != 'spc_known_ef_fraction':
            result[column] = result[column].astype('Int64')
    return result


def build_analysis(data, output=None, start=2010, end=2025):
    """Generate files and reconciliation metadata; caller verifies source collection."""
    data = Path(data).resolve()
    output = Path(output).resolve() if output else data/'analysis'
    if output == data or output in [data/x for x in ['spc','ncei_storm_events','nws_dat','census_population','census_boundaries']]:
        raise ValueError('Analysis output must not overwrite a source directory')
    inputs = []
    def input_path(relative):
        path = data/relative
        inputs.append({'path':relative, 'sha256':sha(path)})
        return path
    spc_source = pd.read_csv(input_path(f'spc/tornadoes_{start}_{end}.csv'), dtype={'om':'string','stf':'string'})
    county_source = pd.read_csv(input_path(f'census_population/county_context_{start}_{end}.csv'), dtype={'county_fips':'string'})
    tornadoes, counties = tornado_table(spc_source), county_table(county_source)
    if not tornadoes.year.between(start,end).all() or not counties.year.between(start,end).all():
        raise ValueError('Source rows fall outside the selected years')
    ncei_counts, dat_counts = {}, {}
    for year in range(start, end+1):
        for table in ['details', 'fatalities', 'locations']:
            path = input_path(f'ncei_storm_events/tornado/{year}_{table}.csv')
            ncei_counts[year,table] = len(pd.read_csv(path, usecols=['EVENT_ID']))
        for layer in ['points', 'lines', 'polygons']:
            index = json.loads(input_path(f'nws_dat/{year}/{layer}/index.json').read_text())
            if index['features'] != sum(batch['features'] for batch in index['batches']):
                raise ValueError('DAT index counts do not reconcile')
            dat_counts[year,layer] = index['features']
    annual = annual_table(tornadoes, counties, start, end, ncei_counts, dat_counts)
    assert annual.spc_tracks.sum() == len(tornadoes)
    assert annual.filter(regex=r'^spc_ef[0-5]$').to_numpy().sum() + annual.spc_ef_unknown.sum() == len(tornadoes)
    assert annual.census_county_rows.sum() == len(counties)
    output.mkdir(parents=True, exist_ok=True)
    tables = {'tornadoes.parquet':tornadoes,'county_context.parquet':counties,'annual_summary.csv':annual}
    with TemporaryDirectory(prefix='.analysis-',dir=output.parent) as temp:
        temp = Path(temp)
        entries = []
        for filename, frame in tables.items():
            target = temp/filename
            if target.suffix == '.parquet':
                frame.to_parquet(target, engine='pyarrow', compression='zstd', index=False)
                pd.testing.assert_frame_equal(frame,pd.read_parquet(target,engine='pyarrow'))
            else:
                frame.to_csv(target,index=False,float_format='%.8f')
                restored = pd.read_csv(target).astype(frame.dtypes.to_dict())
                pd.testing.assert_frame_equal(frame,restored,check_exact=False,rtol=1e-7,atol=1e-8)
            entries.append({'path':filename,'rows':len(frame),'bytes':target.stat().st_size,'sha256':sha(target),
                            'columns':[{'name':c,'dtype':str(frame[c].dtype)} for c in frame]})
        manifest = {'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),
                    'start_year':start,'end_year':end,'inputs':inputs,'files':entries,
                    'software':{'pandas':pd.__version__,'pyarrow':pyarrow.__version__},
                    'spc_field_mapping':{old:{'name':new,'description':note} for old,(new,_,note) in SPC_FIELDS.items()},
                    'verification':{'status':'passed','spc_rows_preserved':len(tornadoes),'unknown_ef_preserved':int(tornadoes.ef_rating.isna().sum()),
                                    'county_rows_preserved':len(counties),'annual_rows':len(annual),'parquet_roundtrip':'passed'}}
        (temp/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        for filename in [*tables,'manifest.json']:
            os.replace(temp/filename,output/filename)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    from scripts.verify_downloads import verify
    checked=verify(args.data_dir,2010,2025)
    if checked['status'] != 'passed':
        raise ValueError(f'Source collection verification failed: {checked["errors"]}')
    result=build_analysis(args.data_dir,args.output)
    print(json.dumps(result['verification'],indent=2))

if __name__ == '__main__':main()
