"""Acquisition partitions must preserve event semantics and compact retention."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile
import io
import unittest

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from requests.exceptions import ReadTimeout
from shapely.geometry import Point, box
from shapely.ops import transform

from enrichment.batching import radar_plans, nlcd_plans, warning_partition, warning_plans
from enrichment.common import Cache, Config, stable_id
from enrichment.land import raster_summary
from enrichment.weather import (radar_query, text_members, text_partition, warning_day,
                                warning_frame, warning_text)


class Response(io.BytesIO):
    status = 200
    url = 'https://test.example/data'
    def __init__(self, body):
        super().__init__(body)
        self.headers = {'Content-Length': str(len(body))}


class BatchingTests(unittest.TestCase):
    def test_month_boundary_includes_previous_day_and_reconstructs_query(self):
        start = pd.Timestamp('2020-02-01T00:10Z')
        self.assertEqual(warning_partition(start), (pd.Timestamp('2020-01-31T00:00Z'), pd.Timestamp('2020-03-01T00:00Z')))
        rows = []
        for i, (issued, poly) in enumerate([('202001302359','202001302359'),
                ('202001312355','202001312355'), (None,'202002010005'), ('202002020000','202002020000')]):
            rows.append(dict(PROD_ID=f'{poly}-KOUN-WFUS54-TOROUN', WFO='OUN', PHENOM='TO', ETN=i,
                             VTEC_YR=2020, ISSUED=issued, INIT_ISS=issued, POLY_BEG=poly, geometry=box(-98,34,-96,36)))
        frame = gpd.GeoDataFrame(rows, crs=4326)
        result = warning_day(warning_frame(frame, 'asset:a'), pd.Timestamp('2020-01-31T00:00Z'), pd.Timestamp('2020-02-02T00:00Z'))
        self.assertEqual(len(result[0]),2)
        self.assertEqual(set(result[3].query(Point(-97,35), predicate='covered_by')), {0,1})
        self.assertEqual({r['product_id'] for r in result[0]}, {rows[i]['PROD_ID'] for i in (1,2)})

    def test_shared_nlcd_window_has_identical_values_and_transform(self):
        import rasterio
        from rasterio.transform import from_origin
        with TemporaryDirectory() as d:
            data = np.array([[21,22,41,0],[23,24,82,11],[52,71,95,90],[41,42,43,31]],dtype='uint8')
            full, crop = Path(d)/'full.tif', Path(d)/'crop.tif'
            for path, values, grid in [(full,data,from_origin(0,120,30,30)),
                                      (crop,data[1:3,1:3],from_origin(30,90,30,30))]:
                with rasterio.open(path,'w',driver='GTiff',width=values.shape[1],height=values.shape[0],
                        count=1,dtype='uint8',crs=5070,transform=grid,nodata=0) as dst:
                    dst.write(values,1)
            polygon = transform(Transformer.from_crs(5070,4326,always_xy=True).transform, box(30,30,90,90))
            for product in ('land_cover','impervious'):
                self.assertEqual(raster_summary(full,polygon,product,(30,30,90,90)), raster_summary(crop,polygon,product))
            with self.assertRaisesRegex(ValueError,'aligned'):
                raster_summary(full,polygon,'land_cover',(31,30,91,90))
            with self.assertRaisesRegex(ValueError,'complete event'):
                raster_summary(crop,polygon,'land_cover',(0,0,90,90))

    def test_planners_share_nearby_events_without_clipping(self):
        events = [dict(tornado_id=str(i),start_utc=f'2020-05-01T00:{i}0:00Z',year=2020,
                       longitude=-97,latitude=35,usable=True,area_wkt=box(-97,35,-96.99,35.01).wkt)
                  for i in range(2)]
        self.assertEqual(len(set(radar_plans(events,Config()).values())),1)
        self.assertEqual(len(set(nlcd_plans(events,Config()).values())),1)
        events.append(dict(events[0], tornado_id='far', longitude=-80, area_wkt=box(-80,35,-79,36).wkt))
        self.assertEqual(len(set(nlcd_plans(events,Config()).values())),2)

    def test_sparse_warning_month_keeps_small_queries(self):
        events=[dict(tornado_id=str(i),start_utc=f'2020-05-0{i+1}T00:00Z',usable=True) for i in range(3)]
        plans, dates, text_days=warning_plans(events[:1])
        self.assertEqual((plans['0'][1]-plans['0'][0]).days,2)
        self.assertEqual(text_days,set())
        plans, dates, text_days=warning_plans(events)
        self.assertEqual(len(set(plans.values())),1)
        self.assertEqual((plans['0'][1]-plans['0'][0]).days,32)
        self.assertIn('20200430',dates)

    def test_capped_warning_text_partition_splits_without_missing_boundary(self):
        class Source:
            calls=[]
            def fetch(self,url,**kwargs):
                self.calls.append(url)
                return Path(str(len(self.calls))),str(len(self.calls))
            def memo(self,key,build):return build()
        cache=Source()
        with patch('enrichment.weather.text_members',side_effect=[None,{'a':'text'},{'b':'text'}]):
            result=text_partition(cache,'TOR',pd.Timestamp('2020-05-01T00:00Z'),pd.Timestamp('2020-05-02T00:00Z'))
        self.assertEqual(len(result),2)
        self.assertIn('edate=2020-05-01T12%3A00Z',cache.calls[1])
        self.assertIn('sdate=2020-05-01T12%3A00Z',cache.calls[2])

    def test_capped_radar_is_split_and_malformed_counts_fail(self):
        raw=dict(SHAPE='POINT (-97 35)',ZTIME='2020-01-01T00:30:00Z',WSR_ID='KTLX',CELL_ID='A1')
        class Source:
            def json(self, url):
                self.calls.append(url)
                return (dict(result=[raw],summary={'count':10000 if len(self.calls)==1 else 1}),stable_id('asset',url))
            def memo(self, key, build): return build()
            calls=[]
        cache=Source()
        result=radar_query(cache,'nx3tvs',pd.Timestamp('2020-01-01T00:00Z'),pd.Timestamp('2020-01-01T01:00Z'),(-98,34,-96,36))
        self.assertEqual(len(cache.calls),3)
        self.assertEqual(len(result),2)
        self.assertEqual(result[0][0][0]['record_id'],result[1][0][0]['record_id'])
        with patch.object(cache,'json',return_value=({'result':[raw],'summary':{'count':0}},'asset')):
            with self.assertRaisesRegex(ValueError,'count'):
                radar_query(cache,'nx3tvs',pd.Timestamp('2020-01-01'),pd.Timestamp('2020-01-02'),(-98,34,-96,36))

    def test_bulk_warning_text_retains_only_selected_member_with_parent_provenance(self):
        body=io.BytesIO()
        text='001 \nWFUS54 KOUN 010010\nTOROUN\n/O.NEW.KOUN.TO.W.0001.200501T0010Z-200501T0100Z/\n'
        with ZipFile(body,'w') as archive:
            archive.writestr('TOROUN_202005010010.txt',text)
        with TemporaryDirectory() as d:
            cache=Cache(d,storage='compact');cache.acquisition='batch';owner=cache.fork()
            with patch('enrichment.common.urlopen',return_value=Response(body.getvalue())) as opened:
                result,aid=warning_text('202005010010-KOUN-WFUS54-TOROUN',owner)
            self.assertEqual(result,text)
            self.assertEqual(opened.call_count,1)
            self.assertIn('pil=TOR&',opened.call_args.args[0].full_url)
            meta=cache.assets[aid]
            self.assertEqual(meta['retention'],'required')
            parent=meta['request_metadata']['container_asset_id']
            self.assertIn(parent,owner.touched)
            self.assertEqual(meta['request_metadata']['container_sha256'],cache.assets[parent]['sha256'])
            cache.commit([cache.assets[a] for a in owner.touched],owner=owner);cache.finish()
            self.assertFalse(Path(cache.assets[parent]['path']).exists())
            self.assertEqual(Path(meta['path']).read_text(),text)

    def test_duplicate_zip_member_is_ambiguous_and_capped_zip_is_not_complete(self):
        import warnings
        with TemporaryDirectory() as d:
            path=Path(d)/'text.zip'
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',UserWarning)
                with ZipFile(path,'w') as z:
                    for _ in range(2):z.writestr('TOROUN_202005010010.txt','WFUS54 KOUN 010010\nTOROUN\n')
            self.assertIsNone(text_members(path)['202005010010-KOUN-WFUS54-TOROUN'])
            with ZipFile(path,'w') as z:
                for i in range(9999):z.writestr(str(i),'')
            self.assertIsNone(text_members(path))

    def test_timeout_retry_cleans_reservations_and_preserves_budget(self):
        with TemporaryDirectory() as d:
            cache=Cache(d,max_bytes=10,storage='compact',max_cache_bytes=10)
            with patch('enrichment.common.urlopen',side_effect=[ReadTimeout(),Response(b'abc')]), patch('enrichment.cache.time.sleep'):
                path,aid=cache.fetch('https://test.example/data')
            self.assertEqual(path.read_bytes(),b'abc')
            self.assertEqual(cache.stats['retries'],1)
            self.assertEqual(cache.downloaded,3)
            self.assertEqual(cache._network_reserved,{})
            self.assertEqual(cache._disk_reserved,{})
            self.assertFalse(list(Path(d).glob('*.part')))
            cache.commit([cache.assets[aid]]);cache.finish()


if __name__ == '__main__':
    unittest.main()
