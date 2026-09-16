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


def annual_table(tornadoes, counties, start, end, ncei_counts, footprint_counts):
    rows = []
    for year in range(start, end + 1):
        tracks = tornadoes[tornadoes.year.eq(year)]
        county = counties[counties.year.eq(year)]
        row = {'year': year, 'spc_tracks': len(tracks), 'spc_ef_unknown': int(tracks.ef_rating.isna().sum())}
        row.update({f'spc_ef{i}': int(tracks.ef_rating.eq(i).sum()) for i in range(6)})
        row['spc_known_ef_fraction'] = tracks.ef_rating.notna().mean() if len(tracks) else float('nan')
        # Missing partitions raise rather than becoming fabricated zero counts.
        row.update({f'ncei_tornado_{table}_rows': ncei_counts[year, table] for table in ['details', 'fatalities', 'locations']})
        row.update({f'efc_{source.lower()}_footprints': footprint_counts[year, source] for source in ['DAT', 'SED']})
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
# Preserve source column names and sentinels; add explicit analysis helpers.
EFC_TYPES = {}
for names, dtype in [
    ('objectid objectid_line', 'string'),
    ('event_id CZ_TIMEZONE efscale qc globalid cropdamage propdamage created_user last_edited_user comments wfo path_guid source edit_user max_efscale event_id_line efscale_line qc_line globalid_line cropdamage_line propdamage_line edit_user_line created_user_line last_edited_user_line comments_line wfo_line source_line convective_day', 'string'),
    ('stormdate starttime endtime created_date last_edited_date edit_time stormdate_line starttime_line endtime_line edit_time_line created_date_line last_edited_date_line', 'string'),
    ('injuries fatalities efnum injuries_line fatalities_line efnum_line', 'Int64'),
    ('startlat startlon endlat endlon length width maxwind Shape__Length Shape__Area area_acres startlat_line startlon_line endlat_line endlon_line length_line width_line maxwind_line Shape__Length_line', 'Float64'),
    ('parents children', 'object'),
]:
    EFC_TYPES.update({name:dtype for name in names.split()})
EFC_DATES = ['stormdate','starttime','endtime','created_date','last_edited_date','edit_time',
             'stormdate_line','starttime_line','endtime_line','edit_time_line','created_date_line','last_edited_date_line']


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


def footprint_table(features):
    """Retain each EFC damage region, raw properties, relationships, and geometry."""
    props = [f['properties'] for f in features]
    added = {'source_file','source_year','source_row'}
    unexpected = set().union(*(set(p) for p in props)) - set(EFC_TYPES) - added
    if unexpected:
        raise ValueError(f'EFC properties absent from supported schema: {sorted(unexpected)}')
    frame = pd.DataFrame(props,columns=[*EFC_TYPES,*sorted(added)])
    for name,dtype in EFC_TYPES.items():
        if name in ('parents','children'):
            frame[name] = frame[name].map(lambda x: x if isinstance(x,list) else [])
        else:
            frame[name] = frame[name].astype(dtype)
    for name in ['source_year','source_row']:
        frame[name] = frame[name].astype('Int64')
    frame['source_file'] = frame.source_file.astype('string')
    if (frame.objectid.isna().any() or not frame.source.isin(['DAT','SED']).all()
            or frame.duplicated(['source_year','source','objectid']).any()):
        raise ValueError('EFC source/year/object IDs must be present and unique')
    frame.insert(0,'footprint_id','efc:'+frame.source_year.astype('string')+':'+frame.source+':'+frame.objectid)
    frame['ef_rating'] = nullable_ef(frame.efscale)
    frame['max_ef_rating'] = nullable_ef(frame.max_efscale)
    frame['width_is_placeholder'] = frame.width.eq(0.99).fillna(False).astype('boolean')
    frame['path_width_yards'] = frame.width.where(frame.width.gt(0) & ~frame.width_is_placeholder)
    for name in EFC_DATES:
        values = frame[name].replace({'':pd.NA,'-99':pd.NA,'-99.0':pd.NA})
        # The live catalog mixes ISO timestamps with inherited ArcGIS epoch-ms
        # edit times. Preserve original text and parse only this explicit format.
        epoch = values.str.fullmatch(r'\d{12,13}(?:\.0)?',na=False)
        parsed = pd.to_datetime(values.mask(epoch),utc=True,format='ISO8601',errors='raise').astype('datetime64[ns, UTC]')
        parsed.loc[epoch] = pd.to_datetime(pd.to_numeric(values[epoch]),unit='ms',utc=True)
        frame[name+'_datetime_utc'] = parsed
    # Identifier fields and record references are not SPC or NCEI event keys.
    geometry = gpd.GeoSeries([shape(f['geometry']) if f.get('geometry') is not None else None
                             for f in features],crs='OGC:CRS84')
    return gpd.GeoDataFrame(frame,geometry=geometry), {name:name for name in EFC_TYPES}


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
    tables, mappings, ncei_counts, footprint_counts = {}, {}, {}, {}
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
    from footprint_data import validate_collection
    features = []
    for year in range(start,end+1):
        name = f'event_footprints/{year}_tornado_footprint.geojson'
        path = input_path(name)
        metadata = json.loads(input_path(name+'.metadata.json').read_text())
        if sha(path) != metadata['sha256']:
            raise ValueError(f'EFC source checksum mismatch: {name}')
        value = json.loads(path.read_text())
        checked = validate_collection(value,year)
        for source,count in checked['source_counts'].items():
            footprint_counts[year,source] = count
        for row,feature in enumerate(value['features'],1):
            feature['properties'].update(source_file=name,source_year=year,source_row=row)
        features.extend(value['features'])
    name = 'tornado_footprints.parquet'
    tables[name], mappings[name] = footprint_table(features)
    name = 'census_boundaries/counties_2020_5m.geojson'
    tables['county_boundaries.parquet'], mappings['county_boundaries.parquet'] = boundary_table(
        json.loads(input_path(name).read_text()), name)
    return tables,mappings,ncei_counts,footprint_counts


