"""Retention, bounded disk use and resume after raw-source eviction."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from enrichment.common import Cache, CacheLimitExceeded, Config, digest, stable_id
from enrichment.pipeline import COLLECTORS, DEFAULT_SOURCES, collect
from enrichment.storage import verify_asset


class Response(io.BytesIO):
    status=200
    url='https://example.test/source'
    def __init__(self,body=b'12345678'):
        super().__init__(body);self.headers={'Content-Length':str(len(body))}


class RetentionTests(unittest.TestCase):
    def test_release_readiness_rejects_a_successful_pilot(self):
        from enrichment.verify import verify
        with TemporaryDirectory() as d:
            root=Path(d)
            (root/'manifest.json').write_text(json.dumps(dict(status='complete',event_count=4,
                full_backbone_count=20164,requested_sources=list(DEFAULT_SOURCES))))
            with self.assertRaisesRegex(ValueError,'pilot'):
                verify(root,root,root,require_full=True)

    def test_only_committed_bodies_can_be_evicted(self):
        with TemporaryDirectory() as d:
            cache=Cache(d,storage='compact',max_cache_bytes=8)
            with patch('enrichment.common.urlopen',side_effect=lambda *a,**k:Response()):
                first,asset=cache.fetch('https://example.test/first')
                with self.assertRaises(CacheLimitExceeded):cache.fetch('https://example.test/second')
                self.assertTrue(first.exists())
                cache.commit([cache.assets[asset]])
                second,asset2=cache.fetch('https://example.test/second')
                self.assertFalse(first.exists());self.assertTrue(second.exists())
                cache.finish();self.assertTrue(second.exists())
                cache.commit([cache.assets[asset2]])
                cache.finish();self.assertFalse(second.exists())
                self.assertTrue(first.with_suffix('.bin.json').exists())

    def test_archive_and_cache_modes_and_required_text(self):
        with TemporaryDirectory() as d:
            for mode in ['archive','cache','compact']:
                cache=Cache(Path(d)/mode,storage=mode,max_cache_bytes=8)
                with patch('enrichment.common.urlopen',side_effect=lambda *a,**k:Response()):
                    binary,aid=cache.fetch('https://example.test/binary')
                    text,tid=cache.fetch('https://example.test/warning',suffix='.txt')
                cache.commit([cache.assets[aid],cache.assets[tid]])
                cache.finish()
                self.assertEqual(binary.exists(),mode!='compact')
                self.assertTrue(text.exists())
                self.assertEqual(cache.assets[tid]['retention'],'required')

    def test_verifier_distinguishes_intentionally_absent_and_corrupt(self):
        with TemporaryDirectory() as d:
            root=Path(d);p=root/'body';p.write_bytes(b'valid')
            asset=dict(path='body',bytes=5,sha256=digest(p),asset_id='one',retention='optional')
            self.assertEqual(verify_asset(root,asset),'bytes_verified')
            p.write_bytes(b'wrong')
            with self.assertRaisesRegex(ValueError,'differs'):verify_asset(root,asset)
            p.unlink();self.assertEqual(verify_asset(root,asset),'intentionally_not_retained')
            asset['retention']='required'
            with self.assertRaisesRegex(ValueError,'missing'):verify_asset(root,asset)

    def test_partial_resume_only_retries_failed_job_then_retires_checkpoints(self):
        with TemporaryDirectory() as d, ExitStack() as stack:
            root=Path(d);data=root/'data';out=data/'enrichment'
            (data/'analysis').mkdir(parents=True)
            pd.DataFrame({'tornado_id':['a','b']}).to_parquet(data/'analysis/tornadoes.parquet')
            events=[dict(tornado_id=v,year=2020,area_states=['01'],start_utc=None,
                         area_wkt=None,latitude=35.,longitude=-97.,usable=True) for v in ['a','b']]
            calls=[]
            def adapter(name):
                def run(event,cache,config):
                    calls.append((name,event['tornado_id']))
                    if len(calls)==3:raise RuntimeError('simulated transient failure')
                    _,aid=cache.fetch('https://example.test/'+name+'/'+event['tornado_id'])
                    return {'radar_detections':[dict(record_id=stable_id('sample',calls[-1]),
                            tornado_id=event['tornado_id'],asset_id=aid)]}
                return run
            stack.enter_context(patch('enrichment.pipeline.input_hashes',return_value={}))
            stack.enter_context(patch.dict(COLLECTORS,{name:adapter(name) for name in COLLECTORS}))
            stack.enter_context(patch('enrichment.common.urlopen',side_effect=lambda *a,**k:Response()))
            stack.enter_context(redirect_stdout(io.StringIO()))
            first=collect(data,out,events,list(DEFAULT_SOURCES),Config(),max_bytes=1000,timeout=1,max_cache_bytes=16)
            self.assertEqual(first['status'],'partial');self.assertEqual(len(calls),10)
            self.assertNotIn('era5_samples.parquet',[v['path'] for v in first['outputs']])
            self.assertFalse(any(name=='era5' for name,_ in calls))
            self.assertTrue(list((out/'jobs').glob('job_*.json')))
            self.assertFalse(list((out/'raw').glob('*.bin')))
            second=collect(data,out,events,list(DEFAULT_SOURCES),Config(),max_bytes=1000,timeout=1,max_cache_bytes=16)
            self.assertEqual(second['status'],'complete');self.assertEqual(len(calls),11)
            self.assertEqual(second['downloaded_bytes'],8)
            self.assertFalse(list((out/'jobs').glob('job_*.json')))
            self.assertFalse(list((out/'job_tables').glob('*.gz')))
            with patch('enrichment.common.urlopen',side_effect=AssertionError('unexpected network')):
                third=collect(data,out,events,list(DEFAULT_SOURCES),Config(),max_bytes=0,timeout=1,max_cache_bytes=16)
            self.assertTrue(third['resumed_complete']);self.assertEqual(len(calls),11)
            (out/'tables/radar_detections.parquet').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'table changed'):
                collect(data,out,events,list(DEFAULT_SOURCES),Config(),max_bytes=0,timeout=1,max_cache_bytes=16)


if __name__=='__main__':unittest.main()
