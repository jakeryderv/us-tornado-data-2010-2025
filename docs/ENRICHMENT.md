# Linked enrichment and event feature views

The existing nine `analysis/` tables remain the backbone. Optional collection
adds radar detections, warning histories, land cover and tract
exposure under `enrichment/`. Reproducible, offline feature generation creates
two additional event tables under `ml/`; it never replaces the source tables.

**These additions are local and not part of published v2.1.0.** Initial validation
uses four events spanning 2010–2025. ERA5 is deferred; the default collection
and its ML views contain no ERA5 table or feature columns. A 20,164-row ML table is not evidence of
20,164 enriched events: inspect `source_event_count`, `source_coverage`, and the
per-source status columns. Unrequested rows remain in both views.

## Collect and rebuild

Use Python 3.12 and the locked optional dependencies:

```sh
uv sync --locked --group enrichment
# If .env does not exist, copy .env.example to .env and fill in the key locally.
uv run --group enrichment python -m enrichment.pipeline --all-events --dry-run
uv run --group enrichment python -m enrichment.pipeline --all-events --storage compact --cache-gb 5 --max-download-gb 100
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

Plan roughly **1.5–4 GB persistent data** after a completed full compact run,
and initially allow **10–15 GB working disk** including the 5 GB cache,
compressed checkpoints and table consolidation. These are planning estimates, not measurements of a full collection.
Incomplete runs retain compressed checkpoints and can use additional space.
Disk cache and network transfer limits are independent. A body larger than the
cache limit fails explicitly; raise `--cache-gb` and resume.

Without ERA5, plan for **many hours to a few days**, depending on source API
latency and retries. This is not a measured full-run duration; the pilot cannot
reliably extrapolate thousands of different radar/land/Census queries. There is
no CDS queue in the default run. Read `progress.json` for finished jobs,
transferred/cache bytes and per-source elapsed seconds. The collector processes
sources in groups, so an event is fully finished only after all requested sources
finish. Budget-limited or failed jobs are retried on the next invocation.

Rerun the same command to resume: completed extraction checkpoints do not need
the original binaries. A fully completed identical selection verifies and reuses
its consolidated tables without downloading. When every backbone event and all
five default sources finish successfully (including documented unavailable jobs), compact
mode retires that run's redundant checkpoints. Subset/partial runs keep theirs.
A failed or transfer-budget-limited run exits with code 2; inspect status/reasons.
Only one collector may use an output directory at a time (Linux/macOS file lock).

For a small check, replace `--all-events` with `--limit 4`, or repeat `--event-id`
for a chosen cohort. `--start-year`, `--end-year`, and `--sources radar warnings
nlcd acs tiger` select scope; `--data-dir` selects the backbone location.
`--output` selects a separate collection directory. The offline builder accepts
matching `--data-dir`, `--enrichment-dir`, and `--output` options.

**Each invocation materializes its selected cohort and sources.** A later small
run against the same output replaces the active normalized tables with that
selection; it does not union selections automatically. Use a separate `--output`
for experiments. Applicable extraction checkpoints remain; optional raw bodies
may be evicted. Running `--all-events` later reuses applicable checkpoints and
expands the active cohort.

The Census API key is loaded from the repository's ignored `.env` or environment.
It is excluded from persisted request URLs, identifiers and error messages.
No CDS credentials or ERA5 downloads are needed for this default workflow.
The validated ERA5 adapter is [deferred and explicitly opt-in](ERA5.md).

## Files and keys

| File under `enrichment/tables/` | Unit and links | Contents |
|---|---|---|
| `radar_detections.parquet` | One `record_id` per product/detection | Radar/cell ID, UTC observation, point, native record, normalized indicators |
| `tornado_radar.parquet` | `tornado_id` + radar `record_id` | Distance, assumed availability, association method, query asset |
| `warning_updates.parquet` | One `record_id` per warning/product/polygon | Stable VTEC `warning_id`, product issuance, known expiry/action, polygon, raw attributes and text asset |
| `tornado_warnings.parquet` | `tornado_id` + update `record_id` | All updates of relevant warnings, start-point coverage and issue lead time |
| `acs_tracts.parquet` | `acs5:period_end:GEOID` | Population, housing and mobile-home estimates/MOEs, five-year period, geography vintage, raw response |
| `tract_boundaries.parquet` | `tiger:vintage:GEOID` | Intersecting tract geometries and original attributes |
| `tornado_tracts.parquet` | `tornado_id` + `tract_id` | Exact-vintage `acs_id`, `area_id`, intersection area and both area fractions |
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
  Keep returned source rows; link only detections actually inside the radius and
  time window. Preserve raw fields. TVS `MAX_SHEAR` is converted from 10^-3/s to
  1/s; velocity indicators retain knots, reflectivity dBZ, VIL kg/m².
  The structure product is filtered by SWDI (at least 45 dBZ). This is a selected
  detection catalog, not all Level III products or volume scans. Nearby detections
  are not verified parent-storm tracks. Empty successful queries do not establish
  radar operation, full archive coverage, or absence of rotation. Suspected
  truncated responses fail explicitly rather than supplying biased aggregates.
- **[IEM warning archive](https://mesonet.agron.iastate.edu/request/gis/watchwarn.phtml):**
  NWS tornado and severe-thunderstorm warnings, including follow-up polygon
  updates and original NWS product text. Normalize the national two-day query
  response and retain original warning text; link warning histories when any polygon covers the reported start.
  Evaluate the latest update issued by onset, its polygon, and its original
  product's VTEC action/expiration. Retrospectively shortened database expiry
  fields cannot rewrite what was known earlier. Partial county cancellations
  retain the continuing storm-based polygon; unresolved VTEC cases fail.
  Missing original issuance remains null and does not become zero lead time.
- **[USGS Annual NLCD](https://www.usgs.gov/centers/eros/science/annual-nlcd-data-access):**
  prior event-year land cover and fractional impervious surface, using native
  numeric WCS GeoTIFF extracts from the USGS MRLC GeoServer. Sample 30 m,
  EPSG:5070 grids snapped to the native origin; retain counts, summaries and grid metadata. Count pixel centers within the
  event area, excluding no-data. Tiny areas may contain no centers; this is
  missing information, not undeveloped land. These selected products cover CONUS.
- **[Census ACS 5-year](https://www.census.gov/programs-surveys/acs/data/data-via-api.html):**
  the period ending one year before the event; B01003 population, B25001 housing
  units and B25024 mobile homes, with 90% MOEs. Retain all tract rows returned for
  requested states/vintages. Negative API sentinel values become null, with raw
  values preserved. A missing tract estimate is not zero.
- **[Census TIGER/Line](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html):**
  tract boundaries matching the ACS geography vintage. Extract intersecting
  geometries and attributes; full source ZIPs are disposable in compact mode. The 2009 collection is
  county-partitioned; select counties using accepted backbone county links and
  flag unavailable selection if none exists. No modern geometry substitution.

An accepted footprint union defines the retrospective area where available.
Otherwise, use a fixed 500 m buffer around the reported endpoint line/point.
The buffer is an explicit approximation, not an inferred damage width. Keep
the method and contributing footprint IDs. Census state selection uses the
start state plus accepted county-link states. The source footprint/link quality
and any omitted crossing counties limit exposure coverage.

Exposure counts sum tract estimates weighted by intersection area / whole tract
area. They assume uniform within-tract distribution, not exact people or homes
struck. Require all intersecting estimates and at least 99% geometric tract
coverage for aggregate counts; otherwise retain null. MOEs remain in source
tables; no propagated confidence interval is claimed.

## ML views and leakage contract

Both `ml/events_onset.parquet` and `ml/events_retrospective.parquet` contain
every backbone tornado once. `tornado_id` and the final `target_ef_rating` are
unchanged; unknown ratings remain null and `target_known` remains false.

- **Onset:** start coordinates and UTC calendar features; pre-cutoff radar
  detection counts/maxima; active warning counts and tornado-warning lead time;
  source status and missingness metadata. There are no environmental values in the onset view.
- **Retrospective:** the onset fields plus `post_` end/duration/path dimensions,
  casualties (metadata), radar aggregates through onset +60 minutes, land-class
  fractions, impervious percentage, weighted tract exposure and area provenance.

The cutoff is **reported tornado onset**. Radar availability is observation
time plus an assumed 5 minutes; warnings use the issued product time.
Environmental variables are deferred. The optional ERA5 extension, if explicitly
enabled later, belongs only in retrospective analysis.
Both location and onset are hindsight anchors from the final catalog: this is
conditional intensity analysis of recorded tornadoes, not proof of a deployable
tornado-detection system. Radar latency remains an assumption, not receipt evidence.

Prior-year NLCD/ACS are still retrospective predictors: the final damage area
was unknown at onset, product releases can occur later, and maps can be revised.
Final dimensions and exposure may encode the EF assessment itself. Never use
`post_` fields in an early forecast. Do not treat a source's presence/absence as
a physical intensity predictor without studying collection bias.

`feature_dictionary.json` supplies explicit onset/retrospective predictor lists,
target, ID, grouping field, column roles and assumptions. Metadata and unknown
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
the ML input snapshot and target preservation. It reports optional bodies as intentionally not retained, separately from missing/corrupt required files. A passed
verification can describe a small pilot; check `selected_events` and
`full_cohort_processed` rather than assuming a pass implies full coverage.
The full test suite also checks the deferred ERA5 adapter; `--group era5` is only
needed for those optional tests. Use `--require-full` after your full collection to
reject a pilot or partial run; omit it for an intentional small validation.

Full source collection, expanded release packaging and a new HF/Kaggle version
are separate steps after the user runs the full download. The existing release
builder requires `--backbone-only` when local enrichment exists, so it cannot
silently publish a release that appears to include these additions.
