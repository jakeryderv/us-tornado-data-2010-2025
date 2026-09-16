"""Offline event views with explicit onset and retrospective feature contracts."""
from .layout import table_path
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from scripts.build_crosswalk import Rules, prepare_spc
from .common import atomic_json, digest, now, stable_id, utc
from .pipeline import COLLECTORS, OPTIONAL_TABLE_SOURCES, ROOT, TABLES, input_hashes, write_table
from .era5 import environmental_summary

RADAR_FEATURES = {
    'radar_tvs_count': ('nx3tvs', None),
    'radar_mda_count': ('nx3mda', None),
    'radar_structure_count': ('nx3structure', None),
    'radar_max_shear_per_s': ('nx3tvs', 'max_shear_per_s'),
    'radar_max_velocity_difference_knots': ('nx3tvs', 'velocity_difference_knots'),
    'radar_max_rotation_velocity_knots': ('nx3mda', 'rotation_velocity_knots'),
    'radar_max_reflectivity_dbz': ('nx3structure', 'max_reflectivity_dbz'),
    'radar_max_vil_kg_m2': ('nx3structure', 'vil_kg_m2'),
}
EXPOSURE_FIELDS = ['land_developed_fraction','land_forest_fraction','land_cropland_fraction',
                   'impervious_mean_percent','nlcd_valid_fraction','exposure_population_estimate',
                   'exposure_housing_estimate','exposure_mobile_home_estimate','exposure_tract_count',
                   'exposure_tract_coverage_fraction']


def grouped(frame, key='tornado_id'):
    return dict(tuple(frame.groupby(key))) if key in frame and len(frame) else {}


def split_groups(backbone, tables):
    """Keep shared backbone groups, radar detections and warnings together."""
    parent = {v:v for v in backbone.tornado_id}
    def root(v):
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v
    groups = []
    if 'suggested_split_group' in backbone:
        groups.extend(backbone.dropna(subset=['suggested_split_group']).groupby('suggested_split_group').tornado_id.agg(list))
    for table,key in [('tornado_radar','record_id'),('tornado_warnings','warning_id')]:
        if len(tables[table]):groups.extend(tables[table].groupby(key).tornado_id.agg(list))
    for group in groups:
        for v in group[1:]:parent[root(v)] = root(group[0])
    members = {}
    for v in parent:members.setdefault(root(v), []).append(v)
    return {v:stable_id('ml-group',sorted(group)) for group in members.values() for v in group}


def radar_summary(rows, cutoff, *, complete, onset=True):
    result = {k:None for k in RADAR_FEATURES}
    if not complete or pd.isna(cutoff):
        return result
    if len(rows):
        eligible = rows.loc[utc(rows['available_at' if onset else 'observed_at']) <= cutoff]
    else:
        eligible = rows
    for name,(product,column) in RADAR_FEATURES.items():
        values = eligible.loc[eligible['product'].eq(product)] if len(eligible) else eligible
        if column is None:
            result[name] = len(values)
        elif len(values):
            value = pd.to_numeric(values[column],errors='coerce').max()
            result[name] = None if pd.isna(value) else float(value)
    return result


def warning_summary(rows, cutoff, *, complete):
    result = dict(warning_active_tornado_count=None, warning_active_severe_count=None,
                  warning_tornado_lead_minutes=None)
    if not complete or pd.isna(cutoff):return result
    result.update(warning_active_tornado_count=0, warning_active_severe_count=0)
    if not len(rows):return result
    before = rows.loc[utc(rows.issued_at)<=cutoff].copy()
    active=[]
    for _,updates in before.groupby('warning_id'):
        latest = updates.loc[utc(updates.issued_at)==utc(updates.issued_at).max()]
        if latest.action.isna().any() or latest.known_expiry_at.isna().any():
            return {k:None for k in result}
        # Retain all geometry pieces at the latest product time. A later
        # polygon moving away or a cancellation supersedes an earlier polygon.
        candidates=latest.loc[latest.covers_start & ~latest.action.isin(['CAN','EXP']) &
                              (utc(latest.known_expiry_at)>cutoff)]
        if len(candidates):active.append(candidates.iloc[0])
    if active:
        frame=pd.DataFrame(active)
        result['warning_active_tornado_count']=int(frame.phenomenon.eq('TO').sum())
        result['warning_active_severe_count']=int(frame.phenomenon.eq('SV').sum())
        tor=frame.loc[frame.phenomenon.eq('TO')]
        if len(tor) and utc(tor.original_issue_at).notna().all():
            result['warning_tornado_lead_minutes']=(cutoff-utc(tor.original_issue_at).min()).total_seconds()/60
    return result


