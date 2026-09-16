# Linked enrichment and event feature views

The existing nine `analysis/` tables remain the backbone. Optional collection
adds eight radar, warning, land-cover, linking and provenance tables to the same
`analysis/` directory, bringing it to 17 Parquet tables. Download metadata and
checkpoints remain under `enrichment/`. Reproducible, offline feature generation creates
two additional event tables under `ml/`; it never replaces the source tables.

**These additions are included in v2.2.1; v2.1.0 remains the earlier backbone-only release.** Development began
with four events spanning 2010–2025, followed by a 200-event equivalence benchmark.
ACS, TIGER/Line tract enrichment and ERA5
are deferred; the default collection and ML views omit their tables and columns.
Existing backbone county estimates and the simplified county map remain included. A 20,164-row ML table is not evidence of
20,164 enriched events: inspect `source_event_count`, `source_coverage`, and the
per-source status columns. Unrequested rows remain in both views.

## Collect and rebuild

Use Python 3.12 and the locked optional dependencies:

```sh
uv sync --locked --group enrichment
# No API keys are needed for the current three-source enrichment.
uv run --group enrichment python -m enrichment.pipeline --all-events --dry-run
uv run --group enrichment python -m enrichment.pipeline --all-events --workers 6 --per-host 2 --parallel-sources --storage compact --cache-gb 5 --max-download-gb 100
uv run --group enrichment python -m enrichment.features
uv run --group enrichment python -m enrichment.verify --require-full --report data/enrichment/verification.json
```

The **100 GB value is a per-invocation transfer ceiling, not disk usage**.
Compact storage is the default. `--cache-gb 5` bounds disposable downloaded
bodies, and compact mode evicts them after durable extraction checkpoints are
saved. It clears eligible bodies when the invocation finishes. Small supporting
files (warning text, inventories and grid/schema descriptions) remain.

Use `--storage cache` to leave the bounded cache between runs, or
`--storage archive` to retain every source body (the cache ceiling then does not
apply). These policies do not change scientific extraction settings.

The earlier **1.5–4 GB persistent / 10–15 GB working disk** planning allowance
included ACS/TIGER. After the full three-source collection and repair, the local
source tables plus `enrichment/` metadata contain about **1.16 GB of file contents** (about
**1.7 GiB allocated on this filesystem**, including many small provenance and
warning-text files). The two ML views add about 4 MB. These figures exclude the
backbone and separate benchmarks. Keep headroom beyond the 5 GB cache for
retained source tables, compressed checkpoints and table consolidation.
Incomplete runs retain compressed checkpoints and can use additional space.
Disk cache and network transfer limits are independent. A body larger than the
cache limit fails explicitly; raise `--cache-gb` and resume. Concurrent jobs pin
their inputs until their extraction checkpoints are committed. If their combined
working set exceeds the cache, increase it or reduce `--workers`.

The [bulk acquisition validation](../reports/enrichment/bulk_validation.md) compares
200 fixed events against the previous event-query collector. Treat timings as
sample measurements: server load, retries and query reuse differ at full scale.
There is no CDS queue in the default run.
The final live sample took **125.8 seconds** for 200 events / 600 source jobs.
A simple linear projection is about 3.5 hours; allow **3–6 hours** for planning,
with longer runs possible during throttling or outages. Full-cohort reuse may
help, while larger table consolidation adds work not represented by a small pilot.
The first full pass on September 16, 2026 took **7,167.6 seconds (about two hours)**
inside the collector and transferred **6.16 GB**. It completed 60,342 jobs, with
133 failures requiring repair and 17 documented NLCD coverage gaps. These are
measured results for this snapshot and host, not a guarantee for future downloads.
The [full repair audit](../reports/enrichment/full_repair_validation.md) records
the resolution of all 133 failures, reuse of 60,359 original jobs, and comparison
of the repaired source values against the original snapshot. All 20,164 events
are processed; NLCD remains unavailable for 17 events outside its selected coverage.
Read `progress.json` for finished jobs, transferred/cache bytes, request counters
and total elapsed time. `source_seconds` sums overlapping job durations;
`source_wall_seconds` measures each source queue's elapsed span. With parallel
sources these spans overlap and must not be added together.

