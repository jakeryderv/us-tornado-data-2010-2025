"""Build and verify identical, versioned data payloads for multiple dataset hosts.

No network access. Build requires a clean code commit and verified local sources.
"""
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.verify_downloads import verify as verify_sources


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def local(root, relative):
    item = PurePosixPath(relative)
    if item.is_absolute() or '..' in item.parts or '\\' in relative:
        raise ValueError(f'Unsafe release path: {relative}')
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError(f'Path escapes release: {relative}')
    return path


def active_files(data, start, end):
    """Select manifest-backed files and required discovery/schema provenance only."""
    data = Path(data)
    names = set()
    def add(relative, sidecar=False):
        p = local(data, relative)
        if not p.is_file() or p.is_symlink():
            raise ValueError(f'Missing release input: {relative}')
        names.add(relative)
        if sidecar:
            add(relative + '.metadata.json')
    for prefix in ['download_manifest', 'census_manifest', 'quality_summary', 'download_verification']:
        add(f'{prefix}_{start}_{end}.json')
    noaa = read_json(data / f'download_manifest_{start}_{end}.json')
    census = read_json(data / f'census_manifest_{start}_{end}.json')
    for folder in ['spc', 'ncei_storm_events']:
        add(f'{folder}/catalog.html', True)
    for filename in ['service.json', 'points_schema.json', 'lines_schema.json', 'polygons_schema.json']:
        add(f'nws_dat/{filename}', True)
    for output in noaa['outputs']:
        add(output['path'], output['source'] == 'SPC')
        if output['source'] == 'NCEI':
            add(output['input'], True)
        elif output['source'] == 'DAT':
            folder = PurePosixPath(output['path']).parent
            for name in ['object_ids.json', 'count.json']:
                add(str(folder / name), True)
            index = read_json(data / output['path'])
            for batch in index['batches']:
                add(str(folder / batch['file']), True)
    for entry in census['sources']:
        add(entry['path'], True)
    add(census['context']['path'])
    add(census['boundaries']['path'])
    return sorted(names)


def schema(data, start, end):
    paths = {'spc': data / f'spc/tornadoes_{start}_{end}.csv',
             'census_county_context': data / f'census_population/county_context_{start}_{end}.csv'}
    for table in ['details', 'fatalities', 'locations']:
        for year in range(start, end + 1):
            paths[f'ncei_{table}_{year}'] = data / f'ncei_storm_events/tornado/{year}_{table}.csv'
    tables = {}
    for name, path in paths.items():
        with path.open(encoding='utf-8-sig', newline='') as handle:
            tables[name] = {'path': str(path.relative_to(data)), 'columns': next(csv.reader(handle)),
                            'note': 'Source-native CSV fields; preserve identifiers as strings.'}
    dat = {name: read_json(data / f'nws_dat/{name}_schema.json')['fields'] for name in ['points', 'lines', 'polygons']}
    return {'csv_tables': tables, 'dat_source_fields': dat,
            'geometry': 'DAT and county GeoJSON coordinates are WGS84 longitude/latitude.'}


def source_doc(path, commit, repository):
    """Make repository-relative documentation links portable on both hosts."""
    def replace(match):
        link = match[1]
        if '://' in link or link.startswith('#'):
            return match[0]
        filename, _, fragment = link.partition('#')
        target = (path.parent / filename).resolve()
        if not target.is_relative_to(ROOT):
            raise ValueError(f'Unsafe documentation link: {link}')
        url = f'{repository}/blob/{commit}/{target.relative_to(ROOT).as_posix()}'
        return '](' + url + ('#' + fragment if fragment else '') + ')'
    return re.sub(r'\]\(([^\s)]+)\)', replace, path.read_text())


