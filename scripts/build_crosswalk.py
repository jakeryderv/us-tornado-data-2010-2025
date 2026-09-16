"""Build a conservative, auditable SPC-centered linkage layer from analysis tables."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Geod
import shapely
import pyproj
from shapely.geometry import LineString, Point
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[1]
GEOD = Geod(ellps='WGS84')


@dataclass(frozen=True)
class Rules:
    candidate_minutes: float = 120
    candidate_km: float = 20
    plausible_minutes: float = 10
    plausible_km: float = 5
    strong_minutes: float = 2
    strong_km: float = 2
    max_duration_minutes: float = 360


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def utc_from_local(value, offset_hours):
    """Offset is local minus UTC; fixed source offsets never acquire DST rules."""
    if pd.isna(value) or pd.isna(offset_hours):
        return pd.NaT
    return (pd.Timestamp(value) - pd.Timedelta(hours=float(offset_hours))).tz_localize('UTC')


def ncei_offset(value):
    match = re.fullmatch(r'[A-Z]+([+-]\d{1,2})', str(value))
    return int(match[1]) if match and -12 <= int(match[1]) <= 14 else None


def coordinate(lon, lat):
    return (pd.notna(lon) and pd.notna(lat) and -180 <= float(lon) <= 180
            and -90 <= float(lat) <= 90 and float(lon) != 0 and float(lat) != 0)


def track(lon, lat, end_lon, end_lat):
    if not coordinate(lon, lat):
        return None, False
    if not coordinate(end_lon, end_lat):
        return Point(lon, lat), False
    return (Point(lon, lat) if (lon, lat) == (end_lon, end_lat)
            else LineString([(lon, lat), (end_lon, end_lat)])), True


def interval_valid(start, end, rules):
    return (pd.notna(start) and pd.notna(end)
            and 0 <= (end-start).total_seconds()/60 <= rules.max_duration_minutes)


def prepare_spc(frame, rules):
    records = []
    for row in frame.itertuples(index=False):
        offset = {3: -6, 9: 0}.get(row.spc_timezone_code)
        start = utc_from_local(f'{row.start_date:%Y-%m-%d} {row.start_time}', offset)
        end = utc_from_local(f'{row.end_date:%Y-%m-%d} {row.end_time}', offset)
        geometry, complete = track(row.start_longitude,row.start_latitude,row.end_longitude,row.end_latitude)
        records.append(dict(tornado_id=row.tornado_id,start=start,end=end,geometry=geometry,
                            eligible=complete and interval_valid(start,end,rules)))
    return records


class Union:
    def __init__(self, keys):
        self.parent = {key:key for key in keys}

    def find(self, key):
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while key != root:
            next_key = self.parent[key]
            self.parent[key] = root
            key = next_key
        return root

    def join(self, left, right):
        a,b = self.find(left),self.find(right)
        if a != b:
            self.parent[max(a,b)] = min(a,b)


def footprint_families(frame):
    """Only source-scoped explicit parent/child IDs and non-sentinel path GUIDs."""
    union = Union(frame.footprint_id)
    lookup = {(int(r.source_year),r.source,str(r.objectid)):r.footprint_id for r in frame.itertuples()}
    guids = {}
    for r in frame.itertuples():
        for item in [*r.parents, *r.children]:
            other = lookup.get((int(r.source_year),r.source,str(int(item))))
            if other is not None:
                union.join(r.footprint_id,other)
        guid = str(r.path_guid).strip().lower()
        if re.fullmatch(r'\{?[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\}?',guid) and set(guid)-set('0-{}'):
            key = (int(r.source_year),r.source,guid)
            if key in guids:
                union.join(r.footprint_id,guids[key])
            guids[key] = r.footprint_id
    return {key:union.find(key) for key in union.parent}


def prepare_sources(events, footprints, rules):
    records = []
    for r in events.itertuples(index=False):
        offset = ncei_offset(getattr(r,'source_timezone',None))
        start,end = [utc_from_local(x,offset) for x in [getattr(r,'begin_datetime_local',pd.NaT),getattr(r,'end_datetime_local',pd.NaT)]]
        geometry,complete = track(*(getattr(r,name,pd.NA) for name in ['begin_longitude','begin_latitude','end_longitude','end_latitude']))
        records.append(dict(source_table='storm_events',source_id=str(r.event_id),source_group_id=f'ncei-episode:{getattr(r,"episode_id",r.event_id)}',
            source_origin='NCEI',source_year=int(r.source_year),source_file=r.source_file,source_row=int(r.source_row),
            start=start,end=end,geometry=geometry,time_basis='ncei_fixed_offset',
            eligible=complete and interval_valid(start,end,rules),
            input_issue='none' if complete and interval_valid(start,end,rules) else 'invalid_time_or_coordinates'))
    families = footprint_families(footprints)
    for r in footprints.itertuples(index=False):
        # Prefer the polygon's own valid interval, then its associated line interval.
        start,end,basis = r.starttime_datetime_utc,r.endtime_datetime_utc,'efc_polygon_interval'
        bad_primary = pd.notna(start) and pd.notna(end) and not interval_valid(start,end,rules)
        if not interval_valid(start,end,rules):
            start,end,basis = r.starttime_line_datetime_utc,r.endtime_line_datetime_utc,'efc_line_interval'
        if not interval_valid(start,end,rules):
            start=end=r.stormdate_datetime_utc
            basis='efc_stormdate_only'
        conflict = pd.notna(start) and pd.notna(r.stormdate_datetime_utc) and abs((start-r.stormdate_datetime_utc).total_seconds()) > 3600
        geometry=r.geometry
        usable = geometry is not None and not geometry.is_empty and geometry.is_valid
        eligible = usable and interval_valid(start,end,rules) and basis != 'efc_stormdate_only' and not conflict and not bad_primary
        issue = ('invalid_geometry' if not usable else 'conflicting_times' if conflict or bad_primary
                 else 'stormdate_only' if basis == 'efc_stormdate_only' else 'none')
        records.append(dict(source_table='tornado_footprints',source_id=r.footprint_id,
            source_group_id=f'efc-family:{families[r.footprint_id]}',source_origin=r.source,
            source_year=int(r.source_year),source_file=r.source_file,source_row=int(r.source_row),
            start=start,end=end,geometry=geometry,time_basis=basis,eligible=eligible,input_issue=issue))
    return records


def project_local(geometry, lon, lat):
    """WGS84 azimuthal-equidistant coordinates centered on the source geometry."""
    def convert(x,y,z=None):
        x,y=np.asarray(x),np.asarray(y)
        azimuth,_,distance=GEOD.inv(np.full_like(x,lon,dtype=float),np.full_like(y,lat,dtype=float),x,y)
        radians=np.deg2rad(azimuth)
        return distance*np.sin(radians),distance*np.cos(radians)
    return transform(convert,geometry)


def spatial_evidence(source_geometry, target_geometry):
    center=source_geometry.representative_point()
    source=project_local(source_geometry,center.x,center.y)
    target=project_local(target_geometry,center.x,center.y)
    # Directed extent supports a county segment contained within a longer SPC track.
    vertices=shapely.points(shapely.get_coordinates(source))
    return source.distance(target)/1000, float(shapely.distance(vertices,target).max())/1000


def time_evidence(source_start,source_end,target_start,target_end):
    gap=max(0,(source_start-target_end).total_seconds(),(target_start-source_end).total_seconds())/60
    outside=max(0,(target_start-source_start).total_seconds(),(source_end-target_end).total_seconds())/60
    return gap,outside,abs((source_start-target_start).total_seconds())/60


def candidate_links(spc, sources, rules):
    targets=sorted((r for r in spc if r['eligible']),key=lambda r:(r['start'],r['tornado_id']))
    starts=np.array([r['start'].value for r in targets],dtype=np.int64)
    rows=[]
    for src in sources:
        base={k:src[k] for k in ['source_table','source_id','source_group_id','source_origin','source_year','source_file','source_row','time_basis','input_issue']}
        base.update(source_start_utc=src['start'],source_end_utc=src['end'],source_auto_eligible=src['eligible'])
        candidates=[]
        if pd.notna(src['start']) and pd.notna(src['end']) and src['geometry'] is not None and not src['geometry'].is_empty:
            low=(src['start']-pd.Timedelta(minutes=rules.candidate_minutes+rules.max_duration_minutes)).value
            high=(src['end']+pd.Timedelta(minutes=rules.candidate_minutes)).value
            for target in targets[np.searchsorted(starts,low):np.searchsorted(starts,high,side='right')]:
                gap,outside,delta=time_evidence(src['start'],src['end'],target['start'],target['end'])
                if gap > rules.candidate_minutes:
                    continue
                # Cheap geodesic radius bound before transforming full footprint vertices.
                point=src['geometry'].representative_point(); startpoint=Point(shapely.get_coordinates(target['geometry'])[0])
                _,_,distance=GEOD.inv(point.x,point.y,startpoint.x,startpoint.y)
                source_coords=shapely.get_coordinates(src['geometry'])
                _,_,radii=GEOD.inv(np.full(len(source_coords),point.x),np.full(len(source_coords),point.y),source_coords[:,0],source_coords[:,1])
                endcoords=shapely.get_coordinates(target['geometry'])[-1]
                _,_,length=GEOD.inv(startpoint.x,startpoint.y,*endcoords)
                if distance > max(radii)+length+rules.candidate_km*1000:
                    continue
                minimum,extent=spatial_evidence(src['geometry'],target['geometry'])
                if minimum > rules.candidate_km:
                    continue
                plausible=outside <= rules.plausible_minutes and extent <= rules.plausible_km
                strong=outside <= rules.strong_minutes and extent <= rules.strong_km
                candidates.append(dict(base,tornado_id=target['tornado_id'],spc_start_utc=target['start'],spc_end_utc=target['end'],
                    interval_gap_minutes=gap,interval_outside_minutes=outside,start_delta_minutes=delta,
                    geometry_gap_km=minimum,source_extent_distance_km=extent,plausible=plausible,
                    evidence_score=outside/rules.plausible_minutes+extent/rules.plausible_km,
                    confidence='high' if strong else 'medium' if plausible else 'none',
                    evidence_tier='strong' if strong else 'borderline' if plausible else 'weak'))
        count=sum(r['plausible'] for r in candidates)
        for rank,row in enumerate(sorted(candidates,key=lambda r:(r['evidence_score'],r['tornado_id'])),1):
            row.update(candidate_rank=rank,candidate_count=len(candidates),plausible_candidate_count=count)
            row['accepted']=bool(row['confidence']=='high' and count==1 and src['eligible'])
            row['match_status']=('accepted' if row['accepted'] else 'ambiguous' if row['plausible'] and count>1
                                 else 'review_required' if row['plausible'] else 'outside_acceptance')
            row['decision_reason']=('unique_time_and_geometry_match' if row['accepted'] else 'multiple_plausible_tracks' if row['plausible'] and count>1
                                    else src['input_issue'] if row['plausible'] and not src['eligible']
                                    else 'borderline_time_or_geometry' if row['plausible'] else 'outside_time_or_extent_threshold')
            rows.append(row)
        if not candidates:
            rows.append(dict(base,tornado_id=None,spc_start_utc=pd.NaT,spc_end_utc=pd.NaT,
                interval_gap_minutes=None,interval_outside_minutes=None,start_delta_minutes=None,geometry_gap_km=None,
                source_extent_distance_km=None,plausible=False,evidence_score=None,confidence='none',evidence_tier='none',candidate_rank=None,
                candidate_count=0,plausible_candidate_count=0,accepted=False,match_status='unmatched',
                decision_reason='no_candidate_within_search_window' if src['eligible'] else src['input_issue']))
    result=pd.DataFrame(rows)
    # A family explicitly linked by EFC must not be split into different SPC events.
    families=result.loc[result.source_table.eq('tornado_footprints') & result.accepted].groupby('source_group_id').tornado_id.nunique()
    conflicts=set(families[families>1].index)
    conflict_mask=result.source_group_id.isin(conflicts) & result.plausible
    result.loc[conflict_mask,['accepted','match_status','decision_reason']]=[False,'ambiguous','footprint_family_conflict']
    result.loc[~result.accepted,'confidence']='none'
    return result


def linkage_groups(spc, crosswalk):
    """Conservative day/episode grouping hint, not a verified outbreak catalog."""
    union=Union(r['tornado_id'] for r in spc)
    day={}
    for row in spc:
        if pd.notna(row['start']):
            key=row['start'].strftime('%Y-%m-%d')
            if key in day:union.join(row['tornado_id'],day[key])
            day[key]=row['tornado_id']
    plausible=crosswalk.loc[crosswalk.plausible & crosswalk.tornado_id.notna()]
    for _,group in plausible.groupby('source_group_id'):
        ids=sorted(group.tornado_id.unique())
        for other in ids[1:]:union.join(ids[0],other)
    return {key:'linkage:'+union.find(key) for key in union.parent}


def linked_views(spc_frame, events, counties, crosswalk, prepared_spc):
    accepted=crosswalk.loc[crosswalk.accepted]
    ncei=accepted.loc[accepted.source_table.eq('storm_events'),['tornado_id','source_id']]
    detail=ncei.merge(events,left_on='source_id',right_on='event_id',validate='one_to_one')
    for name in ['county_zone_type','state_fips','county_zone_code']:
        if name not in detail:detail[name]=pd.Series(pd.NA,index=detail.index,dtype='string')
    county_links=detail.loc[detail.county_zone_type.eq('C'),['tornado_id','source_id','source_year','state_fips','county_zone_code']].copy()
    county_links['county_fips']=county_links.state_fips.str.zfill(2)+county_links.county_zone_code.str.zfill(3)
    county_links=county_links.rename(columns={'source_id':'ncei_event_id','source_year':'year'}).drop(columns=['state_fips','county_zone_code'])
    county_links=county_links.merge(counties,on=['year','county_fips'],how='left',validate='many_to_one',indicator=True)
    county_links['context_available']=county_links.pop('_merge').eq('both')
    county_links['link_basis']='accepted_ncei_county_code'
    for name in ['tornado_id','ncei_event_id','county_fips','link_basis']:
        county_links[name]=county_links[name].astype('string')
    result=spc_frame.copy()
    for table,prefix in [('storm_events','ncei'),('tornado_footprints','footprint')]:
        rows=crosswalk.loc[crosswalk.source_table.eq(table)]
        counts=rows.loc[rows.accepted].groupby('tornado_id').size()
        result[f'{prefix}_accepted_records']=result.tornado_id.map(counts).fillna(0).astype('Int64')
        unresolved=rows.loc[rows.plausible & ~rows.accepted].groupby('tornado_id').source_id.nunique()
        result[f'{prefix}_unresolved_candidate_records']=result.tornado_id.map(unresolved).fillna(0).astype('Int64')
    for origin in ['DAT','SED']:
        counts=accepted.loc[accepted.source_table.eq('tornado_footprints') & accepted.source_origin.eq(origin)].groupby('tornado_id').size()
        result[f'footprint_{origin.lower()}_accepted_regions']=result.tornado_id.map(counts).fillna(0).astype('Int64')
    unique_counties=county_links.drop_duplicates(['tornado_id','year','county_fips'])
    result['linked_county_years']=result.tornado_id.map(unique_counties.groupby('tornado_id').size()).fillna(0).astype('Int64')
    result['county_context_missing_rows']=result.tornado_id.map(unique_counties.groupby('tornado_id').context_available.agg(lambda x:(~x).sum())).fillna(0).astype('Int64')
    for field in ['population','housing_units']:
        # All-or-null sums: never turn missing context or absent matches into zero.
        values=unique_counties.groupby('tornado_id')[field].agg(lambda x:x.sum() if x.notna().all() else pd.NA)
        result[f'linked_county_{field}_sum']=result.tornado_id.map(values).astype('Int64')
    result['suggested_split_group']=result.tornado_id.map(linkage_groups(prepared_spc,crosswalk)).astype('string')
    result['linkage_available']=result.ncei_accepted_records.gt(0)|result.footprint_accepted_records.gt(0)
    return county_links,result


def typed_crosswalk(frame):
    for name in frame.columns:
        if name.endswith('_utc'):
            frame[name]=pd.to_datetime(frame[name],utc=True).astype('datetime64[ns, UTC]')
        elif name in ['accepted','plausible','source_auto_eligible']:
            frame[name]=frame[name].astype('boolean')
        elif name in ['source_year','source_row','candidate_rank','candidate_count','plausible_candidate_count']:
            frame[name]=frame[name].astype('Int64')
        elif name.endswith(('_minutes','_km')) or name=='evidence_score':
            frame[name]=frame[name].astype('Float64')
        else:
            frame[name]=frame[name].astype('string')
    return frame.sort_values(['source_table','source_year','source_id','candidate_rank'],na_position='last').reset_index(drop=True)


def summarize(crosswalk, linked):
    sources=[]
    for (table,origin),frame in crosswalk.groupby(['source_table','source_origin']):
        status=frame.groupby('source_id').agg(accepted=('accepted','any'),plausible=('plausible','any'),candidates=('candidate_count','max'))
        sources.append(dict(source_table=table,source_origin=origin,source_records=len(status),
            accepted_records=int(status.accepted.sum()),unresolved_with_plausible_candidates=int((~status.accepted & status.plausible).sum()),
            only_outside_acceptance=int((~status.plausible & status.candidates.gt(0)).sum()),no_candidates=int(status.candidates.eq(0).sum()),
            accepted_spc_tracks=int(frame.loc[frame.accepted,'tornado_id'].nunique())))
    return dict(sources=sources,spc_tracks=len(linked),spc_with_ncei=int(linked.ncei_accepted_records.gt(0).sum()),
        spc_with_footprints=int(linked.footprint_accepted_records.gt(0).sum()),
        spc_with_both=int((linked.ncei_accepted_records.gt(0)&linked.footprint_accepted_records.gt(0)).sum()),
        spc_without_accepted_links=int((~linked.linkage_available).sum()),split_groups=int(linked.suggested_split_group.nunique()))


def enrich_analysis(tables, rules=Rules()):
    """Return canonical nine-table content and linkage metadata; never mutate inputs."""
    spc,events,footprints,counties=[tables[x] for x in ['tornadoes.parquet','storm_events.parquet','tornado_footprints.parquet','county_context.parquet']]
    if not spc.tornado_id.is_unique or not events.event_id.is_unique or not footprints.footprint_id.is_unique or counties.duplicated(['year','county_fips']).any():
        raise ValueError('Source keys must be unique')
    targets=prepare_spc(spc,rules); sources=prepare_sources(events,footprints,rules)
    print(f'Matching {len(sources):,} source records against {len(targets):,} SPC tracks',flush=True)
    crosswalk=typed_crosswalk(candidate_links(targets,sources,rules))
    accepted=crosswalk.loc[crosswalk.accepted]
    assert not accepted.duplicated(['source_table','source_id']).any()
    assert crosswalk.groupby(['source_table','source_id']).ngroups == len(events)+len(footprints)
    assert set(accepted.tornado_id)<=set(spc.tornado_id)
    assert accepted.source_extent_distance_km.le(rules.strong_km).all()
    assert accepted.interval_outside_minutes.le(rules.strong_minutes).all()
    assert accepted.source_auto_eligible.all()
    assert accepted.plausible_candidate_count.eq(1).all()
    county_links,linked=linked_views(spc,events,counties,crosswalk,targets)
    pd.testing.assert_frame_equal(spc,linked[spc.columns])
    metadata=dict(algorithm='spc-time-geometry-v1',rules=asdict(rules),
        code_sha256=sha(Path(__file__)),summary=summarize(crosswalk,linked),
        spc_auto_eligible=sum(bool(row['eligible']) for row in targets),
        base_spc_columns=list(spc.columns),
        verification=dict(status='passed',all_source_records_represented=True,
            one_accepted_spc_per_source_record=True,spc_rows_and_values_preserved=True,
            accepted_links_pass_strict_thresholds=True),
        limitations=['Automatic evidence tiers are not calibrated probabilities or manual confirmation.',
            'Search completeness is limited to documented time and geometry windows.',
            'County totals describe linked county-years, not people or buildings struck.',
            'Suggested split groups combine UTC days and plausible episode/family links, not verified outbreaks.'],
        software=dict(pandas=pd.__version__,geopandas=gpd.__version__,shapely=shapely.__version__,pyproj=pyproj.__version__,numpy=np.__version__))
    return dict(tables,**{'tornadoes.parquet':linked,'source_crosswalk.parquet':crosswalk,
                         'tornado_counties.parquet':county_links}),metadata


def main():
    # Compatibility entry point: the canonical build now includes linkage.
    import sys
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from scripts.build_analysis import main as build_main
    build_main()


if __name__=='__main__':main()