Rerun the same command to resume: completed extraction checkpoints do not need
the original binaries. A fully completed identical selection verifies and reuses
its consolidated tables without downloading. When every backbone event and all
three default sources finish successfully (including documented unavailable jobs), compact
mode retires that run's redundant checkpoints. Subset/partial runs keep theirs.
A failed or transfer-budget-limited run exits with code 2; inspect status/reasons.
Only one collector may use an output directory at a time (Linux/macOS file lock).

### Consolidated table layout

All user-facing source and supporting tables live in `data/analysis/`; the two
modeling views live in `data/ml/`. `analysis/manifest.json` continues to describe
the nine backbone tables. `enrichment/manifest.json` independently describes the
eight additions and points to them using `table_directory: "../analysis"`.
Asset paths remain relative to `enrichment/`, where raw supporting files and
checkpoints are stored. Rebuilding the backbone leaves the additional tables intact.

An isolated `--output` collection writes its tables into its own `analysis/`
subdirectory, so benchmarks and pilots cannot overwrite the active dataset.
Loaders also understand historical manifests with tables under `tables/`.

To migrate an older completed local collection without downloading it again:

```sh
uv run --group enrichment python -m scripts.migrate_analysis_layout
uv run --group enrichment python -m enrichment.features
uv run --group enrichment python -m enrichment.verify --require-full --report data/enrichment/verification.json
```

The migration validates file hashes, rejects conflicting destinations, commits the
new manifest before removing the old table paths, and preserves the original
extraction definitions. It records compatibility with this reviewed layout-only
code revision so the normal collection command can reuse the completed snapshot.
It does not certify compatibility with future extraction-code changes.

### Repairing failures after a code fix

Ordinary resume requires the same extraction code and settings. After a reviewed
fix limited to failed requests, `--repair-from /path/to/frozen-manifest.json`
explicitly permits reuse of successful checkpoints from that snapshot. Save a
separate copy of `enrichment/manifest.json` before beginning the repair. This mode
requires unchanged backbone hashes, settings, acquisition strategy, events and
sources, and the original checkpoints must still exist. It is not appropriate
for changes to scientific definitions or fixes that invalidate successful rows.

Only unfinished jobs are extracted again. `source_coverage.extraction_definition_id`
and the original job IDs identify which code produced each job; the new manifest
retains inherited code definitions, the old manifest hash and repair accounting.
Full verification validates these identities as well as source/table checksums.
If the repair is interrupted, repeat the same command with the same frozen copy.
After a successful complete repair, the ordinary collection command can verify
and reuse the consolidated snapshot without downloading again.

An upstream service can regenerate an optional ZIP with different bytes for the
same URL. The cache validates a replacement against its own sidecar and checksum;
the older discarded version remains identified by its original hash in source
provenance. This never relaxes verification of retained required files or accepts
untracked/corrupt replacement bytes.

### Concurrency and reuse

The default acquisition strategy is `--acquisition batch`. `--acquisition event`
keeps the previous individual query pattern for controlled comparisons. Both use
the same event windows, source products, feature definitions and spatial rules.
The strategy is recorded in the extraction definition and checkpoint identity.

- **Radar:** group nearby starts in six-hour / one-degree planning bins, fetch
  their combined API bounds, and filter back to each original event window and
  bounding box before the exact 20 km link test. API precision, native fields and
  detection IDs remain unchanged. Responses at the 10,000-row cap are subdivided
  in time, then space if necessary; unresolved truncation fails explicitly.
- **Warnings:** use monthly exports with a one-day overlap when a month contains
  at least three selected onset days; otherwise keep small two-day queries.
  `addsvs=1` preserves follow-up polygons. Local filtering reproduces IEM's
  `coalesce(issue, polygon_begin)` selection for each original two-day window.
  On densely selected days, retrieve separate TOR, SVR and SVS daily text ZIPs
  as needed. Capped ZIPs subdivide in time. Missing or ambiguous product IDs fall
  back to the single-product endpoint. A public product ID can identify multiple
  warnings issued in the same minute: if its text does not match the expected
  office, phenomenon and event number, a one-minute archive query retrieves all
  candidates. Exact product-header and VTEC matching must select one distinct
  text, otherwise the job fails. Duplicate ZIP names are read individually.
  Only selected original texts are
  retained in compact mode, with the ZIP identity and checksum as provenance.
