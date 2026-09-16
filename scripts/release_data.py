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
import zipfile

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


def local(root, relative, *, allow_file_symlink=False):
    item = PurePosixPath(relative)
    if item.is_absolute() or '..' in item.parts or '\\' in relative:
        raise ValueError(f'Unsafe release path: {relative}')
    path = Path(root) / relative
    # HF snapshot caches link individual files to a sibling blob store. Permit
    # those read-only verification inputs; parent directories must stay inside.
    if allow_file_symlink and path.is_symlink() and path.parent.resolve().is_relative_to(Path(root).resolve()):
        return path
    path = path.resolve()
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
    for entry in [noaa['footprints']['inventory'], *noaa['footprints']['documents']]:
        add(entry['path'], True)
    for output in noaa['outputs']:
        add(output['path'], output['source'] in ('SPC', 'EFC'))
        if output['source'] == 'NCEI':
            add(output['input'], True)
    for entry in census['sources']:
        add(entry['path'], True)
    add(census['context']['path'])
    add(census['boundaries']['path'])
    return sorted(names)


def expanded_files(data):
    """Manifest-listed tables/metadata only; never publish checkpoint/cache trees."""
    from enrichment.layout import table_path
    names={'analysis/manifest.json','enrichment/manifest.json','ml/manifest.json','ml/feature_dictionary.json'}
    for entry in read_json(data/'analysis/manifest.json')['files']:
        names.add('analysis/'+entry['path'])
    enrichment_manifest=read_json(data/'enrichment/manifest.json')
    for entry in enrichment_manifest['outputs']:
        names.add(table_path(data/'enrichment',enrichment_manifest,entry).resolve().relative_to(data.resolve()).as_posix())
    for entry in read_json(data/'ml/manifest.json')['files']:
        names.add('ml/'+entry['path'])
    for name in names:
        if not local(data,name).is_file():raise ValueError(f'Missing expanded release file: {name}')
    return sorted(names)


def supporting_assets(manifest):
    result={}
    for asset in manifest['assets']:
        if asset.get('retention')!='required':continue
        name=asset['path']
        if name in result and result[name]['sha256']!=asset['sha256']:
            raise ValueError('Conflicting required asset versions')
        result[name]=asset
    return result


def bundle_support(data, payload):
    """Bundle retained warning texts/schema documents, keeping original paths/hashes."""
    assets=supporting_assets(read_json(data/'enrichment/manifest.json'))
    archive=payload/'enrichment/supporting_assets.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,asset in sorted(assets.items()):
            path=local(data/'enrichment',name)
            if digest(path)!=asset['sha256']:raise ValueError('Supporting asset changed')
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16
            z.writestr(info,path.read_bytes())
    verify_support(payload)
    return archive


def verify_support(folder):
    assets=supporting_assets(read_json(folder/'enrichment/manifest.json'))
    with zipfile.ZipFile(folder/'enrichment/supporting_assets.zip') as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or set(names)!=set(assets):raise ValueError('Supporting archive inventory differs')
        for name,asset in assets.items():
            local(folder/'enrichment',name)
            body=z.read(name)
            if len(body)!=asset['bytes'] or hashlib.sha256(body).hexdigest()!=asset['sha256']:
                raise ValueError('Supporting archive member differs')
    return len(assets)


def restore_support(folder):
    """Optional: expand provenance bytes for the standalone enrichment verifier."""
    folder=Path(folder).resolve()
    verify_release(folder);count=verify_support(folder)
    with zipfile.ZipFile(folder/'enrichment/supporting_assets.zip') as z:
        for info in z.infolist():
            target=local(folder/'enrichment',info.filename)
            if target.exists() and digest(target)!=hashlib.sha256(z.read(info)).hexdigest():
                raise ValueError('Existing supporting file differs')
        for info in z.infolist():
            target=local(folder/'enrichment',info.filename);target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():target.write_bytes(z.read(info))
    return dict(status='passed',restored_supporting_assets=count)


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
    from scripts.build_analysis import EFC_TYPES
    result = {'csv_tables': tables, 'efc_source_fields': EFC_TYPES,
              'geometry': 'Footprint and county GeoJSON coordinates are WGS84 longitude/latitude.'}
    if (data / 'analysis/manifest.json').is_file():
        result['analysis_tables'] = read_json(data / 'analysis/manifest.json')['files']
    if (data/'enrichment/manifest.json').exists():
        result['additional_analysis_tables']=read_json(data/'enrichment/manifest.json')['outputs']
        result['ml_tables']=read_json(data/'ml/manifest.json')['files']
        result['feature_dictionary']='ml/feature_dictionary.json'
    return result


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
        path = local(folder, entry['path'], allow_file_symlink=True)
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


