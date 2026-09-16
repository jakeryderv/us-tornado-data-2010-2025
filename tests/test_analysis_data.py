"""Check analysis semantics, missingness, partition completeness, and Parquet types."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import pandas as pd
import geopandas as gpd
from shapely.geometry import mapping, Polygon, MultiPolygon
from scripts.build_analysis import tornado_table, county_table, annual_table, build_analysis, ncei_table, footprint_table, sha


def spc_fixture():
    common = dict(time='12:00:00',etime='12:05:00',tz=3,st='AL',stf='01',inj=0,fat=0,
                  slat=32.1,slon=-86.2,elat=32.2,elon=-86.1,len=1.25,wid=50,ns=1)
    return pd.DataFrame([dict(common,om='001-A',yr=2010,date='2010-01-01',edat='2010-01-01',mag=0),
                         dict(common,om='002-B',yr=2010,date='2010-02-01',edat='2010-02-01',mag=-9),
                         dict(common,om='001-A',yr=2011,date='2011-01-01',edat='2011-01-01',mag=2)])


def county_fixture():
    return pd.DataFrame([dict(year=2010,county_fips='01001',state_name='Alabama',county_name='Autauga',
                              population=100,housing_units=40,estimate_vintage=2020,map_2020_fips_present=True),
                         dict(year=2011,county_fips='01001',state_name='Alabama',county_name='Autauga',
                              population=None,housing_units=41,estimate_vintage=2020,map_2020_fips_present=False)])


class AnalysisTables(unittest.TestCase):
    def test_unknown_ef_opaque_ids_units_and_nullable_context(self):
        source=spc_fixture(); source.loc[1,'slat']=0
        t=tornado_table(source)
        self.assertEqual(t.tornado_id.tolist(),['spc:2010:001-A','spc:2010:002-B','spc:2011:001-A'])
        self.assertEqual(t.ef_rating.tolist()[0],0)
        self.assertTrue(pd.isna(t.ef_rating.iloc[1]))
        self.assertEqual(t.spc_rating_code.iloc[1],-9)
        self.assertEqual(t.path_length_miles.iloc[0],1.25)
        self.assertEqual(t.path_width_yards.iloc[0],50)
        self.assertFalse(t.start_coordinates_valid.iloc[1])
        self.assertEqual(t.start_latitude.iloc[1],0)  # Source values stay intact.
        self.assertEqual(t.state_fips.iloc[0],'01')
        self.assertEqual(t.start_time.iloc[0],'12:00:00')
        c=county_table(county_fixture())
        self.assertEqual(c.county_fips.iloc[0],'01001')
        self.assertTrue(pd.isna(c.population.iloc[1]))
        with self.assertRaisesRegex(ValueError,'unique'):
            tornado_table(pd.concat([source,source.iloc[[0]]]))
        source.loc[0,'mag']=9
        with self.assertRaisesRegex(ValueError,'Unexpected'):
            tornado_table(source)

    def test_annual_counts_keep_source_units_and_missing_partitions_distinct(self):
        t=tornado_table(spc_fixture()); c=county_table(county_fixture())
        n={(y,k):7 for y in [2010,2011,2012] for k in ['details','fatalities','locations']}
        d={(y,k):20 for y in [2010,2011,2012] for k in ['DAT','SED']}
        a=annual_table(t,c,2010,2012,n,d)
        self.assertEqual(a.spc_tracks.tolist(),[2,1,0])
        self.assertEqual(a.spc_ef_unknown.tolist(),[1,0,0])
        self.assertEqual(a.spc_known_ef_fraction.iloc[0],.5)
        self.assertTrue(pd.isna(a.spc_known_ef_fraction.iloc[2]))
        self.assertEqual(a.ncei_tornado_details_rows.iloc[0],7)
        self.assertEqual(a.efc_dat_footprints.iloc[0],20)
        self.assertEqual(a.census_counties_without_2020_map.tolist(),[0,1,0])
        del d[2010,'DAT']
        with self.assertRaises(KeyError):annual_table(t,c,2010,2012,n,d)

    def test_file_build_roundtrip_and_input_provenance(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'spc').mkdir(); (root/'census_population').mkdir()
            spc_fixture().to_csv(root/'spc/tornadoes_2010_2011.csv',index=False)
            county_fixture().to_csv(root/'census_population/county_context_2010_2011.csv',index=False)
            for year in [2010,2011]:
                for table in ['details','fatalities','locations']:
                    p=root/f'ncei_storm_events/tornado/{year}_{table}.csv';p.parent.mkdir(parents=True,exist_ok=True)
                    p.write_text(f'EVENT_ID,EVENT_TYPE,STATE_FIPS,CZ_FIPS,BEGIN_DATE_TIME,TOR_F_SCALE\n{year},Tornado,01,001,01-JAN-{str(year)[2:]} 00:00:00,EF0\n')
                p=root/f'event_footprints/{year}_tornado_footprint.geojson';p.parent.mkdir(parents=True,exist_ok=True)
                features=[dict(type='Feature',geometry={'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1],[0,0]]]},
                    properties=dict(objectid=i,source=source,stormdate=f'{year}-01-01T00:00:00Z',
                        efscale='EF0' if i==0 else 'EFU',width=0.99 if i==0 else 0,parents=[],children=[1] if i==0 else []))
                    for i,source in enumerate(['DAT','SED'])]
                p.write_text(json.dumps({'type':'FeatureCollection','features':features}))
                p.with_name(p.name+'.metadata.json').write_text(json.dumps({'sha256':sha(p)}))
            county={'STATEFP':'01','COUNTYFP':'001','COUNTYNS':'00123456','AFFGEOID':'0500000US01001','GEOID':'01001',
                    'NAME':'Autauga','NAMELSAD':'Autauga County','STUSPS':'AL','STATE_NAME':'Alabama','LSAD':'06','ALAND':100,'AWATER':2}
            polygon=MultiPolygon([Polygon([(0,0),(4,0),(4,4),(0,4),(0,0)],holes=[[(1,1),(1,2),(2,2),(2,1),(1,1)]])])
            boundary=root/'census_boundaries/counties_2020_5m.geojson';boundary.parent.mkdir()
            boundary.write_text(json.dumps({'features':[{'properties':county,'geometry':mapping(polygon)}]}))
            m=build_analysis(root,start=2010,end=2011)
            self.assertEqual(m['verification']['unknown_ef_preserved'],1)
            self.assertEqual(len(m['inputs']),13)
            self.assertEqual(len(m['files']),10)
            self.assertEqual(m['linkage']['verification']['status'],'passed')
            self.assertTrue((root/'analysis/source_crosswalk.parquet').is_file())
            self.assertFalse((root/'analysis/tornadoes_linked.parquet').exists())
            geo=gpd.read_parquet(root/'analysis/county_boundaries.parquet')
            self.assertEqual(geo.county_fips.iloc[0],'01001')
            self.assertEqual(geo.geometry.iloc[0].wkb,polygon.wkb)
            self.assertTrue(geo.crs.is_geographic)
            footprints=gpd.read_parquet(root/'analysis/tornado_footprints.parquet')
            self.assertEqual(len(footprints),4)
            self.assertEqual(footprints.ef_rating.isna().sum(),2)
            self.assertTrue(footprints.path_width_yards.isna().all())
            self.assertEqual(footprints.width.tolist(),[.99,0,.99,0])
            self.assertEqual(footprints.children.iloc[0].tolist(),[1])
            self.assertEqual(str(footprints.stormdate_datetime_utc.dt.tz),'UTC')
            self.assertEqual(footprints.source_row.tolist(),[1,2,1,2])
            self.assertFalse((root/'analysis/survey_points.parquet').exists())
            t=pd.read_parquet(root/'analysis/tornadoes.parquet')
            self.assertEqual(str(t.ef_rating.dtype),'Int8')
            self.assertEqual(t.ef_rating.isna().sum(),1)
            c=pd.read_parquet(root/'analysis/county_context.parquet')
            self.assertEqual(c.county_fips.tolist(),['01001','01001'])
            self.assertTrue(pd.isna(c.population.iloc[1]))
            events=root/'ncei_storm_events/tornado/2010_fatalities.csv'
            original=events.read_text()
            events.write_text(original.replace('2010,Tornado','9999,Tornado'))
            with self.assertRaisesRegex(ValueError,'Orphan'):build_analysis(root,start=2010,end=2011)
            events.write_text(original)
            batch=root/'event_footprints/2010_tornado_footprint.geojson'
            original=batch.read_text();batch.write_text(original+' ')
            with self.assertRaisesRegex(ValueError,'checksum'):build_analysis(root,start=2010,end=2011)
            batch.write_text(original)
            batch.unlink()
            with self.assertRaises(FileNotFoundError):build_analysis(root,start=2010,end=2011)


    def test_ncei_preserves_text_identifiers_and_local_dates(self):
        source=pd.DataFrame({'EVENT_ID':['001','002'],'STATE_FIPS':['1','1'],'CZ_FIPS':['1','2'],
            'CZ_TYPE':['C','Z'],'BEGIN_DATE_TIME':['01-JAN-10 12:30:00',pd.NA],
            'TOR_F_SCALE':['EF0','EFU'],'EVENT_NARRATIVE':['NA','Null'],
            'DEATHS_DIRECT':['0',pd.NA],'DAMAGE_PROPERTY':['0','10K'],'source_year':[2010,2010]})
        frame,_=ncei_table([source])
        self.assertEqual(frame.event_id.tolist(),['001','002'])
        self.assertEqual(frame.state_fips.tolist(),['01','01'])
        self.assertEqual(frame.county_zone_code.tolist(),['001','002'])
        self.assertEqual(frame.event_narrative.tolist(),['NA','Null'])
        self.assertEqual(frame.damage_property.tolist(),['0','10K'])
        self.assertEqual(frame.ef_rating.iloc[0],0)
        self.assertTrue(pd.isna(frame.ef_rating.iloc[1]))
        self.assertTrue(pd.isna(frame.deaths_direct.iloc[1]))
        self.assertIsNone(frame.begin_datetime_local.dt.tz)
        self.assertTrue(pd.isna(frame.begin_datetime_local.iloc[1]))

    def test_footprints_preserve_sentinels_relationships_and_unknown_labels(self):
        props=dict(objectid=1,source='DAT',stormdate='2011-01-01T02:00:00Z',efscale='EF3+',
                   max_efscale='EFU',width=.99,maxwind=-99,parents=[2],children=[],edit_time=1775923739000.0,edit_time_line=-99.0,
                   source_file='2010.geojson',source_year=2010,source_row=1)
        feature=dict(type='Feature',properties=props,geometry=None)
        frame,_=footprint_table([feature])
        self.assertEqual(frame.footprint_id.iloc[0],'efc:2010:DAT:1')
        self.assertEqual(frame.objectid.iloc[0],'1')
        self.assertEqual(frame.maxwind.iloc[0],-99)
        self.assertEqual(frame.parents.iloc[0],[2])
        self.assertTrue(frame.geometry.isna().iloc[0])
        self.assertTrue(pd.isna(frame.ef_rating.iloc[0]))
        self.assertTrue(pd.isna(frame.path_width_yards.iloc[0]))
        self.assertTrue(frame.width_is_placeholder.iloc[0])
        self.assertTrue(pd.isna(frame.edit_time_line_datetime_utc.iloc[0]))
        self.assertEqual(frame.edit_time.iloc[0],'1775923739000.0')
        self.assertEqual(frame.edit_time_datetime_utc.iloc[0],pd.to_datetime(1775923739000,unit='ms',utc=True))
        from footprint_data import validate_collection
        self.assertEqual(validate_collection({'type':'FeatureCollection','features':[feature]},2010)['quality']['utc_year_differs'],1)
        with self.assertRaisesRegex(ValueError,'unique'):
            footprint_table([feature,feature])
        props['unexpected']='new field'
        with self.assertRaisesRegex(ValueError,'schema'):footprint_table([feature])

if __name__=='__main__':unittest.main()