- **NLCD:** share prior-year extracts among nearby areas when the combined extent
  has limited overhead (at most twice the sum of individual areas, and four
  million pixels per merged extract). Each event is cropped back to its own
  integer native-grid window before masking; no resampling is introduced. Tiny
  WCS affine serialization drift is tolerated during alignment validation; the
  returned transform is retained and pixel statistics are compared exactly.
  One-cell-wide requests are padded on the native grid to avoid a GeoServer
  resolution error; sampling still uses the original event window and footprint.

NOAA's annual TVS/MDA CSV archives were evaluated but are **not enabled** as a
replacement. They have rounded coordinates, different fields and additional
records absent from the API. Substituting them would change the source cohort;
the validation report records this finding. Larger storm-structure annual files
are also unnecessary for the current extraction. There is no annual radar archive
download hidden in the default run.

The default remains four workers processing sources in groups. The recommended
full-run command uses `--workers 6 --parallel-sources`: two independent workers
per source with at most two simultaneous requests to each host. Queues commit
results in deterministic event order within each source, so slow warning calls
do not block radar or NLCD. Parallel queues require at least one worker per
requested source. ERA5 must run separately and remains serial/deferred.

`--workers 1` without `--parallel-sources` restores serial execution. Shared
requests have one downloader; worker connections are reused. Transfer
reservations, cache space, pinned inputs and eviction remain coordinated.
HTTP 429/5xx honors `Retry-After`; transient connection/read errors retry up to
three attempts. Failed jobs are still resumable. Warning inputs and checkpoint
batches are shared rather than copied once per event. Worker/queue settings do
not change extraction definitions.

### Repeat a bounded benchmark

The benchmark selects 200 events across all 16 years, including six large outbreak
days. It requires a fresh output directory and cannot target more than 500 events.
It builds both ML views and verifies source tables, links and provenance:

```sh
uv run --group enrichment python -m scripts.benchmark_enrichment --workers 6 --parallel-sources --output data/benchmarks/live
uv run --group enrichment python -m scripts.benchmark_enrichment --events-json data/benchmarks/live/events.json --output data/benchmarks/replay --replay-from data/benchmarks/live --compare-with data/benchmarks/live
```

Replay copies retained raw inputs and forbids HTTP. Its default table comparison
excludes `source_coverage.job_id` and `extraction_definition_id`, which identify
the extraction code. For an event-query
versus batch-query comparison, add `--compare-science`: changed asset references
are verified separately, NLCD affine origins allow 1 mm drift (pixel sizes allow
1 micrometre), and all counts, measurements, links and ML values must match exactly.
Use `--acquisition event` to collect the control. Compare live timing only
with other live runs; cached replay measures extraction work. The benchmark keeps
its bounded raw cache for comparison, independently of the active dataset.

For a small check, replace `--all-events` with `--limit 4`, or repeat `--event-id`
for a chosen cohort. `--start-year`, `--end-year`, and `--sources radar warnings
nlcd` select scope; `--data-dir` selects the backbone location.
`--output` selects a separate collection directory. The offline builder accepts
matching `--data-dir`, `--enrichment-dir`, and `--output` options.

**Each invocation materializes its selected cohort and sources.** A later small
run against the same output replaces the active normalized tables with that
selection; it does not union selections automatically. Use a separate `--output`
for experiments. Applicable extraction checkpoints remain; optional raw bodies
may be evicted. Running `--all-events` later reuses applicable checkpoints and
expands the active cohort.

No Census or CDS credentials are needed for this default workflow.
[ACS/TIGER tract exposure](CENSUS_TRACTS.md) and [ERA5](ERA5.md) are future targets;
optional adapters remain available only through explicit source selection.

## Files and keys

The default collection has eight source/link/provenance tables:

| File under `analysis/` | Unit and links | Contents |
|---|---|---|
| `radar_detections.parquet` | One `record_id` per product/detection | Radar/cell ID, UTC observation, point, native record, normalized indicators |
| `tornado_radar.parquet` | `tornado_id` + radar `record_id` | Distance, assumed availability, association method, query asset |
| `warning_updates.parquet` | One `record_id` per warning/product/polygon | Stable VTEC `warning_id`, product issuance, known expiry/action, polygon, raw attributes and text asset |
| `tornado_warnings.parquet` | `tornado_id` + update `record_id` | All updates of relevant warnings, start-point coverage and issue lead time |
| `nlcd_samples.parquet` | Event area/year/product `record_id` | Land-class pixel counts, impervious mean, valid/total pixel counts, grid and source provenance |
| `event_areas.parquet` | `tornado_id`, `area_id` | Retrospective area geometry, construction method and accepted footprint IDs |
| `source_coverage.parquet` | `tornado_id` + source | Requested-job status and reason; `complete`, `unavailable`, `failed`, `budget_exceeded` |
| `record_provenance.parquet` | Table/record/role/asset | Maps normalized rows to content-addressed source assets |

