"""Offline source, provenance, and temporal leakage regression tests."""
from dataclasses import asdict
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from shapely.geometry import box

from enrichment.common import Cache, Config, public_url, stable_id
from enrichment.features import (dictionary, exposure_summary,
                                 make_views, radar_summary, split_groups, warning_summary)
from enrichment.land import acs_rows, raster_summary
from enrichment.weather import parse_vtec, swdi_rows


class Response(io.BytesIO):
    status=200
    url='https://example.test/data'
    def __init__(self, body, length=None):
        super().__init__(body)
        self.headers={'Content-Length':str(len(body) if length is None else length)}


class SourceTests(unittest.TestCase):
    def test_cache_checks_small_responses_and_corruption(self):
        with TemporaryDirectory() as d:
            cache=Cache(d)
            with patch('enrichment.common.urlopen',return_value=Response(b'{"rows":[]}')):
                data,asset=cache.json('https://example.test/data?key=private')
            self.assertEqual(data,{'rows':[]})
            meta=cache.assets[asset]
            self.assertNotIn('private',json.dumps(meta))
            with patch('enrichment.common.urlopen',side_effect=AssertionError('cached')):
                cache.json('https://example.test/data?key=newprivate')
            Path(meta['path']).write_text('changed')
            with self.assertRaisesRegex(ValueError,'Corrupt cache'):
                cache.json('https://example.test/data')

    def test_cache_rejects_truncated_binary_and_ignored_ranges(self):
        with TemporaryDirectory() as d:
            for response,kwargs in [(Response(b'abc',99),{}),
                                    (Response(b'<xml>error</xml>'),{'suffix':'.tif'}),
                                    (Response(b'GRIBdata'),{'headers':{'Range':'bytes=0-7'}})]:
                with patch('enrichment.common.urlopen',return_value=response):
                    with self.assertRaises(ValueError):Cache(d).fetch('https://example.test/data',**kwargs)
            self.assertEqual(list(Path(d).iterdir()),[])

    def test_job_checkpoints_share_large_tables_and_verify_them(self):
        from enrichment.pipeline import load_job, save_job
        with TemporaryDirectory() as d:
            output=Path(d)
            result={'tables':{'acs_tracts':[{'record_id':'a','population':20}]},'assets':[],'coverage':{'status':'complete'}}
            for name in ['a','b']:save_job(output/(name+'.json'),result,output)
            self.assertEqual(len(list((output/'job_tables').glob('*.json.gz'))),1)
            self.assertEqual(load_job(output/'a.json',output)['tables'],result['tables'])
            next((output/'job_tables').glob('*.json.gz')).write_text('[]')
            with self.assertRaisesRegex(ValueError,'Changed shared'):load_job(output/'b.json',output)

    def test_json_error_html_does_not_poison_authenticated_retry(self):
        with TemporaryDirectory() as d:
            cache=Cache(d)
            with patch('enrichment.common.urlopen',return_value=Response(b'<html>Missing Key</html>')):
                with self.assertRaises(ValueError):cache.json('https://api.census.gov/data/test')
            self.assertEqual(list(Path(d).iterdir()),[])

    def test_swdi_units_raw_values_and_count_validation(self):
        raw={'SHAPE':'POINT (-97 35)','ZTIME':'2020-01-01T00:00Z','WSR_ID':'KTLX',
             'CELL_ID':'A1','MAX_SHEAR':'30','MXDV':'-999'}
        payload={'result':[raw],'summary':{'count':1}}
        row=swdi_rows(payload,'nx3tvs','asset:1')[0]
        self.assertEqual(row['max_shear_per_s'],.03)
        self.assertIsNone(row['velocity_difference_knots'])
        self.assertEqual(json.loads(row['raw_json']),raw)
        self.assertEqual(row['record_id'],swdi_rows(payload,'nx3tvs','asset:2')[0]['record_id'])
        payload['summary']['count']=2
        with self.assertRaises(ValueError):swdi_rows(payload,'nx3tvs','asset:1')

    def test_vtec_uses_original_product_expiry(self):
        text='/O.NEW.KOUN.TO.W.0001.200101T0000Z-200101T0100Z/'
        self.assertEqual(parse_vtec(text,'OUN','TO',1),('NEW','2020-01-01T01:00:00+00:00'))
        with self.assertRaises(ValueError):parse_vtec(text,'OUN','TO',2)

    def test_partial_county_cancellation_retains_continuing_polygon(self):
        text='/O.CAN.KSHV.TO.W.0003.000000T0000Z-100121T0000Z/\n/O.CON.KSHV.TO.W.0003.000000T0000Z-100121T0000Z/'
        self.assertEqual(parse_vtec(text,'SHV','TO',3),('CON','2010-01-21T00:00:00+00:00'))
        self.assertEqual(parse_vtec(text.splitlines()[0],'SHV','TO',3)[0],'CAN')

    def test_wrong_content_range_rejected_even_with_correct_size(self):
        with TemporaryDirectory() as d:
            response=Response(b'GRIBtest');response.status=206
            response.headers['Content-Range']='bytes 8-15/100'
            with patch('enrichment.common.urlopen',return_value=response):
                with self.assertRaisesRegex(ValueError,'different byte range'):
                    Cache(d).fetch('https://example.test/grid',headers={'Range':'bytes=0-7'})

    def test_acs_preserves_sentinels_and_moes(self):
        columns=['NAME','state','county','tract','B01003_001E','B01003_001M',
                 'B25001_001E','B25001_001M','B25024_010E','B25024_010M']
        raw=['Some tract','01','003','000100','-666666666','-222222222','100','10','5','2']
        row=acs_rows([columns,raw],2019,'asset:1')[0]
        self.assertEqual(row['record_id'],'acs5:2019:01003000100')
        self.assertIsNone(row['population']);self.assertIsNone(row['population_moe'])
        self.assertEqual(row['housing_units_moe'],10)
        self.assertIn('-666666666',row['raw_json'])

    def test_native_raster_keeps_class_codes_and_nodata(self):
        import rasterio
        from rasterio.transform import from_origin
        from shapely.ops import transform
        from pyproj import Transformer
        with TemporaryDirectory() as d:
            p=Path(d)/'source.tif'
            with rasterio.open(p,'w',driver='GTiff',width=2,height=2,count=1,dtype='uint8',
                              crs='EPSG:5070',transform=from_origin(0,60,30,30),nodata=0) as dst:
                dst.write(np.array([[21,41],[82,0]],dtype='uint8'),1)
            polygon=transform(Transformer.from_crs(5070,4326,always_xy=True).transform,box(0,0,60,60))
            result=raster_summary(p,polygon,'land_cover')
            self.assertEqual(result['pixel_count'],4);self.assertEqual(result['valid_pixel_count'],3)
            self.assertEqual(result['class_21_pixels'],1)


