"""Move completed enrichment tables into analysis without changing extracted data.

Only use for the reviewed directory-layout refactor. Rebuild the ML views and
run enrichment.verify afterward to bind them to the updated source manifest.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil

from enrichment.common import Config, atomic_json, digest, now, stable_id
from enrichment.layout import new_table_directory, table_directory, table_path
from enrichment.pipeline import ROOT, extraction_definition
from enrichment.storage import verify_asset


def migrate(data, output):
    data, output = Path(data), Path(output)
    with (output/'collection.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another collector is using this output directory') from None
        path=output/'manifest.json'
        manifest=json.loads(path.read_text())
        if manifest['status']!='complete':
            raise ValueError('Finish the collection before migrating its layout')
        relative=new_table_directory(data,output)
        destination=output/relative
        old_directory=table_directory(output,manifest)
        files=[(table_path(output,manifest,item),destination/item['path'],item) for item in manifest['outputs']]
        reserved={'tornadoes','storm_events','storm_fatalities','storm_locations','tornado_footprints',
                  'county_context','county_boundaries','source_crosswalk','tornado_counties'}
        for source,target,item in files:
            if target.stem in reserved or target.suffix!='.parquet':
                raise ValueError('Migration cannot overwrite a backbone or metadata file')
            # Also permits resuming after the manifest commit but before unlinking.
            if not source.is_file() or digest(source)!=item['sha256']:
                raise ValueError(f'Source table differs: {source}')
            if target.exists() and digest(target)!=item['sha256']:
                raise ValueError(f'Conflicting destination table: {target}')
        for asset in manifest['assets']:
            verify_asset(output,asset)
        if old_directory.resolve()==destination.resolve():
            # Finish cleanup from an interrupted migration after its manifest commit.
            legacy=output/'tables'
            for _,target,item in files:
                old=legacy/item['path']
                if old.exists():
                    if digest(old)!=item['sha256']:raise ValueError('Conflicting legacy table')
                    old.unlink()
            if legacy.exists() and not any(legacy.iterdir()):legacy.rmdir()
            return dict(status='already_migrated',tables=len(files))
        definition=extraction_definition(data,Config(**manifest['definition']['config']),manifest['definition']['acquisition'])
        if any(definition[k]!=manifest['definition'][k] for k in ('config','backbone','acquisition')):
            raise ValueError('Migration cannot change scientific settings or backbone')
        previous_hash=digest(path)
        destination.mkdir(parents=True,exist_ok=True)
        # Publish verified copies first, commit the manifest, then remove old names.
        for source,target,item in files:
            if not target.exists():
                try:os.link(source,target)
                except OSError:
                    temp=target.with_suffix('.migration.tmp')
                    shutil.copyfile(source,temp)
                    if digest(temp)!=item['sha256']:raise ValueError('Migration copy differs')
                    os.replace(temp,target)
        manifest['table_directory']=relative
        manifest['layout_migration']=dict(migrated_at=now(),previous_manifest_sha256=previous_hash,
            previous_table_directory=str(old_directory.relative_to(output)),
            reusable_definition=definition,reusable_definition_id=stable_id('definition',definition),
            reason='Reviewed table-path refactor only; original extraction definitions and table bytes retained')
        atomic_json(path,manifest)
        for source,_,_ in files:source.unlink()
        if not any(old_directory.iterdir()):old_directory.rmdir()
        return dict(status='migrated',tables=len(files),table_directory=relative,
                    manifest_sha256=digest(path),previous_manifest_sha256=previous_hash)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    parser.add_argument('--enrichment-dir',type=Path)
    args=parser.parse_args()
    print(json.dumps(migrate(args.data_dir,args.enrichment_dir or args.data_dir/'enrichment'),indent=2))


if __name__=='__main__':main()