Read geometry tables with GeoPandas; all other tables with pandas. Retained/cached bodies
and HTTP metadata live in `enrichment/raw/`, resumable results in `jobs/` (with
shared large table checkpoints in `job_tables/`), and
the active collection inventory in `manifest.json`. The manifest records request
URLs, retrieval times, checksums, retention requirements,
selected IDs, parameters, source and code hashes. Asset paths in the manifest are relative to `enrichment/`.
Raw cache sidecars may contain local paths; they are working cache metadata.
`complete` describes the requested selection, including documented unavailable
jobs, not universal geographic/temporal coverage or every possible variable.

## Source choices and extraction

- **[NOAA SWDI](https://www.ncei.noaa.gov/swdiws/):** NEXRAD Level III-derived
  `nx3tvs`, `nx3mda` and `nx3structure` detections. Query the reported start's
  20 km neighborhood from 60 minutes before through 60 minutes after onset.
  Keep source rows in each event query window; link only detections actually inside
  the radius and time window. Preserve raw fields. TVS `MAX_SHEAR` is converted from 10^-3/s to
  1/s; velocity indicators retain knots, reflectivity dBZ, VIL kg/m².
  The structure product is filtered by SWDI (at least 45 dBZ). This is a selected
  detection catalog, not all Level III products or volume scans. Nearby detections
  are not verified parent-storm tracks. Empty successful queries do not establish
  radar operation, full archive coverage, or absence of rotation. Suspected
  truncated responses fail explicitly rather than supplying biased aggregates.
- **[IEM warning archive](https://mesonet.agron.iastate.edu/request/gis/watchwarn.phtml):**
  NWS tornado and severe-thunderstorm warnings, including follow-up polygon
  updates and original NWS product text. Fetch shared partitions, reconstruct each
  national two-day event window locally, and retain selected original warning text; link warning histories when any polygon covers the reported start.
  Evaluate the latest update issued by onset, its polygon, and its original
  product's VTEC action/expiration. Retrospectively shortened database expiry
  fields cannot rewrite what was known earlier. Partial county cancellations
  retain the continuing storm-based polygon; unresolved VTEC cases fail.
  Missing original issuance remains null and does not become zero lead time.
  This preserves IEM's polygon-export cohort: its current query excludes
  standalone `CAN` rows. It is not a complete VTEC message timeline, so warning
  activity after a cancellation absent from that export can be overstated.
- **[USGS Annual NLCD](https://www.usgs.gov/centers/eros/science/annual-nlcd-data-access):**
  prior event-year land cover and fractional impervious surface, using native
  numeric WCS GeoTIFF extracts from the USGS MRLC GeoServer. Sample 30 m,
  EPSG:5070 grids snapped to the native origin; retain counts, summaries and grid metadata. Count pixel centers within the
  event area, excluding no-data. Tiny areas may contain no centers; this is
  missing information, not undeveloped land. These selected products cover CONUS.
An accepted footprint union defines the retrospective area where available.
Otherwise, use a fixed 500 m buffer around the reported endpoint line/point.
The buffer is an explicit approximation, not an inferred damage width. Keep
the method and contributing footprint IDs. The source footprint/link quality
limits path-based land-cover interpretation. Tract exposure summaries are deferred;
county context remains available separately in the backbone.

## ML views and leakage contract

Both `ml/events_onset.parquet` and `ml/events_retrospective.parquet` contain
every backbone tornado once. `tornado_id` and the final `target_ef_rating` are
unchanged; unknown ratings remain null and `target_known` remains false.

- **Onset:** start coordinates and UTC calendar features; pre-cutoff radar
  detection counts/maxima; active warning counts and tornado-warning lead time;
  source status and missingness metadata. There are no environmental values in the onset view.
- **Retrospective:** the onset fields plus `post_` end/duration/path dimensions,
  casualties (metadata), radar aggregates through onset +60 minutes, land-class
  fractions, impervious percentage and area provenance.

The cutoff is **reported tornado onset**. Radar availability is observation
time plus an assumed 5 minutes; warnings use the issued product time.
Environmental variables are deferred. The optional ERA5 extension, if explicitly
enabled later, belongs only in retrospective analysis.
Both location and onset are hindsight anchors from the final catalog: this is
conditional intensity analysis of recorded tornadoes, not proof of a deployable
tornado-detection system. Radar latency remains an assumption, not receipt evidence.

Prior-year NLCD summaries are still retrospective predictors: the final damage area
was unknown at onset, product releases can occur later, and maps can be revised.
Final dimensions and exposure may encode the EF assessment itself. Never use
`post_` fields in an early forecast. Do not treat a source's presence/absence as
a physical intensity predictor without studying collection bias.

`feature_dictionary.json` supplies explicit onset/retrospective predictor lists,
target, ID, grouping field, column roles and assumptions. Schema 2 also provides
source tables/columns, units, aggregation/window and availability basis for each
field. `eligible_for_conditional_onset` identifies the 15 candidates;
`available_by_onset` is null for those fields because actual real-time delivery
is not verified. `post_nlcd_valid_fraction` is quality metadata, excluded from
the default retrospective predictor list. Metadata and unknown
targets are not silently imputed. `ml_split_group` joins existing backbone split
groups and events sharing radar detections or warnings; keep groups intact.
These conservative groups are not independently verified meteorological outbreaks.
Use temporal evaluation and exclude groups crossing a proposed train/test boundary;
fit imputers/scalers only on training data.

```python
import json
from pathlib import Path
import pandas as pd

root = Path('data/ml')
spec = json.loads((root / 'feature_dictionary.json').read_text())
events = pd.read_parquet(root / 'events_onset.parquet')
labelled = events.loc[events.target_known & events.target_ef_rating.notna()]
X = labelled[spec['onset_predictor_columns']]
y = labelled[spec['target']]
groups = labelled[spec['group']]
# Inspect source statuses and missingness before selecting a research cohort.
```

## Validation and reproducibility

Collection verifies cached body digests, HTTP lengths/ranges, binary formats,
source schemas and Parquet round trips. Source and feature builds record code,
configuration and input hashes. Feature generation validates source keys, links,
coverage/provenance and exact preservation of all backbone IDs and EF targets.
The retained linked tables, geometries, configuration and locked code are enough
for offline ML regeneration. Independent re-extraction from grids/archives needs
upstream downloads in compact mode. Checksums detect changed source bytes; they
cannot recover removed upstream files. Opt into archive mode if those original
bytes must remain locally available. Changing scientific extraction code
invalidates source jobs; changing only the ML builder does not.

```sh
uv run --locked --group era5 python -m unittest discover -s tests -v
```

Tests include pre-cutoff filtering, future cancellations/polygon changes, partial
county cancellation, invalid range responses, Census
sentinels, raster no-data and incomplete exposure. The local inspection notebook
checks active table hashes and reads coverage and both views without downloading.
`python -m enrichment.verify` rechecks retained source bytes, table keys/links,
the ML input snapshot and target preservation. It independently reconstructs all
11 radar/warning onset aggregates from source timestamps/values, rejecting changed
latency or future selected polygon times; this does not prove actual receipt or
archive completeness. It reports optional bodies as intentionally not retained, separately from missing/corrupt required files. A passed
verification can describe a small pilot; check `selected_events` and
`full_cohort_processed` rather than assuming a pass implies full coverage.
The full test suite also checks the deferred Census and ERA5 adapters; `--group era5` is only
needed for those optional tests. Use `--require-full` after your full collection to
reject a pilot or partial run; omit it for an intentional small validation.

The v2.2.1 release builder requires full source/ML verification and packages all
17 analysis tables plus both ML views. It preserves table bytes and the extraction
manifest. Required supporting texts/schema documents are bundled into
`enrichment/supporting_assets.zip`; caches/checkpoints are excluded. Expand that
bundle using `python -m scripts.release_data restore-support PATH` before running
the standalone enrichment verifier on a downloaded release. ML regeneration does
not require expanding it. See [RELEASING.md](RELEASING.md) for publication steps.

Research coverage reports can be regenerated with
`python -m scripts.research_report --data-dir data --output reports/research`.
They summarize source completeness, linkage uncertainty and feature missingness by
year, state/territory and EF class. See [research readiness](RESEARCH_READINESS.md)
for measured counts, biases, intended uses and conclusions the dataset cannot support.
