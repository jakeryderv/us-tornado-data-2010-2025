"""Download the tornado-classification dataset into data/.

Usage: uv run python download_data.py
No network or filesystem mutations occur on import, --help, or --dry-run.
See README.md for source scope, timing rules, and inspection workflow.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import csv
import gzip
import hashlib
import json
import re
import time

import argparse
import sys

ROOT = Path(__file__).resolve().parent


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + '.part')
    part.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    part.replace(path)


def sha256(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def validate_file(path, kind):
    if kind == 'json':
        value = json.loads(path.read_text())
        if isinstance(value, dict) and 'error' in value:
            raise RuntimeError(f'ArcGIS error: {value["error"]}')
    elif kind == 'zip':
        import zipfile
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError(f'Corrupt ZIP: {path}')
    elif kind in ('csv', 'gzip', 'census_csv'):
        opener = gzip.open if kind == 'gzip' else open
        with opener(path, 'rt', encoding='cp1252' if kind == 'census_csv' else 'utf-8-sig', newline='') as handle:
            header = next(csv.reader(handle), [])
            if len(header) < 2 or '<html' in ','.join(header).lower():
                raise ValueError(f'Invalid CSV response: {path}')
            if kind == 'gzip':
                # Read to the end to verify the gzip CRC and detect truncation.
                while handle.read(1024 * 1024):
                    pass


def download_file(url, path, kind, *, refresh=False, timeout=120):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sidecar = path.with_name(path.name + '.metadata.json')
    if not refresh and path.exists() and sidecar.exists():
        metadata = json.loads(sidecar.read_text())
        if (metadata.get('url') == url and metadata.get('bytes') == path.stat().st_size
                and metadata.get('sha256') == sha256(path)):
            return path
    temporary = path.with_name(path.name + '.part')
    for attempt in range(4):
        try:
            request = Request(url, headers={'User-Agent': 'tornado-classification-download/2.0'})
            with urlopen(request, timeout=timeout) as response, temporary.open('wb') as out:
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
                headers = dict(response.headers.items())
                resolved_url = response.url
            validate_file(temporary, kind)
            metadata = dict(url=url, resolved_url=resolved_url, retrieved_at=now(),
                            bytes=temporary.stat().st_size, sha256=sha256(temporary),
                            response_headers=headers)
            temporary.replace(path)
            atomic_json(sidecar, metadata)
            print(f'Downloaded {path} ({metadata["bytes"]:,} bytes)', flush=True)
            return path
        except (HTTPError, URLError, TimeoutError, ConnectionError, OSError) as exc:
            if isinstance(exc, HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt == 3:
                raise
            print(f'Retrying after {type(exc).__name__}: {url}', flush=True)
            time.sleep(2 ** attempt)
        finally:
            temporary.unlink(missing_ok=True)



def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--start-year', type=int, default=2010)
    parser.add_argument('--end-year', type=int, default=2025)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--sources', choices=('all', 'noaa', 'census'), default='all',
                        help='Collect all sources, only NOAA records, or only the two small Census datasets')
    parser.add_argument('--refresh', action='store_true', help='Replace verified caches with fresh source data')
    parser.add_argument('--verify-downloads', action='store_true', help='Independently check file hashes, exact extraction, and annual footprint completeness after downloading')
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--dry-run', action='store_true', help='Print configuration only; no requests, downloads, or directory creation')
    args = parser.parse_args(argv)
    args.data_dir = args.data_dir.expanduser().resolve()
    if not 2010 <= args.start_year <= args.end_year <= datetime.now().year:
        parser.error('Choose an inclusive year range from 2010 through the current year.')
    if args.sources != 'noaa' and not 2010 <= args.start_year <= args.end_year <= 2025:
        parser.error('The pinned Census releases support 2010–2025; use --sources noaa for other periods.')
    if args.timeout <= 0:
        parser.error('Use a positive timeout.')
    return args


def download_records(args):
    """Collect SPC/NCEI/EFC and write their original-format manifests and quality report."""
    START_YEAR, END_YEAR = args.start_year, args.end_year
    REFRESH, TIMEOUT_SECONDS = args.refresh, args.timeout
    DATA = args.data_dir
    YEARS = range(START_YEAR, END_YEAR + 1)
    SPC_INDEX = 'https://www.spc.noaa.gov/wcm/'
    NCEI_INDEX = 'https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/'
    print(f'Destination: {DATA} | Requested years: {START_YEAR}–{END_YEAR}', flush=True)


    # Provenance and validation helpers
    def utc_now():
        return datetime.now(timezone.utc).isoformat()


    def sha256(path):
        with path.open('rb') as handle:
            return hashlib.file_digest(handle, 'sha256').hexdigest()


    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.part')
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
        temporary.replace(path)


    def download(url, path, kind, refresh=None):
        return download_file(url, path, kind, refresh=REFRESH if refresh is None else refresh,
                             timeout=TIMEOUT_SECONDS)


    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.hrefs = []

        def handle_starttag(self, tag, attrs):
            if tag == 'a':
                self.hrefs.extend(value for key, value in attrs if key == 'href' and value)


    def catalog(url, path):
        download(url, path, 'html', refresh=True)
        parser = Links()
        parser.feed(path.read_text())
        return [urljoin(url, href) for href in parser.hrefs]


    summary = []


    def check_year(value, label, expected=None):
        year = int(value)
        if year not in YEARS or (expected is not None and year != expected):
            raise ValueError(f'{label}: year {year} is outside the requested period or annual partition.')
        return year


    def year_from_month(value, label, expected):
        if not re.fullmatch(r'\d{6}', value):
            raise ValueError(f'{label}: invalid year-month {value!r}.')
        return check_year(datetime.strptime(value, '%Y%m').year, label, expected)


    def inspect_spc(path):
        counts = {}
        with path.open(newline='', encoding='utf-8-sig') as handle:
            reader = csv.DictReader(handle)
            if not {'yr', 'date'}.issubset(reader.fieldnames or []):
                raise ValueError('SPC schema changed: expected yr and date columns.')
            for row in reader:
                year = check_year(row['yr'], 'SPC')
                check_year(datetime.fromisoformat(row['date']).year, 'SPC date', year)
                counts[year] = counts.get(year, 0) + 1
        return counts

    # SPC historical single-track records
    spc_links = catalog(SPC_INDEX, DATA / 'spc' / 'catalog.html')
    candidates = []
    for url in spc_links:
        match = re.fullmatch(r'(\d{4})-(\d{4})_actual_tornadoes\.csv', Path(urlparse(url).path).name)
        if match:
            candidates.append((int(match[2]), int(match[1]), url))
    if not candidates:
        raise RuntimeError('No SPC actual_tornadoes CSV found; inspect data/spc/catalog.html.')
    archive_end, archive_start, spc_url = max(candidates)
    if START_YEAR < archive_start or END_YEAR > archive_end:
        print(f'WARNING: SPC published coverage is {archive_start}–{archive_end}.')

    spc_filtered = DATA / 'spc' / f'tornadoes_{START_YEAR}_{END_YEAR}.csv'
    spc_sidecar = spc_filtered.with_name(spc_filtered.name + '.metadata.json')
    legacy_spc = DATA / 'spc' / Path(urlparse(spc_url).path).name
    selection = dict(start_year=START_YEAR, end_year=END_YEAR)
    spc_metadata = json.loads(spc_sidecar.read_text()) if spc_sidecar.exists() else {}
    reuse_subset = (not REFRESH and spc_filtered.exists()
                    and spc_metadata.get('selection') == selection
                    and spc_metadata.get('source', {}).get('url') == spc_url
                    and spc_metadata.get('sha256') == sha256(spc_filtered)
                    and spc_metadata.get('bytes') == spc_filtered.stat().st_size)
    if not reuse_subset:
        with TemporaryDirectory(prefix='noaa-spc-') as temporary_dir:
            # Reuse the legacy archive only when its provenance and bytes are verified.
            legacy_sidecar = legacy_spc.with_name(legacy_spc.name + '.metadata.json')
            legacy_metadata = json.loads(legacy_sidecar.read_text()) if legacy_sidecar.exists() else {}
            reuse_legacy = (not REFRESH and legacy_spc.exists()
                            and legacy_metadata.get('url') == spc_url
                            and legacy_metadata.get('sha256') == sha256(legacy_spc)
                            and legacy_metadata.get('bytes') == legacy_spc.stat().st_size)
            if reuse_legacy:
                spc_raw, source_metadata = legacy_spc, legacy_metadata
            else:
                spc_raw = download(spc_url, Path(temporary_dir) / legacy_spc.name, 'csv')
                source_metadata = json.loads(spc_raw.with_name(spc_raw.name + '.metadata.json').read_text())
            temporary_output = spc_filtered.with_name(spc_filtered.name + '.part')
            try:
                with spc_raw.open(newline='', encoding='utf-8-sig') as source:
                    reader = csv.DictReader(source)
                    if not {'yr', 'date'}.issubset(reader.fieldnames or []):
                        raise ValueError('SPC schema changed: expected yr and date columns.')
                    with temporary_output.open('w', newline='', encoding='utf-8') as target:
                        writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
                        writer.writeheader()
                        for row in reader:
                            if int(row['yr']) in YEARS:
                                writer.writerow(row)
                inspect_spc(temporary_output)  # Validate before replacing the retained dataset.
                spc_metadata = dict(source=source_metadata, selection=selection, derived_at=utc_now(),
                                    sha256=sha256(temporary_output), bytes=temporary_output.stat().st_size)
                temporary_output.replace(spc_filtered)
                write_json(spc_sidecar, spc_metadata)
            finally:
                temporary_output.unlink(missing_ok=True)
    spc_year_counts = inspect_spc(spc_filtered)
    spc_count = sum(spc_year_counts.values())
    summary.append(dict(source='SPC', rows=spc_count, path=str(spc_filtered.relative_to(DATA)),
                        source_archive=spc_metadata['source'], sha256=sha256(spc_filtered),
                        metadata=str(spc_sidecar.relative_to(DATA)),
                        date_fields=['yr', 'date'], year_counts=spc_year_counts))
    print(f'SPC: {spc_count:,} track rows saved; full-archive provenance retained in {spc_sidecar.name}.')

    # NCEI Storm Events
    ncei_links = catalog(NCEI_INDEX, DATA / 'ncei_storm_events' / 'catalog.html')
    ncei_files = {}
    for url in ncei_links:
        name = Path(urlparse(url).path).name
        match = re.fullmatch(r'StormEvents_(details|fatalities|locations)-ftp_v[^_]+_d(\d{4})_c(\d{8})\.csv\.gz', name)
        if match and int(match[2]) in YEARS:
            key = (int(match[2]), match[1])
            candidate = (match[3], url)
            if key not in ncei_files or candidate > ncei_files[key]:
                ncei_files[key] = candidate
    if not ncei_files:
        raise RuntimeError('No requested NCEI files found; inspect the catalog and year settings.')

    missing_ncei = []
    for year in YEARS:
        tornado_ids = set()
        for table in ('details', 'fatalities', 'locations'):
            item = ncei_files.get((year, table))
            if item is None:
                missing_ncei.append(dict(year=year, table=table))
                print(f'WARNING: NCEI {year} {table} is unavailable.')
                continue
            url = item[1]
            raw = download(url, DATA / 'ncei_storm_events' / 'raw' / Path(urlparse(url).path).name, 'gzip')
            if table != 'details' and (year, 'details') not in ncei_files:
                continue  # Cannot identify tornado-related rows without the details table.
            output = DATA / 'ncei_storm_events' / 'tornado' / f'{year}_{table}.csv'
            output.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(raw, 'rt', encoding='utf-8-sig', newline='') as source:
                reader = csv.DictReader(source)
                date_field = {'details': 'BEGIN_YEARMONTH', 'locations': 'YEARMONTH',
                              'fatalities': 'FAT_YEARMONTH'}[table]
                required = ({'EVENT_ID', 'EVENT_TYPE'} if table == 'details' else {'EVENT_ID'}) | {date_field}
                if not required.issubset(reader.fieldnames or []):
                    raise ValueError(f'NCEI schema changed: {raw.name} lacks {required}.')
                with output.open('w', newline='', encoding='utf-8') as target:
                    writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
                    writer.writeheader()
                    count = 0
                    raw_count = 0
                    for row in reader:
                        year_from_month(row[date_field], f'NCEI {table}', year)
                        raw_count += 1
                        keep = (row['EVENT_TYPE'].strip().casefold() == 'tornado'
                                if table == 'details' else row['EVENT_ID'] in tornado_ids)
                        if keep:
                            writer.writerow(row)
                            count += 1
                            if table == 'details':
                                tornado_ids.add(row['EVENT_ID'])
            summary.append(dict(source='NCEI', year=year, table=table, rows=count,
                                path=str(output.relative_to(DATA)), input=str(raw.relative_to(DATA)),
                                input_sha256=sha256(raw), date_field=date_field, raw_rows_validated=raw_count,
                                year_counts={year: count}))
            print(f'NCEI {year} {table}: {count:,} tornado-related rows.')

    # NOAA Event Footprint Catalog: generation-pinned annual snapshots.
    from footprint_data import collect
    footprints = collect(DATA, START_YEAR, END_YEAR, download)
    summary.extend(footprints['outputs'])

    # Original-source manifest
    coverage = []
    for source, product in [('SPC', 'tracks'), *[('NCEI', t) for t in ('details', 'fatalities', 'locations')],
                            ('EFC', 'footprints')]:
        records = [row for row in summary if row['source'] == source
                   and row.get('table', row.get('layer', 'tracks')) == product]
        counts = {year: 0 for year in YEARS}
        for row in records:
            for year, count in row['year_counts'].items():
                counts[int(year)] += count
        available = (set(YEARS) & set(range(archive_start, archive_end + 1)) if source == 'SPC'
                     else {row['year'] for row in records})
        unavailable = sorted(set(YEARS) - available)
        empty = sorted(year for year in available if counts[year] == 0)
        coverage.append(dict(source=source, product=product, available_years=sorted(available),
                             years_with_records=[year for year, count in counts.items() if count],
                             unavailable_years=unavailable, zero_record_years=empty, rows_by_year=counts))
        print(f'{source} {product}: {len(available)}/{len(YEARS)} yearly outputs available')
        if unavailable:
            print(f'  WARNING: unavailable years: {unavailable}')
        if empty:
            print(f'  NOTE: years with zero records: {empty} (not evidence of no tornadoes)')

    manifest = dict(completed_at=utc_now(), start_year=START_YEAR, end_year=END_YEAR,
                    spc_published_years=[archive_start, archive_end],
                    missing_ncei=missing_ncei, footprints=footprints,
                    requested_archives_available=(not missing_ncei and START_YEAR >= archive_start
                                                  and END_YEAR <= archive_end),
                    date_validation_passed=True, year_coverage=coverage, outputs=summary)
    manifest_path = DATA / f'download_manifest_{START_YEAR}_{END_YEAR}.json'
    write_json(manifest_path, manifest)
    print(f'Manifest: {manifest_path}')
    for source in ('SPC', 'NCEI', 'EFC'):
        records = [row for row in summary if row['source'] == source]
        print(f'{source}: {len(records)} outputs, {sum(row["rows"] for row in records):,} rows/features')
    print('Counts above combine different record types; they are not comparable tornado totals.')

    # Complete the migration only after the subset, provenance, and manifest are saved and verified.
    if legacy_spc.exists():
        saved_metadata = json.loads(spc_sidecar.read_text())
        saved_manifest = json.loads(manifest_path.read_text())
        if (saved_metadata != spc_metadata or sha256(spc_filtered) != saved_metadata['sha256']
                or saved_manifest != json.loads(json.dumps(manifest))
                or sha256(legacy_spc) != saved_metadata['source']['sha256']):
            raise RuntimeError('Legacy SPC cleanup skipped: provenance verification failed.')
        legacy_spc.unlink()
        legacy_spc.with_name(legacy_spc.name + '.metadata.json').unlink(missing_ok=True)
        print('Removed the verified full-history SPC CSV and sidecar; source provenance is preserved.')

    # Label and footprint quality, retaining all source records.
    from collections import Counter

    spc_ratings_by_year = {year: Counter() for year in YEARS}
    with spc_filtered.open(newline='', encoding='utf-8-sig') as handle:
        for row in csv.DictReader(handle):
            year = check_year(row['yr'], 'SPC quality summary')
            magnitude = row['mag'].strip()
            label = magnitude if magnitude in {'0', '1', '2', '3', '4', '5'} else 'unknown_other'
            spc_ratings_by_year[year][label] += 1

    print('SPC rating magnitudes (EF scale for the default 2010–2025 period)')
    print(f"{'Year':<6}" + ''.join(f'{label:>9}' for label in ['0', '1', '2', '3', '4', '5', 'Unknown']))
    spc_rating_totals = Counter()
    for year, counts in spc_ratings_by_year.items():
        spc_rating_totals.update(counts)
        print(f'{year:<6}' + ''.join(f'{counts[label]:>9,}' for label in ['0', '1', '2', '3', '4', '5', 'unknown_other']))
    print(f"{'Total':<6}" + ''.join(f'{spc_rating_totals[label]:>9,}' for label in ['0', '1', '2', '3', '4', '5', 'unknown_other']))

    quality_path = DATA / f'quality_summary_{START_YEAR}_{END_YEAR}.json'
    quality_summary = dict(generated_at=utc_now(), start_year=START_YEAR, end_year=END_YEAR,
                           download_manifest=str(manifest_path.relative_to(DATA)),
                           download_manifest_sha256=sha256(manifest_path),
                           spc_file=str(spc_filtered.relative_to(DATA)), spc_sha256=sha256(spc_filtered),
                           spc_ratings_by_year={year: dict(counts) for year, counts in spc_ratings_by_year.items()},
                           spc_rating_totals=dict(spc_rating_totals), footprints_by_year=[{'year':o['year'], 'source_counts':o['source_counts'], **o['quality']} for o in footprints['outputs']])
    write_json(quality_path, quality_summary)
    print(f'\nDetailed annual quality summary: {quality_path}')
    print('All records are retained. Event matching and training-label selection belong in dataset preparation.')
    return manifest_path, manifest, spc_filtered


def main(argv=None):
    args = parse_args(argv)
    if args.dry_run:
        print(json.dumps(vars(args), indent=2, default=str))
        print('Configuration only: no catalog requests or data downloads were performed.')
        return 0

    args.data_dir.mkdir(parents=True, exist_ok=True)
    verification_path = args.data_dir / f'download_verification_{args.start_year}_{args.end_year}.json'
    if args.verify_downloads:
        atomic_json(verification_path, dict(status='pending_download', started_at=now()))
    if args.sources != 'census':
        download_records(args)
    if args.sources != 'noaa':
        from census_data import collect
        collect(args.data_dir, args.start_year, args.end_year,
                lambda url, path, kind: download_file(url, path, kind, refresh=args.refresh, timeout=args.timeout))
    if args.verify_downloads:
        from scripts.verify_downloads import verify
        print('Download finished; independently verifying saved data...', flush=True)
        result = verify(args.data_dir, args.start_year, args.end_year, sources=args.sources)
        atomic_json(verification_path, result)
        if result['status'] != 'passed':
            raise ValueError(f'Verification failed: {result["errors"]}; see {verification_path}')
        print(f'Independent verification passed: {verification_path}', flush=True)
    print('Collection finished. Open notebooks/tornado_dataset.ipynb to inspect local outputs.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as exc:
        args = parse_args()
        if args.verify_downloads and not args.dry_run:
            report = args.data_dir / f'download_verification_{args.start_year}_{args.end_year}.json'
            previous = json.loads(report.read_text()) if report.exists() else {}
            if previous.get('status') != 'failed':
                atomic_json(report, dict(status='download_interrupted' if isinstance(exc, KeyboardInterrupt)
                                        else 'download_failed', stopped_at=now(), error=str(exc),
                                        verification_completed=False))
        raise