class FeatureTests(unittest.TestCase):
    cutoff=pd.Timestamp('2020-01-01T00:30Z')

    def test_shared_source_records_join_otherwise_distinct_split_groups(self):
        base=pd.DataFrame({'tornado_id':['a','b','c','d'], 'suggested_split_group':['day1','day2','day2','day3']})
        tables={'tornado_radar':pd.DataFrame({'tornado_id':['a','b'],'record_id':['shared','shared']}),
                'tornado_warnings':pd.DataFrame(columns=['tornado_id','warning_id'])}
        groups=split_groups(base,tables)
        self.assertEqual(groups['a'],groups['b']);self.assertEqual(groups['a'],groups['c'])
        self.assertNotEqual(groups['a'],groups['d'])

    def test_radar_after_cutoff_cannot_change_onset_features(self):
        rows=pd.DataFrame([dict(product='nx3tvs',observed_at='2020-01-01T00:20Z',available_at='2020-01-01T00:25Z',
             max_shear_per_s=.02,velocity_difference_knots=50),
             dict(product='nx3tvs',observed_at='2020-01-01T00:29Z',available_at='2020-01-01T00:34Z',
             max_shear_per_s=999,velocity_difference_knots=999)])
        r=radar_summary(rows,self.cutoff,complete=True)
        self.assertEqual(r['radar_tvs_count'],1);self.assertEqual(r['radar_max_shear_per_s'],.02)
        self.assertIsNone(radar_summary(rows,self.cutoff,complete=False)['radar_tvs_count'])
        self.assertEqual(radar_summary(pd.DataFrame(),self.cutoff,complete=True)['radar_tvs_count'],0)

    def warning_rows(self):
        return pd.DataFrame([dict(warning_id='one',issued_at='2020-01-01T00:00Z',original_issue_at='2020-01-01T00:00Z',
             action='NEW',known_expiry_at='2020-01-01T01:00Z',phenomenon='TO',covers_start=True),
             dict(warning_id='one',issued_at='2020-01-01T00:40Z',original_issue_at='2020-01-01T00:00Z',
             action='CAN',known_expiry_at='2020-01-01T01:00Z',phenomenon='TO',covers_start=True)])

    def test_future_warning_cancellation_does_not_rewrite_history(self):
        rows=self.warning_rows()
        self.assertEqual(warning_summary(rows,self.cutoff,complete=True)['warning_active_tornado_count'],1)
        self.assertEqual(warning_summary(rows,pd.Timestamp('2020-01-01T00:45Z'),complete=True)['warning_active_tornado_count'],0)

    def test_warning_missing_original_issue_has_unknown_lead(self):
        rows=self.warning_rows();rows['original_issue_at']=None
        result=warning_summary(rows,self.cutoff,complete=True)
        self.assertEqual(result['warning_active_tornado_count'],1)
        self.assertIsNone(result['warning_tornado_lead_minutes'])

    def test_latest_polygon_moving_away_supersedes_old_polygon(self):
        rows=self.warning_rows();rows.loc[1,['issued_at','action','covers_start']]=['2020-01-01T00:25Z','CON',False]
        self.assertEqual(warning_summary(rows,self.cutoff,complete=True)['warning_active_tornado_count'],0)

    def test_exposure_deduplicates_tracts_and_retains_missing_context(self):
        tracts=pd.DataFrame([dict(tract_id='t1',acs_id='a1',area_fraction=.5,intersection_fraction=.1),
                             dict(tract_id='t2',acs_id='a2',area_fraction=.5,intersection_fraction=.2)])
        acs=pd.DataFrame([dict(record_id='a1',population=100,housing_units=40,mobile_home_units=2),
                          dict(record_id='a2',population=None,housing_units=60,mobile_home_units=3)])
        r=exposure_summary(pd.DataFrame(),pd.concat([tracts,tracts]),acs,nlcd_complete=False,census_complete=True)
        self.assertIsNone(r['exposure_population_estimate'])
        self.assertEqual(r['exposure_housing_estimate'],16)
        self.assertEqual(r['exposure_tract_count'],2)
        tracts.loc[1,'area_fraction']=.2
        self.assertIsNone(exposure_summary(pd.DataFrame(),tracts,acs,nlcd_complete=False,census_complete=True)['exposure_housing_estimate'])

    def test_missing_sources_preserve_all_rows_and_unknown_targets(self):
        from enrichment.pipeline import TABLES
        base=pd.DataFrame([dict(tornado_id='spc:2020:one',start_date=pd.Timestamp('2020-01-01'),start_time='00:00:00',
            end_date=pd.Timestamp('2020-01-01'),end_time='00:10:00',spc_timezone_code=9,
            start_longitude=-97,start_latitude=35,end_longitude=-96.9,end_latitude=35.1,
            year=2020,ef_rating=pd.NA,ef_rating_known=False,path_length_miles=2,path_width_yards=10,injuries=0,fatalities=0)])
        tables={name:pd.DataFrame({k:pd.Series(dtype='string') for k in keys}) for name,keys in TABLES.items()}
        tables['source_coverage']=pd.DataFrame(columns=['tornado_id','source','status'])
        tables['event_areas']=pd.DataFrame(columns=['tornado_id'])
        onset,retro=make_views(base,tables,asdict(Config()))
        self.assertEqual(len(onset),1);self.assertTrue(pd.isna(onset.iloc[0].target_ef_rating))
        self.assertFalse(any(x.startswith('post_') for x in onset))
        self.assertFalse(any(x.startswith('env_') for x in onset))
        self.assertIn('post_era5_cape_j_kg',retro)
        self.assertIn('post_path_width_yards',retro)
        spec=dictionary(onset,retro,asdict(Config()))
        self.assertNotIn('target_ef_rating',spec['onset_predictor_columns'])
        self.assertNotIn('feature_missing_count',spec['onset_predictor_columns'])
        self.assertFalse(any('status' in x or x.startswith('post_') for x in spec['onset_predictor_columns']))
        self.assertFalse(any('era5' in x for x in spec['onset_predictor_columns']))
        # The default release has no empty ERA5 table, status, or feature columns.
        tables.pop('era5_samples')
        onset,retro=make_views(base,tables,asdict(Config()))
        self.assertFalse(any('era5' in c for c in onset))
        self.assertFalse(any('era5' in c for c in retro))
        self.assertEqual(len(onset),len(base))


if __name__=='__main__':unittest.main()
