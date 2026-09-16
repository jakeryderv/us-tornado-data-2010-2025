# Analysis tables

Release **v2.2.1** has **17 Parquet tables in `analysis/`**, plus the annual
summary CSV. Two generated modeling views live in `ml/`. Download caches and
collection metadata live separately in `enrichment/`.

The eight additions are `radar_detections`, `tornado_radar`, `warning_updates`,
`tornado_warnings`, `event_areas`, `nlcd_samples`, `source_coverage`, and
`record_provenance` (all `.parquet`). Their source attribution, fields and
missingness rules are in the [enrichment guide](ENRICHMENT.md). They are tracked
by `enrichment/manifest.json`, whose `table_directory` points to `../analysis`;
`analysis/manifest.json` tracks the nine backbone tables described below.

The following backbone tables are unchanged from v2.1.0.

Release **v2.1.0** provides **nine main tables**, a 16-row annual summary, and
one manifest under `analysis/`. The tables total about **29.3 MB**. Start with
`tornadoes.parquet` (1.1 MB); download the additional tables as needed.
These preserve separate units of observation; no cross-source joined training
dataset is implied. The main SPC table adds linkage summaries; the six supporting source tables retain their v2.0.0 schemas. Version 2 replaced the three DAT survey tables with one footprint table.

| Source | File | Rows | Unit |
|---|---|---:|---|
| SPC | `tornadoes.parquet` | 20,164 | Historical track |
| Census population estimates | `county_context.parquet` | 50,294 | County/year |
| Census county map | `county_boundaries.parquet` | 3,234 | Fixed 2020 map feature |
| NCEI | `storm_events.parquet` | 23,189 | Tornado event/county segment |
| NCEI | `storm_fatalities.parquet` | 1,352 | Related fatality record |
| NCEI | `storm_locations.parquet` | 35,920 | Related location record |
| NOAA Event Footprint Catalog | `tornado_footprints.parquet` | 24,858 | Damage footprint region; not a unique tornado |
| Linkage | `source_crosswalk.parquet` | 95,222 | Source/candidate pair or unmatched source |
| NCEI + Census | `tornado_counties.parquet` | 20,842 | Accepted event county/year context |
| Cross-source counts | `annual_summary.csv` | 16 | Year, 2010–2025 |

`analysis/manifest.json` records input hashes, output hashes, field types, the SPC
field mapping, library versions, and reconciliation results. Original source
files remain intact. The release manifest covers the analysis files as well.

## Load

With `pandas` and `pyarrow` installed, use paths relative to your downloaded
Hugging Face snapshot or Kaggle dataset folder:

```python
import pandas as pd

tornadoes = pd.read_parquet(root / "analysis/tornadoes.parquet")
# Optional supporting records:
county_context = pd.read_parquet(root / "analysis/county_context.parquet")
events = pd.read_parquet(root / "analysis/storm_events.parquet")
annual_summary = pd.read_csv(root / "analysis/annual_summary.csv")

import geopandas as gpd
footprints = gpd.read_parquet(root / "analysis/tornado_footprints.parquet")
county_map = gpd.read_parquet(root / "analysis/county_boundaries.parquet")
```

Kaggle exposes `analysis/` directly as well as retaining identical copies inside
`release.zip.bin`. You do not need the full source archive to load these tables.
Version v1.1.0 contains only the tornado, county-context, and annual-summary
tables; v1.0.0 contains no analysis layer.

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

## NCEI consolidated records

`storm_events.parquet`, `storm_fatalities.parquet`, and
`storm_locations.parquet` concatenate the 16 annual **tornado extracts**. The
original all-hazard archives stay in the source collection. Every source column
is retained; names become lowercase, with these descriptive exceptions:

| Source field | Analysis field / interpretation |
|---|---|
| `STATE` | `state_name` |
| `CZ_TYPE`, `CZ_FIPS`, `CZ_NAME` | `county_zone_type`, `county_zone_code`, `county_zone_name` |
| `CZ_TIMEZONE` | `source_timezone`; retained source designation |
| `TOR_F_SCALE` | `source_ef_rating`; original label retained |
| `TOR_LENGTH`, `TOR_WIDTH` | `path_length_miles`, `path_width_yards` |
| `BEGIN_LAT`, `BEGIN_LON`, `END_LAT`, `END_LON` | `begin_latitude`, `begin_longitude`, `end_latitude`, `end_longitude`; decimal degrees |
| `BEGIN_RANGE`, `END_RANGE`, `RANGE` | `begin_range_miles`, `end_range_miles`, `range_miles`; distance relative to the reported reference location |

Identifiers (`event_id`, `episode_id`, `fatality_id`) are strings. `state_fips`
is padded to two characters and `county_zone_code` to three. Check
`county_zone_type` before interpreting a code as a county; zones are retained.
`event_id` is unique in this release's event table. Both related tables retain
only event references present in the same source-year partition. This supports
explicit one-to-many NCEI joins; neither `episode_id` nor `event_id` is an SPC or
DAT identifier. `location_index` identifies a location within its source event.