def build(data, output, config, *, backbone_only=False):
    data, output = Path(data).resolve(), Path(output).resolve()
    expanded=bool(config.get('include_enrichment'))
    if expanded and backbone_only:raise ValueError('This release advertises enrichment; cannot build backbone only')
    if (data/'enrichment/manifest.json').exists() and not expanded and not backbone_only:
        raise ValueError('Choose expanded packaging or explicitly request backbone only')
    if expanded and not (data/'enrichment/manifest.json').exists():
        raise ValueError('Full enrichment is required for this release')
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
    enrichment_verification=None
    if expanded:
        from enrichment.verify import verify as verify_enrichment
        enrichment_verification=verify_enrichment(data,data/'enrichment',data/'ml',require_full=True)
        names=sorted(set(names)|set(expanded_files(data)))
    payload = output / 'payload'
    payload.mkdir(parents=True)
    for name in names:
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(data / name, target)
    copied = verify_sources(payload, start, end)
    if copied['status'] != 'passed' or any(copied.get(k) != result.get(k) for k in ['manifest', 'census_manifest', 'counts']):
        raise ValueError('Copied release inputs do not match the verified source snapshot')
    if expanded:
        bundle_support(data,payload)
        write_json(payload/'enrichment/verification.json',enrichment_verification)
        from scripts.research_report import build_report
        from scripts.source_registry import build_registry
        build_report(payload,payload/'research')
        write_json(payload/'source_registry.json',build_registry(payload,ROOT/'release/sources.json'))
    else:
        from scripts.build_analysis import build_analysis
        build_analysis(payload, start=start, end=end)
    repository = config['code_repository']
    for source, name in [('docs/DATASET_CARD.md', 'DATASET_CARD.md'), ('docs/DATASET.md', 'COLLECTION.md'),
                         ('docs/DATA_SOURCES.md', 'DATA_SOURCES.md'), ('docs/ANALYSIS.md', 'ANALYSIS.md'),
                         ('docs/LINKAGE.md','LINKAGE.md'),('docs/ENRICHMENT.md','ENRICHMENT.md'),
                         ('docs/RESEARCH_READINESS.md','RESEARCH_READINESS.md')]:
        (payload / name).write_text(source_doc(ROOT / source, commit, repository), encoding='utf-8')
    shutil.copyfile(ROOT / 'LICENSE', payload / 'CODE_LICENSE.txt')
    write_json(payload / 'schema.json', schema(payload, start, end))
    citation = '\n'.join(['cff-version: 1.2.0', 'type: dataset',
                          'message: "Cite this release and the original products used; see source_registry.json for provider citations and terms."',
                          'title: ' + json.dumps(config['title']), 'version: ' + config['version'],
                          'date-released: ' + datetime.now(timezone.utc).date().isoformat(),
                          'url: https://huggingface.co/datasets/jakeryderv/' + config['slug'],
                          'license-url: ' + repository + '/blob/' + commit + '/docs/DATA_SOURCES.md',
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
                    source_verification=result, enrichment_verification=enrichment_verification, file_count=len(entries), payload_bytes=sum(x['bytes'] for x in entries), files=entries)
    write_json(payload / 'release_manifest.json', manifest)
    sums = [f'{entry["sha256"]}  {entry["path"]}' for entry in entries]
    sums.append(f'{digest(payload / "release_manifest.json")}  release_manifest.json')
    (payload / 'SHA256SUMS').write_text('\n'.join(sums) + '\n')
    verified = verify_release(payload)
    write_json(output / 'build_verification.json', verified)
    print(json.dumps(verified, indent=2))
    return payload


def platform_card(card, platform):
    """Same factual card, with the host's own quick-start example."""
    return re.sub(r'<!-- platform:(huggingface|kaggle) -->(.*?)<!-- /platform -->',
                  lambda match: match[2] if match[1]==platform else '',card,flags=re.S)


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
    card = platform_card((payload / 'DATASET_CARD.md').read_text(),platform)
    summary = f'\nRelease **{manifest["version"]}**; {manifest["file_count"]:,} shared files; {manifest["payload_bytes"]:,} bytes.\n'
    if platform == 'huggingface':
        header = ('---\npretty_name: ' + json.dumps(config['title']) + '\nlanguage:\n- en\nlicense: other\n'
                  'license_name: us-government-works\n'
                  f'license_link: https://huggingface.co/datasets/{owner}/{config["slug"]}/blob/main/DATA_SOURCES.md\n'
                  'tags:\n- tornado\n- weather\n- geospatial\n- census\n- tabular\n'
                  'size_categories:\n- 10K<n<100K\nviewer: false\n---\n\n')
        (destination / 'README.md').write_text(header + card + summary)
    elif platform == 'kaggle':
        summary += ('\nKaggle transport: `release.zip.bin` is a ZIP archive with an extra `.bin` suffix '
                    'to preserve source files and the bundled supporting assets. Extract it with Python `zipfile` '
                    'for the complete collection. The consolidated analysis tables are also directly '
                    'available under `analysis/` and `ml/`; they have the same bytes as the copies in the archive. '
                    'Verify the extracted tree against `release_manifest.json`.\n')
        (destination / 'README.md').write_text(card + summary)
        write_json(destination / 'dataset-metadata.json', dict(title=config['title'], subtitle=config['subtitle'],
                   id=f'{owner}/{config["slug"]}', licenses=[{'name': config['data_license']}],
                   description=card + summary, keywords=['geography','earth and nature'],
                   **read_json(ROOT/'release/kaggle/metadata.json')))
        shutil.copyfile(ROOT/'release/kaggle/dataset-cover-image.png',destination/'dataset-cover-image.png')
    else:
        raise ValueError('Unknown platform')
    verified = verify_release(destination, digest(payload / 'release_manifest.json'))
    if platform == 'kaggle':
        archive = destination / 'release.zip.bin'
        paths = [entry['path'] for entry in manifest['files']] + ['release_manifest.json', 'SHA256SUMS']
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
            for relative in sorted(paths):
                zipped.write(local(payload, relative), relative)
        # Keep analysis and descriptive files visible; preserve full sources in the archive.
        for item in list(destination.iterdir()):
            if item.is_dir() and item.name not in ('analysis','ml','research'):
                shutil.rmtree(item)
            elif item.is_dir():
                continue
            elif item.name not in {'release.zip.bin', 'README.md', 'dataset-metadata.json',
                                   'DATASET_CARD.md', 'DATA_SOURCES.md', 'CITATION.cff',
                                   'CODE_LICENSE.txt', 'COLLECTION.md', 'ANALYSIS.md', 'LINKAGE.md', 'ENRICHMENT.md', 'schema.json',
                                   'source_registry.json','RESEARCH_READINESS.md',
                                   'release_manifest.json', 'SHA256SUMS','dataset-cover-image.png'}:
                item.unlink()
        verified['archive_sha256'] = digest(archive)
        verified['archive_bytes'] = archive.stat().st_size
    return verified


def unpack(archive, destination, expected_manifest_sha, expected_archive_sha=None):
    """Restore a Kaggle transport archive into a new directory and verify it."""
    archive, destination = Path(archive).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError('Extraction directory already exists')
    if expected_archive_sha and digest(archive) != expected_archive_sha:
        raise ValueError('Archive does not match trusted archive hash')
    with zipfile.ZipFile(archive) as zipped:
        seen = set()
        for info in zipped.infolist():
            local(destination, info.filename)
            if info.filename in seen or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Duplicate path or symlink in archive')
            seen.add(info.filename)
        destination.mkdir(parents=True)
        zipped.extractall(destination)
    return verify_release(destination, expected_manifest_sha)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--backbone-only', action='store_true', help='Explicitly exclude local enrichment/ML tables')
    b.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    b.add_argument('--output', type=Path)
    v = sub.add_parser('verify')
    v.add_argument('folder', type=Path)
    v.add_argument('--manifest-sha256')
    r = sub.add_parser('restore-support')
    r.add_argument('folder',type=Path)
    u = sub.add_parser('unpack')
    u.add_argument('archive', type=Path)
    u.add_argument('destination', type=Path)
    u.add_argument('--manifest-sha256', required=True)
    u.add_argument('--archive-sha256')
    s = sub.add_parser('stage')
    s.add_argument('platform', choices=['huggingface', 'kaggle'])
    s.add_argument('--owner', required=True)
    s.add_argument('--payload', type=Path, required=True)
    s.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    config = read_json(ROOT / 'release/dataset.json')
    if args.command == 'build':
        build(args.data_dir, args.output or ROOT / 'dist' / config['version'], config, backbone_only=args.backbone_only)
    elif args.command == 'verify':
        print(json.dumps(verify_release(args.folder, args.manifest_sha256), indent=2))
    elif args.command == 'restore-support':
        print(json.dumps(restore_support(args.folder),indent=2))
    elif args.command == 'unpack':
        print(json.dumps(unpack(args.archive, args.destination, args.manifest_sha256, args.archive_sha256), indent=2))
    else:
        print(json.dumps(stage(args.payload, args.output, args.platform, args.owner, config), indent=2))


if __name__ == '__main__':
    main()
