"""Bounded enrichment benchmark; never uses the active enrichment output.

Run as ``python -m scripts.benchmark_enrichment --help``. Replay copies cached
inputs and disables HTTP, separating extraction cost from upstream variability.
"""
import argparse
from collections import Counter
from contextlib import nullcontext
import json
from pathlib import Path
import shutil
import time
from unittest.mock import patch

import pandas as pd
import numpy as np
from dotenv import load_dotenv

from enrichment.common import Config, atomic_json, stable_id
from enrichment.features import build
from enrichment.pipeline import DEFAULT_SOURCES, collect, event_records
from enrichment.verify import verify
from enrichment.layout import table_directory


ROOT = Path(__file__).resolve().parents[1]


def select_events(events):
    """200 fixed events: all 16 years plus six large outbreak days."""
    selected = {}
    for year in range(2010, 2026):
        rows = sorted((e for e in events if e['year'] == year),
                      key=lambda e: stable_id('benchmark', e['tornado_id']))
        selected.update((e['tornado_id'], e) for e in rows[:8])
    days = Counter(e['start_utc'][:10] for e in events if e['start_utc'])
    for day, _ in days.most_common(6):
        rows = [e for e in events if (e['start_utc'] or '').startswith(day)]
        selected.update((e['tornado_id'], e) for e in rows[:12])
    for event in sorted(events, key=lambda e: stable_id('benchmark-fill', e['tornado_id'])):
        if len(selected) >= 200:
            break
        selected[event['tornado_id']] = event
    return sorted(selected.values(), key=lambda e: e['tornado_id'])


def compare_tables(before, after, *, coverage=False, science=False, names=None):
    paths = sorted(before/name for name in names) if names is not None else sorted(before.glob('*.parquet'))
    if names is None and {p.name for p in paths} != {p.name for p in after.glob('*.parquet')}:
        raise ValueError('Table sets differ')
    compared = {}
    for path in paths:
        if science and path.name == 'record_provenance.parquet':
            continue  # Verified independently; batched URLs/bytes necessarily differ.
        left, right = pd.read_parquet(path), pd.read_parquet(after / path.name)
        # Job IDs include code hashes, which necessarily differ after an edit.
        if coverage and path.name == 'source_coverage.parquet':
            left, right = (f.drop(columns=['job_id','extraction_definition_id'],errors='ignore') for f in (left,right))
        if science:
            cols = [c for c in left if c.endswith('asset_id')]
            left, right = left.drop(columns=cols), right.drop(columns=cols)
            if path.name == 'nlcd_samples.parquet' and len(left):
                delta = np.abs(np.stack(left.raster_transform) - np.stack(right.raster_transform))
                # Different WCS extents can serialize slightly different affine
                # coefficients. Counts/means are still compared exactly below.
                if np.any(delta > np.array([1e-6, 0, .001, 0, 1e-6, .001])):
                    raise AssertionError('NLCD grid difference exceeds sub-mm origin / micrometre pixel-size tolerance')
                left, right = left.drop(columns='raster_transform'), right.drop(columns='raster_transform')
        pd.testing.assert_frame_equal(left, right, check_like=True, check_exact=True)
        compared[path.name] = len(left)
    return compared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--output', type=Path, required=True, help='New experiment directory')
    parser.add_argument('--events-json', type=Path, help='Reuse a previously selected cohort')
    parser.add_argument('--replay-from', type=Path, help='Copy retained raw inputs; forbid network')
    parser.add_argument('--compare-with', type=Path, help='Require identical source and ML tables')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--acquisition', choices=['batch','event'], default='batch')
    parser.add_argument('--parallel-sources', action='store_true')
    parser.add_argument('--compare-science', action='store_true', help='Allow changed acquisition assets, but require identical source values, links and ML views')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output directory; benchmark must not resume or overwrite a collection')
    load_dotenv(ROOT / '.env')
    events = (json.loads(args.events_json.read_text()) if args.events_json else
              select_events(event_records(args.data_dir, Config())))
    if not 1 <= len(events) <= 500:
        parser.error('Benchmark cohort must contain 1–500 events')
    args.output.mkdir(parents=True)
    atomic_json(args.output / 'events.json', events)
    if args.replay_from:
        shutil.copytree(args.replay_from / 'raw', args.output / 'raw')
    context = (patch('enrichment.common.urlopen', side_effect=RuntimeError('HTTP forbidden during replay'))
               if args.replay_from else nullcontext())
    start = time.monotonic()
    with context:
        manifest = collect(args.data_dir, args.output, events, list(DEFAULT_SOURCES), Config(),
                           workers=args.workers, per_host=2, timeout=90,
                           acquisition=args.acquisition,
                           parallel_sources=args.parallel_sources,
                           max_bytes=10_000_000_000, storage='cache', max_cache_bytes=5_000_000_000)
    timing = dict(wall_seconds=time.monotonic() - start, events=len(events),
                  workers=args.workers, replay=bool(args.replay_from),
                  status_counts=manifest['status_counts'], downloaded_bytes=manifest['downloaded_bytes'],
                  source_wall_seconds=manifest['source_wall_seconds'], performance=manifest['performance'])
    atomic_json(args.output / 'timing.json', timing)
    if manifest['status'] != 'complete':
        raise RuntimeError('Benchmark has unfinished jobs; inspect source_coverage and timing.json')
    build(args.data_dir, args.output, args.output / 'ml')
    atomic_json(args.output / 'verification.json', verify(args.data_dir, args.output, args.output / 'ml'))
    if args.compare_with:
        reference_ml = args.output / 'reference_ml'
        build(args.data_dir, args.compare_with, reference_ml)
        reference_manifest=json.loads((args.compare_with/'manifest.json').read_text())
        if {v['path'] for v in reference_manifest['outputs']}!={v['path'] for v in manifest['outputs']}:
            raise ValueError('Source table sets differ')
        comparison = dict(
            source_tables=compare_tables(table_directory(args.compare_with,reference_manifest), table_directory(args.output,manifest),
                                         names=[v['path'] for v in manifest['outputs']],coverage=True, science=args.compare_science),
            ml_tables=compare_tables(reference_ml, args.output / 'ml'),
            excluded_fields=['source_coverage.job_id and extraction_definition_id (contain extraction code hashes)'] +
                (['source asset references and record_provenance (new batched requests; independently verified)',
                  'NLCD affine metadata compared within 1 mm origin / 1 micrometre pixel size; all pixel statistics exact'] if args.compare_science else []))
        atomic_json(args.output / 'comparison.json', comparison)
    print(json.dumps(timing, indent=2))


if __name__ == '__main__':
    main()
