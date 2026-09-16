"""Semantic checks for conservative linkage, ambiguity, time zones and aggregation."""
import unittest

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

from scripts.build_crosswalk import (
    Rules, candidate_links, footprint_families, linked_views, ncei_offset,
    prepare_sources, spatial_evidence, time_evidence, utc_from_local,
)


def timestamp(value='2020-06-01 18:00'):
    return pd.Timestamp(value,tz='UTC')


def target(identifier='spc:a',start=None):
    start=start if start is not None else timestamp()
    return dict(tornado_id=identifier,start=start,end=start+pd.Timedelta(minutes=10),
                geometry=LineString([(-97,35),(-96.9,35)]),eligible=True)


def source(identifier='event:a',start=None,geometry=None):
    start=start if start is not None else timestamp()
    return dict(source_table='storm_events',source_id=identifier,source_group_id='ncei-episode:a',
        source_origin='NCEI',source_year=2020,source_file='2020_details.csv',source_row=1,
        start=start,end=start+pd.Timedelta(minutes=5),
        geometry=geometry if geometry is not None else LineString([(-96.99,35),(-96.95,35)]),
        time_basis='ncei_fixed_offset',eligible=True,input_issue='none')


class CrosswalkTests(unittest.TestCase):
    def test_fixed_offsets_and_new_year_without_daylight_saving(self):
        self.assertEqual(ncei_offset('CST-6'),-6)
        self.assertEqual(ncei_offset('HST-10'),-10)
        self.assertIsNone(ncei_offset('unknown'))
        self.assertEqual(utc_from_local('2020-12-31 23:30',-6),timestamp('2021-01-01 05:30'))
        self.assertEqual(utc_from_local('2020-07-01 12:00',-6),timestamp('2020-07-01 18:00'))
        self.assertTrue(pd.isna(utc_from_local('2020-07-01',None)))

    def test_county_segment_inside_longer_track_is_accepted(self):
        result=candidate_links([target()],[source(start=timestamp('2020-06-01 18:04'))],Rules())
        self.assertTrue(result.accepted.iloc[0])
        self.assertEqual(result.confidence.iloc[0],'high')
        self.assertLess(result.source_extent_distance_km.iloc[0],0.1)
        self.assertEqual(time_evidence(timestamp('2020-06-01 18:04'),timestamp('2020-06-01 18:09'),target()['start'],target()['end'])[:2],(0,0))

    def test_competing_tracks_stay_ambiguous_and_order_independent(self):
        targets=[target('spc:b'),target('spc:a',timestamp('2020-06-01 18:01'))]
        first=candidate_links(targets,[source()],Rules()).sort_values('tornado_id').reset_index(drop=True)
        second=candidate_links(targets[::-1],[source()],Rules()).sort_values('tornado_id').reset_index(drop=True)
        pd.testing.assert_frame_equal(first,second)
        self.assertFalse(first.accepted.any())
        self.assertTrue(first.match_status.eq('ambiguous').all())
        self.assertTrue(first.confidence.eq('none').all())

    def test_borderline_unique_candidate_requires_review(self):
        row=source(start=timestamp('2020-06-01 17:56'))
        result=candidate_links([target()],[row],Rules())
        self.assertFalse(result.accepted.any())
        self.assertEqual(result.evidence_tier.iloc[0],'borderline')
        self.assertEqual(result.decision_reason.iloc[0],'borderline_time_or_geometry')

    def test_intersecting_but_divergent_paths_are_not_accepted(self):
        src=source(geometry=LineString([(-96.95,34.8),(-96.95,35.2)]))
        result=candidate_links([target()],[src],Rules())
        self.assertLess(result.geometry_gap_km.iloc[0],0.01)
        self.assertGreater(result.source_extent_distance_km.iloc[0],20)
        self.assertFalse(result.accepted.any())

    def test_missing_and_nonqualifying_records_are_preserved(self):
        records=[source('no-time'),source('far',geometry=Point(-120,40)),source('review')]
        records[0].update(start=pd.NaT,end=pd.NaT,eligible=False,input_issue='invalid_time_or_coordinates')
        records[2].update(eligible=False,input_issue='stormdate_only')
        frame=candidate_links([target()],records,Rules())
        self.assertEqual(set(frame.source_id),{'no-time','far','review'})
        self.assertFalse(frame.accepted.any())
        self.assertEqual(frame.loc[frame.source_id.eq('review'),'match_status'].iloc[0],'review_required')

    def test_geometry_distance_supports_alaska_and_hawaii(self):
        for lon,lat in [(-150,61),(-155,20),(-97,35)]:
            minimum,extent=spatial_evidence(Point(lon,lat),LineString([(lon-.01,lat),(lon+.01,lat)]))
            self.assertLess(minimum,0.01)
            self.assertLess(extent,0.01)

    def test_conflicting_footprint_family_assignments_are_demoted(self):
        a,b=source('efc:a'),source('efc:b',start=timestamp('2020-06-01 20:00'))
        for r in [a,b]:r.update(source_table='tornado_footprints',source_group_id='efc-family:shared')
        result=candidate_links([target(),target('spc:b',timestamp('2020-06-01 20:00'))],[a,b],Rules())
        self.assertFalse(result.accepted.any())
        self.assertTrue(result.loc[result.plausible,'decision_reason'].eq('footprint_family_conflict').all())

    def test_source_families_are_scoped_by_year_and_origin(self):
        rows=[]
        for year in [2020,2021]:
            for origin in ['DAT','SED']:
                for i in [1,2]:
                    rows.append(dict(footprint_id=f'{year}:{origin}:{i}',source_year=year,source=origin,objectid=str(i),
                                     parents=[] if i==1 else [1],children=[2] if i==1 else [],path_guid=None))
        families=footprint_families(pd.DataFrame(rows))
        self.assertEqual(len(set(families.values())),4)
        self.assertEqual(families['2020:DAT:1'],families['2020:DAT:2'])

    def test_county_context_deduplicates_segments_and_keeps_missing_null(self):
        targets=[target(),target('spc:b',timestamp('2020-06-02 18:00'))]
        records=[source('e1'),source('e2'),source('e3')]
        for r in records:r['source_group_id']='ncei-episode:a'
        cw=candidate_links(targets,records,Rules())
        events=pd.DataFrame([dict(event_id=f'e{i}',source_year=2020,county_zone_type='C',state_fips='01',
                                  county_zone_code='001' if i<3 else '003') for i in [1,2,3]])
        counties=pd.DataFrame([dict(year=2020,county_fips='01001',population=100,housing_units=40),
                               dict(year=2020,county_fips='01003',population=pd.NA,housing_units=20)])
        spc=pd.DataFrame(dict(tornado_id=['spc:a','spc:b'],ef_rating=pd.Series([2,pd.NA],dtype='Int64')))
        links,view=linked_views(spc,events,counties,cw,targets)
        self.assertEqual(len(links),3)
        self.assertEqual(view.linked_county_years.tolist(),[2,0])
        self.assertTrue(view.linked_county_population_sum.isna().all())
        self.assertEqual(view.linked_county_housing_units_sum.iloc[0],60)
        self.assertTrue(pd.isna(view.linked_county_housing_units_sum.iloc[1]))
        pd.testing.assert_frame_equal(spc,view[spc.columns])

    def test_wider_guard_blocks_a_strong_match_with_borderline_competitor(self):
        result=candidate_links([target('a'),target('b',timestamp('2020-06-01 18:07'))],[source()],Rules())
        self.assertEqual(result.plausible_candidate_count.tolist(),[2,2])
        self.assertFalse(result.accepted.any())

    def test_full_build_hashes_and_preservation(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from scripts.build_crosswalk import build_crosswalk,sha
        spc=pd.DataFrame([dict(tornado_id='spc:a',year=2020,start_date=pd.Timestamp('2020-06-01'),
            start_time='12:00:00',end_date=pd.Timestamp('2020-06-01'),end_time='12:10:00',spc_timezone_code=3,
            start_longitude=-97.,start_latitude=35.,end_longitude=-96.9,end_latitude=35.,ef_rating=pd.NA)])
        events=pd.DataFrame([dict(event_id='e1',episode_id='episode1',source_year=2020,source_file='2020.csv',source_row=1,
            begin_datetime_local=pd.Timestamp('2020-06-01 12:00'),end_datetime_local=pd.Timestamp('2020-06-01 12:05'),
            source_timezone='CST-6',begin_longitude=-97.,begin_latitude=35.,end_longitude=-96.95,end_latitude=35.,
            county_zone_type='C',state_fips='01',county_zone_code='001')])
        footprints=gpd.GeoDataFrame([dict(footprint_id='efc:2020:DAT:1',objectid='1',source_year=2020,source='DAT',
            source_file='2020.geojson',source_row=1,parents=[],children=[],path_guid=None,
            starttime_datetime_utc=timestamp(),endtime_datetime_utc=timestamp('2020-06-01 18:05'),
            starttime_line_datetime_utc=pd.NaT,endtime_line_datetime_utc=pd.NaT,stormdate_datetime_utc=timestamp(),
            geometry=Point(-96.97,35).buffer(.001))],crs='OGC:CRS84')
        counties=pd.DataFrame([dict(year=2020,county_fips='01001',population=100,housing_units=40)])
        with TemporaryDirectory() as temp:
            root=Path(temp); analysis=root/'analysis';analysis.mkdir()
            frames={'tornadoes.parquet':spc,'storm_events.parquet':events,'tornado_footprints.parquet':footprints,'county_context.parquet':counties}
            for name,frame in frames.items():frame.to_parquet(analysis/name,index=False)
            (analysis/'manifest.json').write_text(json.dumps({'files':[{'path':name,'sha256':sha(analysis/name)} for name in frames]}))
            original={name:sha(analysis/name) for name in frames}
            report=build_crosswalk(root)
            self.assertEqual(report['summary']['spc_with_both'],1)
            self.assertEqual(report['verification']['status'],'passed')
            result=pd.read_parquet(root/'linkage/tornadoes_linked.parquet')
            self.assertTrue(pd.isna(result.ef_rating.iloc[0]))
            self.assertEqual(result.linked_county_population_sum.iloc[0],100)
            self.assertEqual(original,{name:sha(analysis/name) for name in frames})
            again=build_crosswalk(root)
            self.assertEqual([f['sha256'] for f in report['files']],[f['sha256'] for f in again['files']])
            saved=sha(root/'linkage/manifest.json')
            (analysis/'tornadoes.parquet').write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'Stale analysis'):build_crosswalk(root)
            self.assertEqual(sha(root/'linkage/manifest.json'),saved)
            with self.assertRaisesRegex(ValueError,'separate'):build_crosswalk(root,analysis)


if __name__=='__main__':unittest.main()