def exposure_summary(nlcd, tracts, acs, *, nlcd_complete, census_complete):
    result={k:None for k in EXPOSURE_FIELDS}
    if nlcd_complete and len(nlcd):
        land=nlcd.loc[nlcd['product'].eq('land_cover')]
        impervious=nlcd.loc[nlcd['product'].eq('impervious')]
        if len(land)==1:
            row=land.iloc[0]; n=row.valid_pixel_count
            if row.pixel_count>0:result['nlcd_valid_fraction']=n/row.pixel_count
            if n>0:
                for key,codes in [('developed',[21,22,23,24]),('forest',[41,42,43]),('cropland',[82])]:
                    result['land_'+key+'_fraction']=sum(row[f'class_{c}_pixels'] for c in codes)/n
        if len(impervious)==1:
            result['impervious_mean_percent']=impervious.iloc[0].mean_impervious_percent
    if census_complete and len(tracts):
        # Weight each intersecting tract once; join exact ACS/vintage keys.
        pairs=tracts.drop_duplicates('tract_id')
        context=pairs.merge(acs,left_on='acs_id',right_on='record_id',how='left',validate='many_to_one')
        result['exposure_tract_count']=len(context)
        result['exposure_tract_coverage_fraction']=min(float(context.area_fraction.sum()),1.0)
        for source,name in [('population','population'),('housing_units','housing'),('mobile_home_units','mobile_home')]:
            if source in context and context[source].notna().all() and result['exposure_tract_coverage_fraction']>=0.99:
                result[f'exposure_{name}_estimate']=float((context[source]*context.intersection_fraction).sum())
    return result


def make_views(backbone, tables, config):
    coverage=grouped(tables['source_coverage'])
    empty=pd.DataFrame()
    radar=tables['tornado_radar']
    if len(radar):radar=radar.merge(tables['radar_detections'],on='record_id',validate='many_to_one')
    warnings=tables['tornado_warnings']
    if len(warnings):warnings=warnings.merge(tables['warning_updates'],on=['record_id','warning_id'],validate='many_to_one')
    include_era5='era5_samples' in tables
    include_census='acs_tracts' in tables and 'tornado_tracts' in tables
    optional_sources=set(OPTIONAL_TABLE_SOURCES.values())
    included_optional={source for name,source in OPTIONAL_TABLE_SOURCES.items() if name in tables}
    radar_groups,warning_groups,env_groups=grouped(radar),grouped(warnings),grouped(tables.get('era5_samples',empty))
    land_groups,tract_groups=grouped(tables['nlcd_samples']),grouped(tables.get('tornado_tracts',empty))
    areas=grouped(tables['event_areas'])
    split_ids=split_groups(backbone,tables)
    onset_rows,retro_rows=[],[]
    prepared=prepare_spc(backbone,Rules())
    for source,time_record in zip(backbone.to_dict('records'),prepared):
        event_id=source['tornado_id'];cutoff=time_record['start']
        statuses={r.source:r.status for r in coverage.get(event_id,empty).itertuples()}
        complete=lambda name:statuses.get(name)=='complete'
        row=dict(tornado_id=event_id, target_ef_rating=source['ef_rating'],target_known=source['ef_rating_known'],
                 onset_utc=cutoff, prediction_cutoff_utc=cutoff,
                 start_longitude=source['start_longitude'],start_latitude=source['start_latitude'],
                 year=source['year'], month=None if pd.isna(cutoff) else cutoff.month,
                 hour_utc=None if pd.isna(cutoff) else cutoff.hour,
                 suggested_split_group=source.get('suggested_split_group'),ml_split_group=split_ids[event_id])
        for name in COLLECTORS:
            if name not in optional_sources or name in included_optional:
                row[name+'_source_status']=statuses.get(name,'not_requested')
        row.update(radar_summary(radar_groups.get(event_id,empty),cutoff,complete=complete('radar')))
        row.update(warning_summary(warning_groups.get(event_id,empty),cutoff,complete=complete('warnings')))
        row['radar_availability_assumed']=complete('radar')
        row['radar_no_detection_is_not_no_radar_coverage']=True
        row['onset_location_from_final_catalog']=True
        row['feature_missing_count']=sum(pd.isna(v) for k,v in row.items() if k.startswith(('radar_','warning_')))
        onset_rows.append(row)
        retrospective=dict(row)
        if include_era5:
            retrospective.update({'post_era5_'+k:v for k,v in environmental_summary(
                env_groups.get(event_id,empty) if complete('era5') else empty,cutoff).items()})
        retrospective.update(post_end_utc=time_record['end'],post_end_longitude=source['end_longitude'],
            post_end_latitude=source['end_latitude'],post_path_length_miles=source['path_length_miles'],
            post_path_width_yards=source['path_width_yards'],post_injuries=source['injuries'],post_fatalities=source['fatalities'])
        retrospective['post_duration_minutes']=((time_record['end']-cutoff).total_seconds()/60
            if pd.notna(time_record['end']) and pd.notna(cutoff) and time_record['end']>=cutoff else None)
        end=cutoff+pd.Timedelta(minutes=config['radar_after_minutes']) if pd.notna(cutoff) else cutoff
        retrospective.update({'post_'+k:v for k,v in radar_summary(radar_groups.get(event_id,empty),end,
                              complete=complete('radar'),onset=False).items()})
        retrospective.update({'post_'+k:v for k,v in exposure_summary(land_groups.get(event_id,empty),
            tract_groups.get(event_id,empty),tables.get('acs_tracts',empty),nlcd_complete=complete('nlcd'),
            census_complete=complete('acs') and complete('tiger')).items()
            if include_census or not k.startswith('exposure_')})
        area=areas.get(event_id)
        retrospective['post_area_id']=area.iloc[0].area_id if area is not None else None
        retrospective['post_area_method']=area.iloc[0].area_method if area is not None else None
        retro_rows.append(retrospective)
    return pd.DataFrame(onset_rows),pd.DataFrame(retro_rows)


