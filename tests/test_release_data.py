"""Release integrity and host metadata checks; synthetic data, no network."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from scripts.release_data import digest, local, stage, verify_release, write_json


class ReleaseIntegrity(unittest.TestCase):
    def fixture(self, root):
        root.mkdir()
        (root/'DATASET_CARD.md').write_text('# Shared card\n')
        (root/'data.csv').write_text('county_fips,value\n01001,7\n')
        entries=[dict(path=p.name,bytes=p.stat().st_size,sha256=digest(p)) for p in sorted(root.iterdir())]
        write_json(root/'release_manifest.json',dict(version='v1.0.0',slug='test-dataset',files=entries,
                   file_count=len(entries),payload_bytes=sum(e['bytes'] for e in entries)))
        (root/'SHA256SUMS').write_text(''.join(f'{e["sha256"]}  {e["path"]}\n' for e in entries)+
                                     f'{digest(root/"release_manifest.json")}  release_manifest.json\n')
        return dict(version='v1.0.0',slug='test-dataset',title='Test Dataset',
                    subtitle='A test dataset with shared metadata',data_license='US-Government-Works')

    def test_both_hosts_have_identical_payload_and_license_mapping(self):
        with TemporaryDirectory() as d:
            base=Path(d);payload=base/'payload';config=self.fixture(payload)
            trusted=digest(payload/'release_manifest.json')
            for host in ['huggingface','kaggle']:
                with self.subTest(host=host):
                    stage(payload,base/host,host,'test-owner',config)
                    self.assertEqual(verify_release(base/host,trusted)['status'],'passed')
                    self.assertEqual((base/host/'DATASET_CARD.md').read_bytes(),(payload/'DATASET_CARD.md').read_bytes())
            kg=json.loads((base/'kaggle/dataset-metadata.json').read_text())
            self.assertEqual(kg['id'],'test-owner/test-dataset')
            self.assertEqual(kg['licenses'],[{'name':'US-Government-Works'}])
            self.assertIn('license: other', (base/'huggingface/README.md').read_text())
            self.assertIn('license_link: DATA_SOURCES.md', (base/'huggingface/README.md').read_text())

    def test_tampering_missing_files_and_wrong_release_are_rejected(self):
        with TemporaryDirectory() as d:
            root=Path(d)/'payload';self.fixture(root)
            with self.assertRaisesRegex(ValueError,'trusted release'):
                verify_release(root,'0'*64)
            (root/'data.csv').write_text('county_fips,value\n01001,8\n')
            with self.assertRaisesRegex(ValueError,'data.csv'):verify_release(root)
            (root/'data.csv').unlink()
            with self.assertRaisesRegex(ValueError,'data.csv'):verify_release(root)

    def test_paths_and_nested_staging_are_rejected(self):
        with TemporaryDirectory() as d:
            root=Path(d)/'payload';config=self.fixture(root)
            for name in ['../outside','/absolute','a/../../escape','..\\escape']:
                with self.assertRaises(ValueError):local(root,name)
            with self.assertRaisesRegex(ValueError,'separate'):
                stage(root,root/'nested','huggingface','test-owner',config)
            manifest=json.loads((root/'release_manifest.json').read_text())
            manifest['files'].append(manifest['files'][0])
            write_json(root/'release_manifest.json',manifest)
            with self.assertRaisesRegex(ValueError,'Duplicate'):verify_release(root)


if __name__=='__main__':unittest.main()
