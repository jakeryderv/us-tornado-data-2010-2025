"""Release integrity and host metadata checks; synthetic data, no network."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import gzip
import unittest
import zipfile

from scripts.release_data import digest, local, stage, unpack, verify_release, write_json


class ReleaseIntegrity(unittest.TestCase):
    def fixture(self, root):
        root.mkdir()
        (root/'DATASET_CARD.md').write_text('# Shared card\n')
        (root/'data.csv').write_text('county_fips,value\n01001,7\n')
        (root/'source.csv.gz').write_bytes(gzip.compress(b'value\n7\n', mtime=0))
        with zipfile.ZipFile(root/'source.zip', 'w') as zipped:
            zipped.writestr('source.txt', 'original archive bytes')
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
                    restored = base/host
                    if host == 'kaggle':
                        restored = base/'restored'
                        result = unpack(base/host/'release.zip.bin', restored, trusted)
                        self.assertEqual(result['status'], 'passed')
                    self.assertEqual(verify_release(restored,trusted)['status'],'passed')
                    self.assertEqual((base/host/'DATASET_CARD.md').read_bytes(),(payload/'DATASET_CARD.md').read_bytes())
            kg=json.loads((base/'kaggle/dataset-metadata.json').read_text())
            self.assertEqual(kg['id'],'test-owner/test-dataset')
            self.assertEqual(kg['licenses'],[{'name':'US-Government-Works'}])
            self.assertIn('license: other', (base/'huggingface/README.md').read_text())
            self.assertIn('license_link: https://huggingface.co/datasets/test-owner/test-dataset/blob/main/DATA_SOURCES.md',
                          (base/'huggingface/README.md').read_text())

    def test_archive_integrity_and_unsafe_members(self):
        with TemporaryDirectory() as d:
            base = Path(d); payload = base/'payload'; config = self.fixture(payload)
            staged = stage(payload, base/'kaggle', 'kaggle', 'test-owner', config)
            archive = base/'kaggle/release.zip.bin'
            with self.assertRaisesRegex(ValueError, 'trusted archive'):
                unpack(archive, base/'wrong', staged['manifest_sha256'], '0'*64)
            with zipfile.ZipFile(base/'unsafe.zip', 'w') as zipped:
                zipped.writestr('../outside', 'invalid')
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                unpack(base/'unsafe.zip', base/'unsafe', staged['manifest_sha256'])
            self.assertFalse((base/'unsafe').exists())

    def test_huggingface_cache_file_symlinks_are_verified(self):
        with TemporaryDirectory() as d:
            base = Path(d); payload = base/'blobs'; self.fixture(payload)
            cache = base/'snapshot'; cache.mkdir()
            for source in payload.iterdir():
                (cache/source.name).symlink_to(source)
            self.assertEqual(verify_release(cache, digest(payload/'release_manifest.json'))['status'], 'passed')
            (payload/'data.csv').write_text('corrupt cache blob')
            with self.assertRaisesRegex(ValueError, 'data.csv'):
                verify_release(cache)
            with self.assertRaisesRegex(ValueError, 'escapes release'):
                local(cache, 'data.csv')  # Writing/build/extraction paths stay strict.

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