def dictionary(onset, retrospective, config):
    excluded={'tornado_id','target_ef_rating','target_known','onset_utc','prediction_cutoff_utc',
              'suggested_split_group','ml_split_group','year','feature_missing_count',
              'era5_source_status'}
    onset_features=[c for c in onset if c not in excluded and not any(x in c for x in
                    ['source_status','assumed','no_detection','from_final_catalog'])]
    columns={}
    for c in retrospective:
        role=('target' if c=='target_ef_rating' else 'retrospective_candidate' if c.startswith('post_')
              else 'onset_predictor_candidate' if c in onset_features else 'metadata')
        if c in {'post_injuries','post_fatalities','post_area_id','post_area_method','post_end_utc','post_era5_grid_distance_km','post_era5_profile_status',
                 'post_era5_surface_geopotential_m2_s2'}:
            role='retrospective_metadata'
        columns[c]=dict(role=role,available_by_onset=c in onset_features,
                        note='Reported start location is a hindsight event anchor; operational latency is assumed for radar; ERA5 is retrospective only.' if c in onset_features else
                             'Never include post_ fields in an onset forecast.' if c.startswith('post_') else 'Exclude from predictors unless separately justified.')
    return dict(schema_version=1,cutoff='reported_tornado_onset',config=config,columns=columns,
                onset_predictor_columns=onset_features,
                retrospective_predictor_columns=onset_features+[c for c,v in columns.items() if v['role']=='retrospective_candidate'],
                target='target_ef_rating',id='tornado_id',group='ml_split_group',
                limitations=['Conditional on a recorded tornado, not a non-tornadic storm classifier.',
                 'Radar association is proximity, not verified parent-storm identity; detections can be shared between events.',
                 'Radar availability uses an assumed latency; ERA5 is retrospective reanalysis and never an onset predictor.',
                 'Post-event dimensions and exposure can encode EF assessment; retrospective candidates are not a forecast feature set.',
                 'No imputation, scaling or train/test split is fitted on the complete dataset.']+
                 (['ACS weighted counts assume uniform distribution within tracts; MOEs remain in source tables, no propagated uncertainty is claimed.']
                  if 'post_exposure_tract_count' in retrospective else []))