def build_analysis(data, output=None, start=2010, end=2025):
    """Generate files and reconciliation metadata; caller verifies source collection."""
    data = Path(data).resolve()
    output = Path(output).resolve() if output else data/'analysis'
    if output == data or any(output.is_relative_to(data/x) for x in ['spc','ncei_storm_events','event_footprints','census_population','census_boundaries']):
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
    consolidated, mappings, ncei_counts, footprint_counts = consolidate_sources(input_path,start,end)
    annual = annual_table(tornadoes, counties, start, end, ncei_counts, footprint_counts)
    assert annual.spc_tracks.sum() == len(tornadoes)
    assert annual.filter(regex=r'^spc_ef[0-5]$').to_numpy().sum() + annual.spc_ef_unknown.sum() == len(tornadoes)
    assert annual.census_county_rows.sum() == len(counties)
    output.mkdir(parents=True, exist_ok=True)
    tables = {'tornadoes.parquet':tornadoes,'county_context.parquet':counties,'annual_summary.csv':annual,**consolidated}
    from scripts.build_crosswalk import enrich_analysis
    tables, linkage = enrich_analysis(tables)
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
                    'invalid_geometries':int((~frame.geometry.isna() & ~frame.geometry.is_valid).sum())}
        manifest = {'schema_version':4,'linkage':linkage,'generated_at':datetime.now(timezone.utc).isoformat(),
                    'start_year':start,'end_year':end,'inputs':inputs,'files':entries,
                    'software':{'pandas':pd.__version__,'pyarrow':pyarrow.__version__,'geopandas':gpd.__version__},
                    'source_field_mappings':mappings,
                    'spc_field_mapping':{old:{'name':new,'description':note} for old,(new,_,note) in SPC_FIELDS.items()},
                    'verification':{'status':'passed','spc_rows_preserved':len(tornadoes),'unknown_ef_preserved':int(tornadoes.ef_rating.isna().sum()),
                                    'county_rows_preserved':len(counties),'annual_rows':len(annual),'parquet_roundtrip':'passed',
                                    'consolidated_rows':{name:len(frame) for name,frame in consolidated.items()},
                                    'ncei_related_event_ids':'passed','efc_annual_counts_hashes_and_footprint_ids':'passed',
                                    'geometry_wkb_roundtrip':'passed'}}
        (temp/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        for filename in [*tables,'manifest.json']:
            os.replace(temp/filename,output/filename)
    # Remove only the three superseded generated tables after a successful build.
    for layer in ['points','lines','polygons']:
        (output/f'survey_{layer}.parquet').unlink(missing_ok=True)
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
