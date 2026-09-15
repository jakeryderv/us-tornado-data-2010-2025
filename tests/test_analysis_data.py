"""Check analysis semantics, missingness, partition completeness, and Parquet types."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import pandas as pd
from scripts.build_analysis import tornado_table, county_table, annual_table, build_analysis


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
        d={(y,k):20 for y in [2010,2011,2012] for k in ['points','lines','polygons']}
        a=annual_table(t,c,2010,2012,n,d)
        self.assertEqual(a.spc_tracks.tolist(),[2,1,0])
        self.assertEqual(a.spc_ef_unknown.tolist(),[1,0,0])
        self.assertEqual(a.spc_known_ef_fraction.iloc[0],.5)
        self.assertTrue(pd.isna(a.spc_known_ef_fraction.iloc[2]))
        self.assertEqual(a.ncei_tornado_details_rows.iloc[0],7)
        self.assertEqual(a.dat_points.iloc[0],20)
        self.assertEqual(a.census_counties_without_2020_map.tolist(),[0,1,0])
        del d[2010,'points']
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
                    p.write_text('EVENT_ID\n1\n2\n')
                for layer in ['points','lines','polygons']:
                    p=root/f'nws_dat/{year}/{layer}/index.json';p.parent.mkdir(parents=True,exist_ok=True)
                    p.write_text(json.dumps({'features':3,'batches':[{'features':3}]}))
            m=build_analysis(root,start=2010,end=2011)
            self.assertEqual(m['verification']['unknown_ef_preserved'],1)
            self.assertEqual(len(m['inputs']),14)
            t=pd.read_parquet(root/'analysis/tornadoes.parquet')
            self.assertEqual(str(t.ef_rating.dtype),'Int8')
            self.assertEqual(t.ef_rating.isna().sum(),1)
            c=pd.read_parquet(root/'analysis/county_context.parquet')
            self.assertEqual(c.county_fips.tolist(),['01001','01001'])
            self.assertTrue(pd.isna(c.population.iloc[1]))
            (root/'nws_dat/2010/points/index.json').unlink()
            with self.assertRaises(FileNotFoundError):build_analysis(root,start=2010,end=2011)

if __name__=='__main__':unittest.main()