def validate_sources(tables, manifest, backbone):
    """Validate keys without equating an absent source record with a zero."""
    event_ids = set(backbone.tornado_id)
    selected = set(manifest['selected_event_ids'])
    if len(selected) != manifest['event_count'] or not selected <= event_ids:
        raise ValueError('Enrichment event selection does not match backbone')
    coverage = tables['source_coverage']
    expected = {(v,s) for v in selected for s in manifest['requested_sources']}
    actual = set(zip(coverage.tornado_id,coverage.source))
    if expected != actual or coverage.duplicated(['tornado_id','source']).any():
        raise ValueError('Incomplete or duplicate source coverage rows')
    assets = {a['asset_id'] for a in manifest['assets']}
    for name,keys in TABLES.items():
        if name in OPTIONAL_TABLE_SOURCES and OPTIONAL_TABLE_SOURCES[name] not in manifest['requested_sources']:
            if name in tables:raise ValueError(f'Unexpected deferred source table: {name}')
            continue
        frame = tables[name]
        if frame.duplicated(keys).any():raise ValueError(f'Duplicate source keys: {name}')
        if 'tornado_id' in frame and not set(frame.tornado_id) <= selected:
            raise ValueError(f'Unknown event in {name}')
        for key in [k for k in frame if k.endswith('asset_id')]:
            if not set(frame[key].dropna()) <= assets:raise ValueError(f'Missing asset provenance: {name}')
    for child,key,parent,parent_key in [('tornado_radar','record_id','radar_detections','record_id'),
            ('tornado_warnings','record_id','warning_updates','record_id'),
            ('tornado_tracts','tract_id','tract_boundaries','record_id')]:
        if child not in tables:continue
        if not set(tables[child][key]) <= set(tables[parent][parent_key]):
            raise ValueError(f'Broken link: {child} -> {parent}')
    if not set(tables['record_provenance'].asset_id) <= assets:
        raise ValueError('Record provenance points to an unknown asset')


def build(data, enrichment, output):
    manifest=json.loads((enrichment/'manifest.json').read_text())
    if manifest['status']=='in_progress':raise ValueError('Wait for the source collection to finish')
    hashes=input_hashes(data)
    if hashes!=manifest['definition']['backbone']:raise ValueError('Backbone changed since enrichment collection')
    tables={}
    for record in manifest['outputs']:
        path=table_path(enrichment,manifest,record)
        if digest(path)!=record['sha256']:raise ValueError(f'Source table changed: {path}')
        tables[path.stem]=pd.read_parquet(path)
    backbone=pd.read_parquet(data/'analysis/tornadoes.parquet')
    validate_sources(tables,manifest,backbone)
    onset,retro=make_views(backbone,tables,manifest['definition']['config'])
    if len(onset)!=len(backbone) or onset.tornado_id.duplicated().any() or not onset.tornado_id.equals(backbone.tornado_id.astype(onset.tornado_id.dtype)):
        raise ValueError('ML view lost or duplicated backbone events')
    pd.testing.assert_series_equal(onset.target_ef_rating.astype('Int8'),backbone.ef_rating,check_names=False)
    output.mkdir(parents=True,exist_ok=True)
    files=[]
    for name,frame in [('events_onset',onset),('events_retrospective',retro)]:
        frame['target_ef_rating']=frame.target_ef_rating.astype('Int8')
        # Keep timestamp columns typed even when the entire input cohort is missing.
        for c in [x for x in frame if x.endswith('_utc')]:frame[c]=pd.to_datetime(frame[c],utc=True)
        path=output/(name+'.parquet');temp=path.with_suffix('.tmp.parquet')
        frame.to_parquet(temp,index=False);pd.testing.assert_frame_equal(frame,pd.read_parquet(temp));temp.replace(path)
        files.append(dict(path=path.name,rows=len(frame),columns=len(frame.columns),sha256=digest(path),bytes=path.stat().st_size))
    spec=dictionary(onset,retro,manifest['definition']['config'])
    atomic_json(output/'feature_dictionary.json',spec)
    result=dict(schema_version=1,generated_at=now(),source_status=manifest['status'],
                source_event_count=manifest['event_count'],backbone_events=len(backbone),
                input_enrichment_manifest_sha256=digest(enrichment/'manifest.json'),backbone=hashes,
                code_sha256={p.name:digest(p) for p in Path(__file__).parent.glob('*.py')},files=files,
                feature_dictionary_sha256=digest(output/'feature_dictionary.json'),
                verification='all catalog rows and targets preserved; source hashes and Parquet round trips checked')
    atomic_json(output/'manifest.json',result)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',type=Path,default=ROOT/'data')
    p.add_argument('--enrichment-dir',type=Path)
    p.add_argument('--output',type=Path)
    a=p.parse_args(argv)
    print(json.dumps(build(a.data_dir,a.enrichment_dir or a.data_dir/'enrichment',a.output or a.data_dir/'ml'),indent=2))
    return 0


if __name__=='__main__':sys.exit(main())
