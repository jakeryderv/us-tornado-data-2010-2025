# Enrichment performance validation

The optimized collector completed the same 200-event live workload in **228.7
seconds**, versus **905.6 seconds** at `df883ab` (about **3.96× faster**). Both
runs completed all 1,000 requested source jobs and transferred about 1.32 GB.
The full 20,164-event collection was not started. The active four-event pilot
and published v2.1.0 dataset were not replaced by these experiments.

| Source | Before: summed serial job seconds | After: source wall seconds |
|---|---:|---:|
| SWDI radar | 186.4 | 34.3 |
| IEM warnings | 356.1 | 88.9 |
| Annual NLCD | 123.7 | 22.5 |
| ACS | 135.3 | 48.7 |
| TIGER | 101.7 | 30.9 |

Totals include final table writing and other collection overhead. They exclude
subsequent ML generation and verification. The optimized run used four workers,
two concurrent HTTP requests per host, a 5 GB retained cache and a 10 GB transfer
ceiling. Sources ran in groups; no ERA5 requests were made.

## Correctness

The optimized collector also replayed the original retained source bytes, with
HTTP disabled. Extraction finished in 74.4 seconds and downloaded zero bytes.
All 11 source/link/provenance tables matched the baseline exactly, including
numeric values, nulls, raw attributes and geometry bytes. The comparison ignores
column order and excludes only `source_coverage.job_id`, which contains code
hashes. Both generated ML views matched exactly across all 20,164 backbone rows;
only the selected 200 events were enriched. Original EF targets, onset cutoff
rules, warning history selection and exposure calculations were preserved.

The verifier passed for both optimized collections, checking source/table
digests, keys, associations, source coverage, target preservation and the onset
feature allowlist. All 69 offline tests passed with the optional ERA5 dependencies
installed. New tests cover shared request ownership, simultaneous HTTP/transfer
limits, in-flight disk reservations, cache pinning, retry cleanup, shared decoded
inputs/checkpoints, legacy checkpoint integrity and deterministic parallel output.

## Interpretation and reproducibility

The cohort spans 2010–2025: eight stable-hash selections per year plus up to
twelve events on each of the six busiest UTC dates, deduplicated and filled to
200. The exact event IDs, timings, extraction configuration, code/backbone hashes
and table row counts are in [performance_validation.json](performance_validation.json).
The benchmark runner is [scripts/benchmark_enrichment.py](../../scripts/benchmark_enrichment.py).
Its replay mode copies source caches and forbids HTTP; its comparison uses
`check_exact=True`. See the [run instructions](../../docs/ENRICHMENT.md#repeat-a-bounded-benchmark).

This is one local benchmark, not a controlled service benchmark or a guarantee
of a fourfold improvement on every machine. The offline replay overlapped part
of the optimized live run. Upstream latency, server caching and returned archive
bytes can differ between live requests, which is why scientific equivalence was
checked with the original cached bytes. The cohort intentionally includes
outbreak days; reuse patterns differ across the full dataset.

A linear projection gives **6.4 hours** for 20,164 events. Allow **6–12 hours** as
a planning range, and longer if providers throttle or requests need retrying.
Shared Census/day inputs may make the full run more efficient per event. Radar
and NLCD requests retain their original bounds and resolution. Persistent-size
estimates are unchanged: compact mode still retains extracted tables, provenance
and supporting files rather than every large source artifact.
