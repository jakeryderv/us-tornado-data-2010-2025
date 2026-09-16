"""Consolidating folders must preserve bytes, source history and download reuse."""
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import io
import json
import unittest

import pandas as pd

from enrichment.common import Config, atomic_json, digest
from enrichment.layout import table_directory, table_path, new_table_directory
from enrichment.pipeline import collect, COLLECTORS, DEFAULT_SOURCES, extraction_definition
from scripts.migrate_analysis_layout import migrate


class LayoutTests(unittest.TestCase):
    def test_legacy_paths_and_isolated_outputs(self):
        root=Path('/dataset')
        self.assertEqual(table_directory(root/'enrichment',{}),root/'enrichment/tables')
        self.assertEqual(new_table_directory(root,root/'enrichment'),'../analysis')
        self.assertEqual(new_table_directory(root,root/'benchmarks/pilot'),'analysis')
        with self.assertRaisesRegex(ValueError,'directory'):
            table_directory(root,{'table_directory':'../../elsewhere'})
        with self.assertRaisesRegex(ValueError,'filename'):
            table_path(root,{}, {'path':'../outside.parquet'})

    def test_migration_preserves_bytes_and_reuses_complete_snapshot_without_collectors(self):
        with TemporaryDirectory() as d, ExitStack() as stack:
            data=Path(d);out=data/'enrichment';analysis=data/'analysis';analysis.mkdir()
            pd.DataFrame({'tornado_id':['a']}).to_parquet(analysis/'tornadoes.parquet')
            backbone_hash=digest(analysis/'tornadoes.parquet')
            events=[dict(tornado_id='a',year=2020,area_states=['01'],start_utc=None,
                         area_wkt=None,latitude=35.,longitude=-97.,usable=True)]
            stack.enter_context(patch('enrichment.pipeline.input_hashes',return_value={}))
            stack.enter_context(redirect_stdout(io.StringIO()))
            old=extraction_definition(data,Config(),'batch');old['code']['pipeline.py']='previous-code'
            with patch.dict(COLLECTORS,{name:lambda *a:{} for name in DEFAULT_SOURCES}),patch('enrichment.pipeline.extraction_definition',return_value=old):
                manifest=collect(data,out,events,list(DEFAULT_SOURCES),Config(),max_bytes=1,timeout=1)
            # Reconstruct the legacy physical layout and retain its original manifest identity.
            (out/'tables').mkdir()
            for item in manifest['outputs']:(analysis/item['path']).rename(out/'tables'/item['path'])
            manifest.pop('table_directory');atomic_json(out/'manifest.json',manifest)
            old_hash=digest(out/'manifest.json')
            # No clobbering an independently existing destination, even on partial migration.
            conflict=analysis/manifest['outputs'][0]['path'];conflict.write_bytes(b'other data')
            with self.assertRaisesRegex(ValueError,'Conflicting destination'):migrate(data,out)
            self.assertEqual(digest(out/'manifest.json'),old_hash)
            self.assertTrue(all((out/'tables'/i['path']).exists() for i in manifest['outputs']))
            conflict.unlink()
            result=migrate(data,out)
            self.assertEqual(result['status'],'migrated')
            updated=json.loads((out/'manifest.json').read_text())
            self.assertEqual(updated['definition'],old)
            self.assertEqual(updated['layout_migration']['previous_manifest_sha256'],old_hash)
            self.assertEqual(digest(analysis/'tornadoes.parquet'),backbone_hash)
            self.assertFalse((out/'tables').exists())
            for item in manifest['outputs']:self.assertEqual(digest(analysis/item['path']),item['sha256'])
            self.assertEqual(migrate(data,out)['status'],'already_migrated')
            def forbidden(*a):raise AssertionError('Migration must not re-extract completed data')
            with patch.dict(COLLECTORS,{name:forbidden for name in DEFAULT_SOURCES}),patch('enrichment.common.urlopen',side_effect=AssertionError('No HTTP')):
                result=collect(data,out,events,list(DEFAULT_SOURCES),Config(),max_bytes=1,timeout=1)
            self.assertTrue(result['resumed_complete']);self.assertEqual(result['new_downloaded_bytes'],0)


if __name__=='__main__':unittest.main()
