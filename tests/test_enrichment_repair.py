"""Failure repairs must retain scientific windows, warning identity and old provenance."""
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile
import io
import json
import unittest
import warnings

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import box
from shapely.ops import transform

from enrichment.common import Cache, Config, stable_id
from enrichment.storage import verify_asset
from enrichment.land import nlcd_request_bounds, raster_summary
from enrichment.pipeline import collect, COLLECTORS
from enrichment.weather import resolve_warning_text


class RepairTests(unittest.TestCase):
    def test_refetched_optional_body_preserves_old_provenance_without_accepting_corruption(self):
        class Response(io.BytesIO):
            status=200
            url='https://test.example/archive'
            def __init__(self,body):
                super().__init__(body)
                self.headers={'Content-Length':str(len(body))}
        with TemporaryDirectory() as d:
            root=Path(d);cache=Cache(root,storage='compact')
            first=cache.fork()
            with patch('enrichment.common.urlopen',return_value=Response(b'old')):
                path,aid=first.fetch(Response.url)
            old=dict(cache.assets[aid],path=path.name)
            cache.commit([old],owner=first);cache.finish()
            second=cache.fork()
            with patch('enrichment.common.urlopen',return_value=Response(b'new')):
                path,newid=second.fetch(Response.url)
            self.assertNotEqual(aid,newid)
            self.assertEqual(verify_asset(root,old),'intentionally_not_retained')
            cache.commit([old])
            self.assertFalse(cache.entries[path]['evictable'])
            with self.assertRaisesRegex(ValueError,'differs'):
                verify_asset(root,dict(old,retention='required'))
            path.write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'differs'):verify_asset(root,old)
            with self.assertRaisesRegex(ValueError,'differs'):cache.commit([old])

    def test_padding_preserves_one_pixel_sampling_window(self):
        import rasterio
        from rasterio.transform import from_origin
        self.assertEqual(nlcd_request_bounds((30,30,60,60)),(0,0,90,90))
        self.assertEqual(nlcd_request_bounds((0,0,90,90)),(0,0,90,90))
        with TemporaryDirectory() as d:
            padded, single = Path(d)/'padded.tif', Path(d)/'single.tif'
            for path, values, grid in [(padded,np.array([[21,21,21],[21,82,21],[21,21,21]],dtype='uint8'),from_origin(0,90,30,30)),
                                      (single,np.array([[82]],dtype='uint8'),from_origin(30,60,30,30))]:
                with rasterio.open(path,'w',driver='GTiff',width=values.shape[1],height=values.shape[0],
                                   count=1,dtype='uint8',crs=5070,transform=grid,nodata=0) as dst:
                    dst.write(values,1)
            polygon=transform(Transformer.from_crs(5070,4326,always_xy=True).transform,box(30,30,60,60))
            self.assertEqual(raster_summary(padded,polygon,'land_cover',(30,30,60,60)),
                             raster_summary(single,polygon,'land_cover'))

    def test_duplicate_product_names_resolve_only_exact_vtec_and_retain_text(self):
        product='201205272313-KOAX-WWUS53-SVSOAX'
        wrong='WWUS53 KOAX 272313\nSVSOAX\n/O.CON.KOAX.SV.W.0155.000000T0000Z-120527T2330Z/\n'
        right=wrong.replace('SV.W.0155','TO.W.0016')
        with TemporaryDirectory() as d:
            path=Path(d)/'text.zip'
            class Cache:
                def memo(self,key,build):return build()
                def fetch(self,url,**kwargs):return path,'parent'
                def member(self,parent,name,text):
                    self.selected=(parent,name,text)
                    return None,'selected'
            cache=Cache()
            def archive(texts):
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore',UserWarning)
                    with ZipFile(path,'w') as z:
                        for text in texts:z.writestr('SVSOAX_201205272313.txt',text)
            with patch('enrichment.weather.warning_text',return_value=(wrong,'wrong')):
                archive([wrong,right,right])
                asset,state=resolve_warning_text(product,cache,('OAX','TO',16))
                self.assertEqual(asset,'selected');self.assertEqual(state[0],'CON')
                self.assertEqual(cache.selected[0],'parent');self.assertEqual(cache.selected[2],right)
                archive([wrong])
                with self.assertRaisesRegex(ValueError,'uniquely resolve'):
                    resolve_warning_text(product,cache,('OAX','TO',16))
                archive([right,right+'Different warning content'])
                with self.assertRaisesRegex(ValueError,'uniquely resolve'):
                    resolve_warning_text(product,cache,('OAX','TO',16))

    def test_explicit_repair_reuses_success_and_preserves_definition(self):
        with TemporaryDirectory() as d, ExitStack() as stack:
            root=Path(d);data=root/'data';out=root/'out'
            (data/'analysis').mkdir(parents=True)
            pd.DataFrame({'tornado_id':['a','b','c']}).to_parquet(data/'analysis/tornadoes.parquet')
            events=[dict(tornado_id=i,year=2020,area_states=['01'],start_utc=None,
                         area_wkt=None,latitude=35.,longitude=-97.,usable=True) for i in 'ab']
            def old(event,*args):
                if event['tornado_id']=='b':raise ValueError('temporary failure')
                return {'radar_detections':[dict(record_id='a',value=1)]}
            stack.enter_context(patch('enrichment.pipeline.input_hashes',return_value={}))
            stack.enter_context(redirect_stdout(io.StringIO()))
            with patch.dict(COLLECTORS,radar=old):
                original=collect(data,out,events,['radar'],Config(),max_bytes=1,timeout=1)
            # Simulate a different code version while keeping a valid definition/job identity.
            old_id=original['definition_id'];original['definition']['code']['weather.py']='older'
            new_old_id=stable_id('definition',original['definition']);original['definition_id']=new_old_id
            for event,j in zip(events,original['job_ids']):
                p=out/'jobs'/(j.replace(':','_')+'.json');saved=json.loads(p.read_text())
                nj=stable_id('job',[new_old_id,event['tornado_id'],'radar'])
                saved['coverage']['job_id']=nj
                (out/'jobs'/(nj.replace(':','_')+'.json')).write_text(json.dumps(saved));p.unlink()
            original['job_ids']=[stable_id('job',[new_old_id,e['tornado_id'],'radar']) for e in events]
            snapshot=root/'before.json';snapshot.write_text(json.dumps(original))
            (out/'manifest.json').write_text(json.dumps(original))
            calls=[]
            def fixed(event,*args):
                calls.append(event['tornado_id'])
                return {'radar_detections':[dict(record_id='b',value=2)]}
            with patch.dict(COLLECTORS,radar=fixed):
                result=collect(data,out,events,['radar'],Config(),max_bytes=1,timeout=1,repair_from=snapshot)
            self.assertEqual(calls,['b'])
            self.assertEqual(result['status_counts'],{'complete':2})
            coverage=pd.read_parquet(out/'analysis/source_coverage.parquet').set_index('tornado_id')
            self.assertEqual(coverage.loc['a','extraction_definition_id'],new_old_id)
            self.assertEqual(coverage.loc['b','extraction_definition_id'],result['definition_id'])
            self.assertIn(new_old_id,result['inherited_definitions'])
            with self.assertRaisesRegex(ValueError,'settings'):
                collect(data,out,events,['radar'],replace(Config(),radar_radius_km=30),max_bytes=1,timeout=1,repair_from=snapshot)
            with self.assertRaisesRegex(ValueError,'selection'):
                collect(data,out,events[:1],['radar'],Config(),max_bytes=1,timeout=1,repair_from=snapshot)


if __name__=='__main__':unittest.main()
