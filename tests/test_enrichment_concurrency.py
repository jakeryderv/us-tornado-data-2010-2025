"""Concurrency invariants: ownership, request coalescing, limits and deterministic output."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock
from urllib.error import HTTPError
from unittest.mock import patch
import io
import json
import time
import unittest

import pandas as pd

from enrichment.common import Cache, Config, BudgetExceeded, CacheLimitExceeded, digest, stable_id
from enrichment.pipeline import collect, DEFAULT_SOURCES, COLLECTORS
from enrichment.checkpoints import SharedRows, BatchedRows, Checkpoints, load


class Response(io.BytesIO):
    status = 200
    url = 'https://test.example/input'
    def __init__(self, body=b'12345678', on_close=lambda: None):
        super().__init__(body)
        self.headers = {'Content-Length':str(len(body))}
        self.on_close = on_close
    def read(self, n=-1):
        time.sleep(.005)
        return super().read(n)
    def __exit__(self, *args):
        self.on_close()
        self.close()


class ConcurrentCacheTests(unittest.TestCase):
    def test_duplicate_requests_share_one_transport_and_keep_separate_owners(self):
        with TemporaryDirectory() as d:
            cache = Cache(d,storage='compact',max_cache_bytes=8)
            owners = [cache.fork() for _ in range(3)]
            barrier = Barrier(3)
            def run(owner):
                barrier.wait()
                return owner.fetch('https://test.example/shared')
            with patch('enrichment.common.urlopen',side_effect=lambda *a,**k:Response()) as opened, ThreadPoolExecutor(3) as pool:
                results = list(pool.map(run, owners))
            self.assertEqual(opened.call_count,1)
            self.assertEqual(len(set(aid for _,aid in results)),1)
            self.assertTrue(all(owner.touched == owners[0].touched for owner in owners))
            path, aid = results[0]
            cache.commit([cache.assets[aid]],owner=owners[0])
            with patch('enrichment.common.urlopen',side_effect=lambda *a,**k:Response()):
                with self.assertRaises(CacheLimitExceeded):cache.fork().fetch('https://test.example/other')
            self.assertTrue(path.exists())
            for owner in owners[1:]:cache.commit([cache.assets[aid]],owner=owner)
            cache.finish()
            self.assertFalse(path.exists())

    def test_host_limit_and_byte_reservations_hold_across_workers(self):
        with TemporaryDirectory() as d:
            cache = Cache(d,max_bytes=18,per_host=2)
            guard=Lock();active=0;peak=0
            def close():
                nonlocal active
                with guard:active-=1
            def opened(*args,**kwargs):
                nonlocal active,peak
                with guard:
                    active+=1;peak=max(peak,active)
                return Response(b'123456',on_close=close)
            def run(i):
                owner=cache.fork()
                try:
                    path,aid=owner.fetch('https://test.example/'+str(i))
                    cache.commit([cache.assets[aid]],owner=owner)
                    return 'complete'
                except BudgetExceeded:return 'budget'
            with patch('enrichment.common.urlopen',side_effect=opened),ThreadPoolExecutor(4) as pool:
                results=list(pool.map(run,range(4)))
            self.assertEqual(results.count('complete'),3)
            self.assertEqual(results.count('budget'),1)
            self.assertEqual(cache.downloaded,18)
            self.assertEqual(cache._network_reserved,{})
            self.assertLessEqual(peak,2);self.assertGreater(peak,1)
            cache.finish()

    def test_decoded_shared_input_is_built_once_and_text_does_not_evict_it(self):
        with TemporaryDirectory() as d:
            cache=Cache(d,memo_items=2);built=[]
            def build():
                built.append(1);time.sleep(.02)
                return SharedRows([{'record_id':'a'}])
            with ThreadPoolExecutor(4) as pool:
                values=list(pool.map(lambda _:cache.memo(('warnings','day'),build),range(4)))
            self.assertEqual(len(built),1)
            self.assertTrue(all(v is values[0] for v in values))
            for i in range(300):cache.memo(('vtec',i),lambda:('NEW','date'))
            self.assertIs(cache.memo(('warnings','day'),build),values[0])

    def test_inflight_disk_reservation_prevents_oversubscription(self):
        with TemporaryDirectory() as d:
            cache=Cache(d,storage='cache',max_cache_bytes=8)
            started,release=Event(),Event()
            class SlowResponse(Response):
                def read(self,n=-1):
                    started.set()
                    if not release.wait(5):raise RuntimeError('test timed out')
                    return super().read(n)
            def opened(request,**kwargs):
                return SlowResponse() if request.full_url.endswith('first') else Response()
            with patch('enrichment.common.urlopen',side_effect=opened),ThreadPoolExecutor(2) as pool:
                first=pool.submit(cache.fork().fetch,'https://test.example/first')
                try:
                    self.assertTrue(started.wait(5))
                    with self.assertRaises(CacheLimitExceeded):
                        cache.fork().fetch('https://test.example/second')
                finally:release.set()
                self.assertTrue(first.result()[0].exists())
            self.assertEqual(cache._disk_reserved,{})
            self.assertEqual(sum(e['bytes'] for e in cache.entries.values()),8)
            self.assertFalse(list(Path(d).glob('*.part')))
            cache.finish()

    def test_retry_after_and_streaming_budget_do_not_leave_partial_files(self):
        with TemporaryDirectory() as d:
            cache=Cache(d,max_bytes=6)
            response=Response(b'12345678');response.headers={}
            error=HTTPError('https://test.example/input',429,'rate limited',{'Retry-After':'0'},None)
            with patch('enrichment.common.urlopen',side_effect=[error,response]):
                with self.assertRaises(BudgetExceeded):cache.fetch('https://test.example/input')
            self.assertEqual(cache.stats['retries'],1)
            self.assertEqual(cache.downloaded,6)
            self.assertEqual(cache._network_reserved,{})
            self.assertFalse(list(Path(d).glob('*.part')))
            self.assertFalse(cache.assets)
            cache.finish()

    def test_shared_checkpoint_background_and_event_overrides_roundtrip(self):
        with TemporaryDirectory() as d:
            root=Path(d);writer=Checkpoints(root)
            shared=SharedRows([dict(record_id='shared',value=None)])
            for i in range(3):
                rows=BatchedRows(shared,[dict(record_id='shared',value=i)])
                writer.save(root/f'{i}.json',dict(tables={'warning_updates':rows},assets=[],coverage={}))
            self.assertEqual(len(list((root/'job_tables').glob('*.gz'))),4)
            for i in range(3):
                result=load(root/f'{i}.json',root)
                self.assertEqual(list(result['tables']['warning_updates']),[dict(record_id='shared',value=None),dict(record_id='shared',value=i)])

    def test_legacy_inline_and_shared_checkpoints_remain_readable(self):
        with TemporaryDirectory() as d:
            root=Path(d);(root/'job_tables').mkdir()
            rows=[{'record_id':'legacy','value':12}]
            tables={'radar_detections':rows}
            saved=dict(tables=tables,tables_digest=stable_id('checkpoint-tables',tables))
            path=root/'legacy.json';path.write_text(json.dumps(saved))
            self.assertEqual(load(path,root)['tables'],tables)
            blob=root/'job_tables/legacy.json';blob.write_text(json.dumps(rows))
            saved.update(tables={},table_refs={'radar_detections':dict(path='job_tables/legacy.json',sha256=digest(blob))})
            path.write_text(json.dumps(saved))
            self.assertEqual(load(path,root)['tables'],tables)
            blob.write_text('[]')
            with self.assertRaisesRegex(ValueError,'Changed shared'):load(path,root)

    def test_parallel_pipeline_matches_serial_despite_reversed_finish_order(self):
        with TemporaryDirectory() as d,ExitStack() as stack:
            root=Path(d);data=root/'data';(data/'analysis').mkdir(parents=True)
            ids=['a','b','c','d']
            pd.DataFrame({'tornado_id':ids}).to_parquet(data/'analysis/tornadoes.parquet')
            events=[dict(tornado_id=v,year=2020,area_states=['01'],start_utc=None,
                area_wkt=None,latitude=35.,longitude=-97.,usable=True) for v in ids]
            def adapter(event,cache,config):
                path,aid=cache.fetch('https://test.example/shared')
                time.sleep((4-ids.index(event['tornado_id']))*.005)
                return {'radar_detections':[dict(record_id='shared',value=event['tornado_id'],asset_id=aid)]}
            stack.enter_context(patch('enrichment.pipeline.input_hashes',return_value={}))
            stack.enter_context(patch.dict(COLLECTORS,{k:adapter for k in DEFAULT_SOURCES}))
            stack.enter_context(patch('enrichment.common.urlopen',side_effect=lambda *a,**k:Response()))
            stack.enter_context(redirect_stdout(io.StringIO()))
            outputs=[]
            for workers in (1,4):
                out=root/str(workers)
                m=collect(data,out,events,list(DEFAULT_SOURCES),Config(),max_bytes=1000,timeout=1,
                          workers=workers,max_cache_bytes=16)
                self.assertEqual(m['status_counts'],{'complete':20})
                self.assertTrue(m['checkpoints_retired'])
                outputs.append({p['path']:pd.read_parquet(out/'tables'/p['path']) for p in m['outputs']})
            for name,frame in outputs[0].items():pd.testing.assert_frame_equal(frame,outputs[1][name])
            self.assertEqual(outputs[0]['radar_detections.parquet'].iloc[0]['value'],'d')


if __name__=='__main__':unittest.main()