Counts, date components, fatality age, and location index use nullable integers;
measurements and coordinates use nullable floating point. `damage_property` and
`damage_crops` remain strings such as `10K`; no currency conversion or imputation
is performed. `event_narrative`, `episode_narrative`, source reports, compass
azimuths, and other categorical fields remain text. Only empty CSV fields become
null: literal text such as `NA` or `Null` remains literal.

`begin_datetime_local`, `end_datetime_local`, and `fatality_datetime_local` are
parsed from the retained original date strings. These are timezone-naive source
wall-clock values, **not UTC**. The related fatality table does not acquire a
timezone implicitly; join to event context deliberately. Clock fields such as
`begin_time`, `end_time`, and `fat_time` retain their source strings.
`ef_rating` in the event table is nullable 0–5, derived only from exact `EF0`–`EF5`
labels; all original labels remain in `source_ef_rating`.

Each consolidated NCEI row adds `source_year`, `source_file`, and `source_row`.
The last is a one-based data-record number excluding the CSV header, so quoted
multiline narratives still count as one row. Rows preserve source-year/file order.
No record is deduplicated or multiplied by a join.

The [NCEI bulk format documentation](https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/Storm-Data-Bulk-csv-Format.pdf)
defines the retained source fields. `manifest.json` supplies every output column's
type and the complete source-to-output name mapping.

## Tornado footprints (v2.0.0)

`tornado_footprints.parquet` retains every feature from the 16 annual NOAA Event
Footprint Catalog GeoJSON files. `source` distinguishes DAT (16,465 regions) from
SED / Storm Events (8,393). These are **24,858 footprints, not 24,858 tornadoes**.
Nested damage regions and multiple polygons for a track remain separate.
Individual DAT damage-indicator survey points and original standalone line/polygon
layers are no longer part of this release; use v1.2.0 if those are needed.

All original property names are preserved, including case. Source column types
are listed in the analysis manifest and source-to-output mappings are identities.
The downloaded `event_footprints/README.md` supplies NOAA's complete field dictionary.

| Fields | Meaning / handling |
|---|---|
| `footprint_id` | Project key `efc:<source_year>:<source>:<objectid>`; unique in this snapshot, not a tornado key |
| `objectid`, `objectid_line` | Source object identifiers represented as strings; missing linked-line `-99` is retained |
| `source`, `source_line` | Catalog origins DAT or SED; blank/null linked-line source remains missing |
| `event_id`, `event_id_line` | Human-readable names, not original NCEI EVENT_ID; SED names are blank in this snapshot |
| `globalid`, `globalid_line`, `path_guid` | Original survey GUIDs where available; SED records have generated object IDs and no original NCEI ID column |
| `efscale`, `max_efscale`, `efnum`, `efscale_line`, `efnum_line` | Original labels/codes, including EFU, EF3+, and sentinels |
| `ef_rating`, `max_ef_rating` | Nullable Int8 for exact EF0–EF5 labels only; regional EF and catalog maximum respectively, not reconciled SPC targets |
| `parents`, `children` | Original arrays of footprint object IDs describing damage-region relationships; use year/source context, not an automatic event grouping |
| `width`, `length`, `width_line`, `length_line` | Original catalog path width in yards and length in miles; values/sentinels unchanged |
| `width_is_placeholder` | True when original `width == 0.99`, NOAA's missing-width display value |
| `path_width_yards` | Nullable positive width; 0.99, zero, negative, or missing widths become null; raw `width` remains intact |
| `startlat`, `startlon`, `endlat`, `endlon` and `_line` variants | Original coordinates; -99 sentinels retained, no coordinate imputation |
| `injuries`, `fatalities`, `maxwind`, `cropdamage`, `propdamage` and `_line` variants | Source impacts and estimated winds; -99 remains a sentinel and monetary strings are not converted |
| `area_acres`, `Shape__Area`, `Shape__Length` | Source geometry measurements; not recomputed or treated as independently observed areas |
| `stormdate`, `starttime`, `endtime`, `created_date`, `last_edited_date`, `edit_time` and `_line` variants | Original timestamp values represented as strings (including numeric sentinels/epoch milliseconds); each gains a `<original_name>_datetime_utc` parsed UTC column |
| `CZ_TIMEZONE`, `convective_day` | Source timezone designation and 12Z-to-next-12Z day label; retained as strings |
| `comments`, `qc`, `wfo`, creator/editor fields and `_line` variants | Retained narratives, survey quality flag, forecast office, and provenance |
| `source_year`, `source_file`, `source_row` | Annual file year, relative GeoJSON path, and one-based feature position |
| `geometry` | Original catalog polygon/multipolygon geometry, encoded as WKB GeoParquet |

