"""Build consolidated analysis tables from the retained source collection."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import sys
from tempfile import TemporaryDirectory

import geopandas as gpd
from geopandas.testing import assert_geodataframe_equal
from shapely.geometry import shape
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


NCEI_TABLES = {'details': 'storm_events.parquet', 'fatalities': 'storm_fatalities.parquet',
               'locations': 'storm_locations.parquet'}
NCEI_RENAMES = {
    'STATE': 'state_name', 'CZ_TYPE': 'county_zone_type', 'CZ_FIPS': 'county_zone_code',
    'CZ_NAME': 'county_zone_name', 'CZ_TIMEZONE': 'source_timezone',
    'TOR_F_SCALE': 'source_ef_rating', 'TOR_LENGTH': 'path_length_miles',
    'TOR_WIDTH': 'path_width_yards', 'BEGIN_LAT': 'begin_latitude', 'BEGIN_LON': 'begin_longitude',
    'END_LAT': 'end_latitude', 'END_LON': 'end_longitude',
    'BEGIN_RANGE': 'begin_range_miles', 'END_RANGE': 'end_range_miles', 'RANGE': 'range_miles',
}
NCEI_INTS = {'BEGIN_YEARMONTH','BEGIN_DAY','END_YEARMONTH','END_DAY','YEAR',
             'INJURIES_DIRECT','INJURIES_INDIRECT','DEATHS_DIRECT','DEATHS_INDIRECT',
             'FAT_YEARMONTH','FAT_DAY','FATALITY_AGE','YEARMONTH','LOCATION_INDEX'}
NCEI_FLOATS = {'MAGNITUDE','TOR_LENGTH','TOR_WIDTH','BEGIN_RANGE','END_RANGE','BEGIN_LAT',
               'BEGIN_LON','END_LAT','END_LON','RANGE','LATITUDE','LONGITUDE'}
DAT_RENAMES = {'objectid':'object_id','event_id':'survey_event_id','globalid':'global_id',
               'path_guid':'path_guid','efscale':'source_ef_rating','wfo':'office',
               'st_length(shape)':'source_geometry_length','st_area(shape)':'source_geometry_area',
               'st_perimeter(shape)':'source_geometry_perimeter'}
DAT_DATES = {'stormdate':'storm','surveydate':'survey','starttime':'start','endtime':'end',
             'edit_time':'edit','created_date':'created','last_edited_date':'last_edited'}


def nullable_ef(values):
    """Only explicit EF0–EF5 labels qualify; preserve raw labels separately."""
    known = values.str.fullmatch(r'EF[0-5]', na=False)
    return values.where(known).str.slice(2).astype('Int8')


def ncei_table(parts):
    """Concatenate all source columns; unknown textual tokens stay literal."""
    result = pd.concat(parts, ignore_index=True)
    mapping = {name: NCEI_RENAMES.get(name, name.lower()) for name in result.columns}
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('NCEI column-name collision')
    for name in result:
        if name in NCEI_INTS:
            result[name] = pd.to_numeric(result[name], errors='raise').astype('Int64')
        elif name in NCEI_FLOATS:
            result[name] = pd.to_numeric(result[name], errors='raise').astype('Float64')
        elif name not in ['source_year','source_row']:
            result[name] = result[name].astype('string')
    result = result.rename(columns=mapping)
    for name, width in [('state_fips',2),('county_zone_code',3)]:
        if name in result:
            result[name] = result[name].str.zfill(width)
            if not result[name].dropna().str.fullmatch(fr'\d{{{width}}}').all():
                raise ValueError(f'Invalid NCEI {name}')
    for old, new, fmt in [('begin_date_time','begin_datetime_local','%d-%b-%y %H:%M:%S'),
                          ('end_date_time','end_datetime_local','%d-%b-%y %H:%M:%S'),
                          ('fatality_date','fatality_datetime_local','%m/%d/%Y %H:%M:%S')]:
        if old in result:
            result[new] = pd.to_datetime(result[old], format=fmt, errors='raise')
    if 'source_ef_rating' in result:
        result['ef_rating'] = nullable_ef(result.source_ef_rating)
    if result.event_id.isna().any():
        raise ValueError('NCEI event IDs must be present')
    return result, mapping


def survey_table(features, schema):
    """Use saved ArcGIS types, preserving every feature and unnormalized sentinel."""
    props = [f['properties'] for f in features]
    definitions = {f['name']:f for f in schema['fields'] if f['type'] != 'esriFieldTypeGeometry'}
    added = {'source_file','source_row','source_year','source_feature_id'}
    unexpected = set().union(*(set(p) for p in props)) - set(definitions) - added
    if unexpected:
        raise ValueError(f'DAT properties absent from saved schema: {sorted(unexpected)}')
    frame = pd.DataFrame(props, columns=[*definitions,*sorted(added)])
    mapping = {}
    for name, definition in definitions.items():
        kind = definition['type']
        new = DAT_RENAMES.get(name,name)
        if kind == 'esriFieldTypeDate':
            if name not in DAT_DATES:
                raise ValueError(f'Unmapped DAT date field: {name}')
            new = DAT_DATES[name] + '_epoch_ms'
            frame[name] = pd.to_numeric(frame[name],errors='raise').astype('Int64')
            frame[DAT_DATES[name]+'_datetime_utc'] = pd.to_datetime(frame[name],unit='ms',utc=True,errors='raise')
        elif kind in ['esriFieldTypeOID','esriFieldTypeString','esriFieldTypeGUID','esriFieldTypeGlobalID']:
            frame[name] = frame[name].astype('string')
        elif kind in ['esriFieldTypeSmallInteger','esriFieldTypeInteger']:
            frame[name] = pd.to_numeric(frame[name],errors='raise').astype('Int64')
        elif kind in ['esriFieldTypeDouble','esriFieldTypeSingle']:
            frame[name] = pd.to_numeric(frame[name],errors='raise').astype('Float64')
        else:
            raise ValueError(f'Unsupported DAT field type: {kind}')
        mapping[name] = new
    frame = frame.rename(columns=mapping)
    for name in ['source_file','source_feature_id']:
        frame[name] = frame[name].astype('string')
    for name in ['source_year','source_row']:
        frame[name] = frame[name].astype('Int64')
    if frame.object_id.isna().any() or frame.object_id.duplicated().any():
        raise ValueError('DAT object IDs must be present and unique within a layer')
    frame['ef_rating'] = nullable_ef(frame.source_ef_rating)
    geometry = gpd.GeoSeries([shape(f['geometry']) if f.get('geometry') is not None else None
                             for f in features],crs='OGC:CRS84')
    frame = gpd.GeoDataFrame(frame,geometry=geometry)
    if not frame.storm_datetime_utc.dt.year.eq(frame.source_year).all():
        raise ValueError('DAT dates disagree with source-year partitions')
    return frame, mapping


def boundary_table(collection, source_file):
    features = collection['features']
    frame = pd.DataFrame([f['properties'] for f in features])
    mapping = {'STATEFP':'state_fips','COUNTYFP':'county_code','COUNTYNS':'county_gnis_id',
               'AFFGEOID':'census_geo_id','GEOID':'county_fips','NAME':'county_name',
               'NAMELSAD':'county_legal_name','STUSPS':'state','STATE_NAME':'state_name',
               'LSAD':'legal_area_code','ALAND':'land_area_m2','AWATER':'water_area_m2'}
    if set(frame) != set(mapping):
        raise ValueError('County boundary fields differ from the expected saved schema')
    for name in frame:
        frame[name] = frame[name].astype('Int64' if name in ['ALAND','AWATER'] else 'string')
    frame = frame.rename(columns=mapping)
    if not frame.county_fips.str.fullmatch(r'\d{5}').fillna(False).all() or frame.county_fips.duplicated().any():
        raise ValueError('Boundary county FIPS must be unique five-character strings')
    frame['boundary_year'] = pd.Series(2020,index=frame.index,dtype='Int16')
    frame['source_file'] = pd.Series(source_file,index=frame.index,dtype='string')
    frame['source_row'] = pd.Series(range(1,len(frame)+1),dtype='Int64')
    return gpd.GeoDataFrame(frame,geometry=gpd.GeoSeries(
        [shape(f['geometry']) if f.get('geometry') is not None else None for f in features],crs='OGC:CRS84')), mapping


def consolidate_sources(input_path, start, end):
    tables, mappings, ncei_counts, dat_counts = {}, {}, {}, {}
    for table, filename in NCEI_TABLES.items():
        parts = []
        for year in range(start,end+1):
            relative = f'ncei_storm_events/tornado/{year}_{table}.csv'
            frame = pd.read_csv(input_path(relative),dtype='string',keep_default_na=False,na_values=[''])
            frame['source_file'] = relative
            frame['source_year'] = pd.Series(year,index=frame.index,dtype='Int64')
            frame['source_row'] = pd.Series(range(1,len(frame)+1),dtype='Int64')
            ncei_counts[year,table] = len(frame)
            parts.append(frame)
        tables[filename], mappings[filename] = ncei_table(parts)
    events = tables['storm_events.parquet']
    if events.event_id.duplicated().any():
        raise ValueError('NCEI event IDs are not unique; review before joining related tables')
    if 'year' in events and not events.year.eq(events.source_year).all():
        raise ValueError('NCEI event years disagree with source files')
    if not events.event_type.eq('Tornado').all():
        raise ValueError('Non-tornado row in NCEI tornado details')
    parents = set(zip(events.source_year,events.event_id))
    for table in ['fatalities','locations']:
        child = tables[NCEI_TABLES[table]]
        if not set(zip(child.source_year,child.event_id)) <= parents:
            raise ValueError(f'Orphan NCEI {table} event reference')
    for layer in ['points','lines','polygons']:
        definitions = json.loads(input_path(f'nws_dat/{layer}_schema.json').read_text())
        features = []
        for year in range(start,end+1):
            relative = f'nws_dat/{year}/{layer}/index.json'
            index = json.loads(input_path(relative).read_text())
            count = 0
            for batch in index['batches']:
                name = (Path(relative).parent/batch['file']).as_posix()
                path = input_path(name)
                if sha(path) != batch['sha256']:
                    raise ValueError(f'DAT batch checksum mismatch: {name}')
                items = json.loads(path.read_text())['features']
                if len(items) != batch['features']:
                    raise ValueError(f'DAT batch count mismatch: {name}')
                for row, f in enumerate(items,1):
                    f['properties'].update(source_file=name,source_year=year,source_row=row,
                                           source_feature_id=str(f['id']) if f.get('id') is not None else None)
                features.extend(items)
                count += len(items)
            if count != index['features']:
                raise ValueError(f'DAT index counts do not reconcile: {relative}')
            dat_counts[year,layer] = count
        filename = f'survey_{layer}.parquet'
        tables[filename], mappings[filename] = survey_table(features,definitions)
    name = 'census_boundaries/counties_2020_5m.geojson'
    tables['county_boundaries.parquet'], mappings['county_boundaries.parquet'] = boundary_table(
        json.loads(input_path(name).read_text()), name)
    return tables,mappings,ncei_counts,dat_counts


def build_analysis(data, output=None, start=2010, end=2025):
    """Generate files and reconciliation metadata; caller verifies source collection."""
    data = Path(data).resolve()
    output = Path(output).resolve() if output else data/'analysis'
    if output == data or any(output.is_relative_to(data/x) for x in ['spc','ncei_storm_events','nws_dat','census_population','census_boundaries']):
        raise ValueError('Analysis output must not overwrite a source directory')
    inputs = []
    def input_path(relative):
        path = (data/relative).resolve()
        if not path.is_relative_to(data):
            raise ValueError(f'Input escapes dataset: {relative}')
        inputs.append({'path':relative, 'sha256':sha(path)})
        return path
    spc_source = pd.read_csv(input_path(f'spc/tornadoes_{start}_{end}.csv'), dtype={'om':'string','stf':'string'})
    county_source = pd.read_csv(input_path(f'census_population/county_context_{start}_{end}.csv'), dtype={'county_fips':'string'})
    tornadoes, counties = tornado_table(spc_source), county_table(county_source)
    if not tornadoes.year.between(start,end).all() or not counties.year.between(start,end).all():
        raise ValueError('Source rows fall outside the selected years')
    consolidated, mappings, ncei_counts, dat_counts = consolidate_sources(input_path,start,end)
    annual = annual_table(tornadoes, counties, start, end, ncei_counts, dat_counts)
    assert annual.spc_tracks.sum() == len(tornadoes)
    assert annual.filter(regex=r'^spc_ef[0-5]$').to_numpy().sum() + annual.spc_ef_unknown.sum() == len(tornadoes)
    assert annual.census_county_rows.sum() == len(counties)
    output.mkdir(parents=True, exist_ok=True)
    tables = {'tornadoes.parquet':tornadoes,'county_context.parquet':counties,'annual_summary.csv':annual,**consolidated}
    with TemporaryDirectory(prefix='.analysis-',dir=output.parent) as temp:
        temp = Path(temp)
        entries = []
        for filename, frame in tables.items():
            target = temp/filename
            if isinstance(frame,gpd.GeoDataFrame):
                frame.to_parquet(target,compression='zstd',index=False,schema_version='1.0.0')
                restored = gpd.read_parquet(target)
                assert_geodataframe_equal(frame,restored,check_less_precise=False)
                pd.testing.assert_series_equal(frame.geometry.to_wkb(),restored.geometry.to_wkb())
            elif target.suffix == '.parquet':
                frame.to_parquet(target, engine='pyarrow', compression='zstd', index=False)
                pd.testing.assert_frame_equal(frame,pd.read_parquet(target,engine='pyarrow'))
            else:
                frame.to_csv(target,index=False,float_format='%.8f')
                restored = pd.read_csv(target).astype(frame.dtypes.to_dict())
                pd.testing.assert_frame_equal(frame,restored,check_exact=False,rtol=1e-7,atol=1e-8)
            entries.append({'path':filename,'rows':len(frame),'bytes':target.stat().st_size,'sha256':sha(target),
                            'columns':[{'name':c,'dtype':str(frame[c].dtype)} for c in frame]})
            if isinstance(frame,gpd.GeoDataFrame):
                entries[-1]['geometry'] = {'encoding':'WKB','geoparquet_version':'1.0.0','crs':frame.crs.to_string(),
                    'types':sorted(frame.geometry.geom_type.dropna().unique().tolist()),
                    'null_geometries':int(frame.geometry.isna().sum()),'empty_geometries':int(frame.geometry.is_empty.sum()),
                    'invalid_geometries':int((frame.geometry.notna() & ~frame.geometry.is_valid).sum())}
        manifest = {'schema_version':2,'generated_at':datetime.now(timezone.utc).isoformat(),
                    'start_year':start,'end_year':end,'inputs':inputs,'files':entries,
                    'software':{'pandas':pd.__version__,'pyarrow':pyarrow.__version__,'geopandas':gpd.__version__},
                    'source_field_mappings':mappings,
                    'spc_field_mapping':{old:{'name':new,'description':note} for old,(new,_,note) in SPC_FIELDS.items()},
                    'verification':{'status':'passed','spc_rows_preserved':len(tornadoes),'unknown_ef_preserved':int(tornadoes.ef_rating.isna().sum()),
                                    'county_rows_preserved':len(counties),'annual_rows':len(annual),'parquet_roundtrip':'passed',
                                    'consolidated_rows':{name:len(frame) for name,frame in consolidated.items()},
                                    'ncei_related_event_ids':'passed','dat_batch_counts_hashes_and_layer_ids':'passed',
                                    'geometry_wkb_roundtrip':'passed'}}
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