def verify_release(folder, expected_manifest_sha=None):
    folder = Path(folder).resolve()
    manifest_path = folder / 'release_manifest.json'
    if expected_manifest_sha and digest(manifest_path) != expected_manifest_sha:
        raise ValueError('Release manifest hash differs from the trusted release')
    manifest = read_json(manifest_path)
    entries = manifest['files']
    names = [entry['path'] for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate release file paths')
    for entry in entries:
        path = local(folder, entry['path'])
        if not path.is_file() or path.stat().st_size != entry['bytes'] or digest(path) != entry['sha256']:
            raise ValueError(f'Release file failed verification: {entry["path"]}')
    if sum(entry['bytes'] for entry in entries) != manifest['payload_bytes'] or len(entries) != manifest['file_count']:
        raise ValueError('Release totals do not match the inventory')
    expected_sums = {entry['path']: entry['sha256'] for entry in entries}
    expected_sums['release_manifest.json'] = digest(manifest_path)
    sums = {}
    for line in (folder / 'SHA256SUMS').read_text().splitlines():
        value, name = line.split('  ', 1)
        if name in sums:
            raise ValueError('Duplicate checksum entry')
        sums[name] = value
    if sums != expected_sums:
        raise ValueError('SHA256SUMS does not match the release inventory')
    return {'status': 'passed', 'version': manifest['version'], 'files': len(entries),
            'payload_bytes': manifest['payload_bytes'], 'manifest_sha256': digest(manifest_path)}


def build(data, output, config):
    data, output = Path(data).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError(f'Release destination already exists: {output}; choose a new version/destination')
    if output.is_relative_to(data) or data.is_relative_to(output):
        raise ValueError('Release staging must be separate from data/')
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip():
        raise ValueError('Commit the release code/docs first; a release must name a clean code revision')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    start, end = config['start_year'], config['end_year']
    if not re.fullmatch(r'v\d+\.\d+\.\d+', config['version']):
        raise ValueError('Use a version like v1.0.0')
    result = verify_sources(data, start, end)
    if result['status'] != 'passed':
        raise ValueError(f'Source verification failed: {result["errors"]}')
    # Require the report that travels with the collection to identify these manifests.
    saved = read_json(data / f'download_verification_{start}_{end}.json')
    if saved.get('status') != 'passed' or saved.get('scope') != 'all' or any(
            saved.get(key) != result[key] for key in ['manifest', 'census_manifest']):
        raise ValueError('Saved collection verification is stale or incomplete; verify the full collection first')
    names = active_files(data, start, end)
    payload = output / 'payload'
    payload.mkdir(parents=True)
    for name in names:
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(data / name, target)
    copied = verify_sources(payload, start, end)
    if copied['status'] != 'passed' or any(copied.get(k) != result.get(k) for k in ['manifest', 'census_manifest', 'counts']):
        raise ValueError('Copied release inputs do not match the verified source snapshot')
    repository = config['code_repository']
    for source, name in [('docs/DATASET_CARD.md', 'DATASET_CARD.md'), ('docs/DATASET.md', 'COLLECTION.md'),
                         ('docs/DATA_SOURCES.md', 'DATA_SOURCES.md')]:
        (payload / name).write_text(source_doc(ROOT / source, commit, repository), encoding='utf-8')
    shutil.copyfile(ROOT / 'LICENSE', payload / 'CODE_LICENSE.txt')
    write_json(payload / 'schema.json', schema(data, start, end))
    citation = '\n'.join(['cff-version: 1.2.0', 'type: dataset',
                          'message: "Cite this release and its original NOAA/Census sources."',
                          'title: ' + json.dumps(config['title']), 'version: ' + config['version'],
                          'authors:', '  - family-names: "Van Slyke"', '    given-names: "Jake"',
                          'repository-code: ' + repository, 'commit: ' + commit, ''])
    (payload / 'CITATION.cff').write_text(citation)
    entries = [dict(path=p.relative_to(payload).as_posix(), bytes=p.stat().st_size, sha256=digest(p))
               for p in sorted(payload.rglob('*')) if p.is_file()]
    noaa = read_json(data / f'download_manifest_{start}_{end}.json')
    census = read_json(data / f'census_manifest_{start}_{end}.json')
    manifest = dict(schema_version=1, slug=config['slug'], title=config['title'], version=config['version'],
                    built_at=datetime.now(timezone.utc).isoformat(), code_commit=commit, code_repository=repository,
                    start_year=start, end_year=end, data_license=config['data_license'], code_license=config['code_license'],
                    source_snapshots={'noaa_completed_at': noaa['completed_at'], 'census_completed_at': census['completed_at']},
                    source_verification=result, file_count=len(entries), payload_bytes=sum(x['bytes'] for x in entries), files=entries)
    write_json(payload / 'release_manifest.json', manifest)
    sums = [f'{entry["sha256"]}  {entry["path"]}' for entry in entries]
    sums.append(f'{digest(payload / "release_manifest.json")}  release_manifest.json')
    (payload / 'SHA256SUMS').write_text('\n'.join(sums) + '\n')
    verified = verify_release(payload)
    write_json(output / 'build_verification.json', verified)
    print(json.dumps(verified, indent=2))
    return payload


def stage(payload, destination, platform, owner, config):
    """Copy a verified common payload; platform metadata is outside its checksum set."""
    payload, destination = Path(payload).resolve(), Path(destination).resolve()
    if destination.is_relative_to(payload) or payload.is_relative_to(destination):
        raise ValueError('Platform staging must be separate from the payload')
    verify_release(payload)
    if destination.exists():
        raise ValueError('Platform staging directory already exists')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', owner):
        raise ValueError('Invalid account/organization name')
    manifest = read_json(payload / 'release_manifest.json')
    if manifest['slug'] != config['slug'] or manifest['version'] != config['version']:
        raise ValueError('Release config does not match payload')
    shutil.copytree(payload, destination)
    card = (payload / 'DATASET_CARD.md').read_text()
    summary = f'\nRelease **{manifest["version"]}**; {manifest["file_count"]:,} shared files; {manifest["payload_bytes"]:,} bytes.\n'
    if platform == 'huggingface':
        header = ('---\npretty_name: ' + json.dumps(config['title']) + '\nlanguage:\n- en\nlicense: other\n'
                  'license_name: us-government-works\nlicense_link: DATA_SOURCES.md\n'
                  'tags:\n- tornado\n- weather\n- geospatial\n- census\n- tabular\n'
                  'size_categories:\n- 10K<n<100K\nviewer: false\n---\n\n')
        (destination / 'README.md').write_text(header + card + summary)
    elif platform == 'kaggle':
        (destination / 'README.md').write_text(card + summary)
        write_json(destination / 'dataset-metadata.json', dict(title=config['title'], subtitle=config['subtitle'],
                   id=f'{owner}/{config["slug"]}', licenses=[{'name': config['data_license']}],
                   description=card + summary, keywords=['weather', 'geography']))
    else:
        raise ValueError('Unknown platform')
    return verify_release(destination, digest(payload / 'release_manifest.json'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    b.add_argument('--output', type=Path)
    v = sub.add_parser('verify')
    v.add_argument('folder', type=Path)
    v.add_argument('--manifest-sha256')
    s = sub.add_parser('stage')
    s.add_argument('platform', choices=['huggingface', 'kaggle'])
    s.add_argument('--owner', required=True)
    s.add_argument('--payload', type=Path, required=True)
    s.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    config = read_json(ROOT / 'release/dataset.json')
    if args.command == 'build':
        build(args.data_dir, args.output or ROOT / 'dist' / config['version'], config)
    elif args.command == 'verify':
        print(json.dumps(verify_release(args.folder, args.manifest_sha256), indent=2))
    else:
        print(json.dumps(stage(args.payload, args.output, args.platform, args.owner, config), indent=2))


if __name__ == '__main__':
    main()