Parsed timestamp columns map blank/-99/-99.0 to null, accept ISO timestamps and
explicit 12–13 digit epoch-millisecond values, and reject other invalid formats.
One live `edit_time` uses epoch milliseconds; the original value is retained as text.

Annual selection uses **source file years 2010–2025**. One 2010-file event has a
2011-01-01 UTC timestamp; source/local/convective year and UTC year can differ.
Validation allows at most one day beyond either annual boundary and does not
silently discard such records. Other later survey/edit dates do not define scope.

All 2,682 placeholder widths and 4,114 zero widths remain explicit. A valid polygon
does not establish observed ground-path accuracy: some catalog shapes come from
lines/endpoints, including lines with missing-width display values. Do not use
those areas as precise exposure measurements. The catalog omits original SED
EVENT_IDs; joining to NCEI/SPC requires a separate, ambiguity-aware matching study.

The catalog uses DAT first and supplements with SED according to proximity rules
(two hours / 1,000 meters in the published method). We preserve NOAA's output;
we do not repeat its matching, deduplicate regions, or claim complete tornado
coverage. See the [NOAA methodology](https://www.ncei.noaa.gov/sites/default/files/2026-08/EFC%20Data%20Information%20Sheet_v6.pdf)
and [audit](../reports/footprints/catalog_audit.md).

## County boundaries and geometry

`county_boundaries.parquet` contains all 3,234 features from the saved generalized
2020 county map. Its fields are:

| Fields | Meaning |
|---|---|
| `county_fips`, `state_fips`, `county_code` | Source `GEOID`, `STATEFP`, `COUNTYFP`; preserve leading zeros |
| `county_gnis_id`, `census_geo_id` | Source `COUNTYNS`, `AFFGEOID` strings |
| `county_name`, `county_legal_name` | Source `NAME`, `NAMELSAD` |
| `state`, `state_name`, `legal_area_code` | Source `STUSPS`, `STATE_NAME`, `LSAD` |
| `land_area_m2`, `water_area_m2` | Source `ALAND`, `AWATER`, integer square meters |
| `boundary_year` | Fixed 2020 reference year |
| `source_file`, `source_row` | Source GeoJSON path and one-based feature position |
| `geometry` | Original Polygon/MultiPolygon geometry |

Both geometry tables use **GeoParquet 1.0, WKB, OGC:CRS84** (WGS84 longitude,
latitude). Polygon parts, holes, coordinates, missing geometries, and source
geometry validity are preserved. No reprojection, simplification, repair, or
spatial join occurs. The manifest reports geometry types and null/empty/invalid
counts. Geometry WKB is checked byte-for-byte through the Parquet round trip.
Use [GeoPandas `read_parquet`](https://geopandas.org/en/stable/docs/reference/api/geopandas.read_parquet.html)
to load these as spatial tables; pandas alone returns geometry as binary values.

County context can be joined explicitly to boundaries on `county_fips` with
`validate="many_to_one"`, using a left join to retain counties absent from the
fixed map. Map-code agreement does not prove stable boundaries across years.

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
- `efc_dat_footprints`, `efc_sed_footprints`: catalog feature counts by original
  annual file and catalog source. These are damage regions, not tornado counts.
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
The consolidated tables and their manifest are replaced on rebuild; original sources
are untouched. After refreshing source data, rerun this command before inspecting
analysis results. The inspection notebook detects stale analysis inputs.

Release builds regenerate this layer from their freshly copied, verified source
snapshot. Parquet round-trip checks verify values and types, while annual totals
reconcile with all consolidated source tables. NCEI child references, EFC annual
checksums/counts and footprint IDs, and geometry WKB round trips are checked. Tests cover unknown labels,
opaque identifiers, leading-zero FIPS, missing counts, and missing partitions.

For EF classification, establish eligible predictors and grouping before splitting
data. Damage characteristics, casualties, and survey-derived values may leak the
rating process. This layer supplies neither a model split nor pre-storm forecasting
inputs. See [the dataset card](DATASET_CARD.md) for broader limitations and sources.


## Linkage columns and bridge tables (v2.1.0)

`tornadoes.parquet` keeps its original 24 SPC columns and adds accepted NCEI and
footprint counts, unresolved plausible-candidate counts, DAT/SED region counts,
linked county-year counts, missing context-join counts, population/housing sums,
`suggested_split_group`, and `linkage_available`. Zero counts mean no accepted
links, not no tornado damage. County sums deduplicate county-year keys and remain
null if no context exists or any linked value is missing; they are county totals,
not people or buildings struck.

`source_crosswalk.parquet` contains every source/candidate pair and one null-target
row for source records with no candidates. `tornado_counties.parquet` records
accepted NCEI county-year links with Census context. See [LINKAGE.md](LINKAGE.md)
for every evidence field, statuses, thresholds, and merge examples.

The complete schemas and dtypes are in `analysis/manifest.json` and `schema.json`.
All files are under `analysis/`; no second tornado table or `linkage/` directory
is needed. These additional columns are not a preselected modeling feature set.
