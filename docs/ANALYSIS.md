# Analysis tables

Starting with **v1.1.0**, `analysis/` provides three small tables (about 1.4 MB
combined) alongside the full source collection. These are conveniences for
inspection and research, not a cross-source joined training dataset.

| File | Rows | Unit |
|---|---:|---|
| `tornadoes.parquet` | 20,164 | One retained SPC historical track |
| `county_context.parquet` | 50,294 | One Census county/year |
| `annual_summary.csv` | 16 | One year, 2010–2025 |

`analysis/manifest.json` records input hashes, output hashes, field types, the SPC
field mapping, library versions, and reconciliation results. Original source
files remain intact. The release manifest covers the analysis files as well.

## Load

With `pandas` and `pyarrow` installed, use paths relative to your downloaded
Hugging Face snapshot or Kaggle dataset folder:

```python
import pandas as pd

tornadoes = pd.read_parquet(root / "analysis/tornadoes.parquet")
county_context = pd.read_parquet(root / "analysis/county_context.parquet")
annual_summary = pd.read_csv(root / "analysis/annual_summary.csv")
```

Kaggle exposes `analysis/` directly as well as retaining identical copies inside
`release.zip.bin`. You do not need the full source archive to load these tables.
The older v1.0.0 release does not contain this layer.

## Tornado fields and transformations

All SPC rows are retained and sorted by `(year, spc_event_id)`. The selected
columns have descriptive names; the full original CSV remains the source for
other fields. No event matching, segment splitting, or deduplication is performed.

| Analysis field | Source / meaning |
|---|---|
| `tornado_id` | `spc:<year>:<om>`; unique within this snapshot, not an NCEI/DAT ID |
| `spc_event_id` | Opaque `om` string; retain suffixes and leading zeros |
| `year` | `yr` |
| `start_date`, `end_date` | `date`, `edat`, stored as timezone-naive date values at midnight |
| `start_time`, `end_time` | `time`, `etime`, retained as clock-time strings |
| `spc_timezone_code` | `tz`; 3=CST, 9=GMT, 0=unknown; no automatic UTC conversion |
| `state`, `state_fips` | `st`, `stf`; state FIPS padded to two characters |
| `spc_rating_code` | Original `mag`, including `-9` |
| `ef_rating` | Nullable integer 0–5; `-9` becomes null |
| `ef_rating_known` | Whether `ef_rating` is non-null |
| `injuries`, `fatalities` | `inj`, `fat`, preserved counts |
| `start_latitude`, `start_longitude` | `slat`, `slon`, decimal degrees |
| `end_latitude`, `end_longitude` | `elat`, `elon`, decimal degrees |
| `start_coordinates_valid`, `end_coordinates_valid` | Both coordinates present, latitude within ±90, longitude within ±180, and neither zero |
| `path_length_miles` | `len`, miles |
| `path_width_yards` | `wid`, yards |
| `states_affected` | `ns` |

The coordinate flags are basic plausibility checks, not location verification;
coordinate values themselves remain unchanged. The dates and times do not form
UTC timestamps. SPC's `state` does not expand multi-state tracks into separate
state records. Do not assume identifiers are stable across upstream revisions.

The original CSV retains the redundant month/day fields, source loss amounts,
state/segment flags, county codes, and other fields omitted from this selected
view. Loss amounts are not converted into dollars, inflation-adjusted, or treated
as known zero damage. SPC's linked format specification is older than the current
archive; this layer intentionally avoids interpreting those monetary fields.

## County context

Columns retain their existing names: `year`, `county_fips`, `state_name`,
`county_name`, `population`, `housing_units`, `estimate_vintage`, and
`map_2020_fips_present`. FIPS is a five-character string; counts are nullable
integers and the map-presence flag is boolean. Rows are sorted by year and FIPS.
No values are imputed, and no tornado/county join is performed.

Population and housing are July 1 county estimates, not the people or buildings
struck. Vintage 2020 supplies 2010–2019; vintage 2025 supplies 2020–2025. The fixed
2020 map is generalized and does not contain nine newer Connecticut planning-region
codes. Map-code presence elsewhere does not prove unchanged boundaries.

## Annual summary rules

- `spc_tracks`: number of retained SPC rows in that year.
- `spc_ef0` through `spc_ef5`, plus `spc_ef_unknown`: exhaustive, non-overlapping
  counts that sum to `spc_tracks`.
- `spc_known_ef_fraction`: known-rated SPC rows divided by all SPC rows; blank
  if a valid annual partition has no tracks. This is label availability, not
  historical tornado-detection completeness.
- `ncei_tornado_details_rows`, `ncei_tornado_fatalities_rows`, and
  `ncei_tornado_locations_rows`: row counts of the corresponding tornado extracts,
  grouped by their existing source-year files. Fatalities *rows* are not a sum of
  people killed. These records are not aligned to SPC tornado identities.
- `dat_points`, `dat_lines`, `dat_polygons`: feature counts from complete annual
  DAT indexes, including unknown and non-tornado categories. They are not tornado
  counts, and are not divided by SPC counts to claim matching coverage.
- `census_county_rows`: rows of county context for that year.
- `census_counties_without_2020_map`: county/year rows whose saved map-code flag
  is false; not a count of tornadoes with missing geography.

Missing required source files cause the build to fail. An available empty
partition can have a zero count. Sources use different units and date conventions;
never sum across source columns to count tornadoes.

## Rebuild and verify

```sh
uv sync --locked
uv run python scripts/build_analysis.py
```

The command verifies the existing full source collection before building the
analysis files. It performs no downloads. Output defaults to `data/analysis/`;
use `--data-dir` or `--output` to choose another collection/output folder.
The three tables and their manifest are replaced on rebuild; original sources
are untouched. After refreshing source data, rerun this command before inspecting
analysis results. The inspection notebook detects stale analysis inputs.

Release builds regenerate this layer from their freshly copied, verified source
snapshot. Parquet round-trip checks verify values and types, while annual totals
reconcile with the full track and county tables. Tests cover unknown labels,
opaque identifiers, leading-zero FIPS, missing counts, and missing partitions.

For EF classification, establish eligible predictors and grouping before splitting
data. Damage characteristics, casualties, and survey-derived values may leak the
rating process. This layer supplies neither a model split nor pre-storm forecasting
inputs. See [the dataset card](DATASET_CARD.md) for broader limitations and sources.
