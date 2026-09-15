# Dataset collection notes

`download_data.py` owns all network requests, raw downloads, derived extracts, provenance,
quality summaries, and optional DAT audit execution. `notebooks/tornado_dataset.ipynb` is read-only.
See [README.md](../README.md) for commands. The default event period is 2010–2025.

## Why 2010–2025, and how the sources will be used

The default **2010–2025** window is a fixed, manageable project scope within the U.S. Enhanced Fujita (EF) scale era. The EF scale became operational on **February 1, 2007**; 2008 is the first full calendar year under it. **2010 is neither the scale-transition date nor an established DAT-completeness threshold.** [NWS EF-scale explanation](https://www.weather.gov/mob/aboutEFScale)

DAT adoption expanded unevenly across NWS regions, including experimental use since 2009. A common date range does not establish common coverage. [NWS development-team history](https://ams.confex.com/ams/97Annual/webprogram/Paper312451.html)

| Source | Intended role in the EF-rating project |
|---|---|
| SPC | Tornado-level records for the primary dataset, with one record per track and a rating to be checked during preparation. |
| NCEI Storm Events | Event context and label reconciliation after linking county/event segments correctly. |
| NWS DAT | Optional survey detail, with event associations and feature quality reviewed before use. |
| Census Population Estimates | Annual county population and housing context; no event joins yet. |
| Census Cartographic Boundaries | Fixed 2020 simplified county map for display. |

**DAT availability must not determine which tornadoes enter the primary EF-rating dataset.** The [saved coverage evaluation](../reports/dat_coverage/dat_coverage_report.md) found strong differences by year, geography, and intensity. It describes a particular snapshot; use `--coverage-audit` to regenerate numeric results after fresh downloads. That option does not rewrite the report or regenerate its chart.

The download script retains unknown ratings and non-tornado DAT surveys. A separate preparation notebook should handle event matching, label filtering, feature selection, and train/test splitting. Unknown ratings must not become EF0. EF labels, derived wind estimates, and narratives revealing the rating must not leak into predictors. A local damage point's rating is not necessarily the maximum rating of its tornado.

## Download and provenance helpers

Each downloaded file has a `.metadata.json` sidecar containing its source URL, UTC retrieval time, byte size, and SHA-256 checksum. Files are written through a temporary path; an interrupted transfer cannot become a valid cached file. The script retries transient network/server errors, rejects HTML masquerading as CSV, and checks ArcGIS error responses.

Catalog pages are fetched anew to discover current filenames. Cached DAT inventories and batches represent an earlier snapshot until refreshed. NOAA can revise a live service while a download is running; batch checks catch missing IDs, but do not provide a transactionally frozen snapshot.

SPC is a derived subset: its sidecar preserves the original archive metadata under `source`, along with the selection and the retained CSV checksum. The full archive is used temporarily and is not retained. Changing the year settings creates new outputs; it does not automatically delete unrelated outputs from earlier runs.

## SPC: historical tornado tracks

Discover the latest `actual_tornadoes` file from SPC's catalog, then retain only `--start-year` through `--end-year`. The full archive is fetched into a temporary directory when needed. A verified existing full archive can be reused during migration; it is removed only after the subset, provenance, and final manifest have been verified. Subsequent runs reuse the filtered CSV when its checksum, source URL, and year selection match. Use `--refresh` to check for revised bytes at the same URL.

The subset's sidecar preserves the full archive's URL, retrieval time, and SHA-256 checksum, plus the subset's checksum. Yearly row counts are recorded in the run manifest. Both `yr` and `date` are checked. Requested years outside the published archive are flagged. See the [SPC format specification](https://www.spc.noaa.gov/wcm/data/SPC_severe_database_description.pdf) for column definitions, units, time conventions, and segment flags.

## NCEI: annual Storm Events tables

Choose the newest creation date (`cYYYYMMDD`) separately for each year/table. Preserve the original compressed tables. Extract tornado details using `EVENT_TYPE == Tornado`, then retain matching `EVENT_ID` rows from locations and fatalities. No cross-source joins or deduplication are performed. Missing annual tables are reported in the final manifest.

The [bulk archive](https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/) also provides the data dictionary and README. Times and damage estimates are retained exactly as published; they are not converted to UTC or numeric dollars here.

Every raw row is checked against the requested years and its annual partition using `BEGIN_YEARMONTH` (details), `YEARMONTH` (locations), or `FAT_YEARMONTH` (fatalities). The same checks therefore cover the tornado extracts. These are the source-native record dates; end times and later survey/edit timestamps are not used to define the study period.

## NWS DAT: damage points, lines, and polygons

Download every date-matching feature from each layer, including unknown and non-tornado categories. **Do not interpret a point's EF rating as the maximum rating of its parent tornado.** DAT's `surveytype` field may be empty; a simple filter on that field would lose records. Photos are not fetched.

For each year/layer, first save the complete object-ID inventory and compare its size with the server's count. Then request small batches by ID, check every returned ID, and preserve each batch as a WGS84 (`EPSG:4326`) GeoJSON FeatureCollection. This avoids silently accepting the first page of a capped API response. A completed `index.json` lists the batches and checksums. Dates in attributes remain ArcGIS epoch milliseconds; selection uses `stormdate` from January 1 inclusive through the following January 1 exclusive. Undated records are excluded.

Files live under `data/nws_dat/<year>/<points|lines|polygons>/`. Read all batch files listed in the index, rather than treating one batch as the whole layer. Empty years have an index with zero features. Preserved schemas describe field meanings and service time-reference metadata.

Each saved feature's `stormdate` is also checked locally against the requested period and annual partition, including when batches are reused from cache. An out-of-range or undated feature fails validation. Years with zero returned features are reported separately from unavailable data.

## Label and survey-quality summary

The script reads the retained SPC CSV and only the DAT batches listed in **the current run's manifest and indexes**. It reports rating counts by year, DAT label categories, missing/empty geometry, and missing identifiers. Detailed annual counts are saved to `data/quality_summary_<start>_<end>.json`.

DAT category names below describe the exported `efscale` field: `EF0`–`EF5`, `EF3+`, and `EFU` are counted as **tornado-labeled**; `TSTM/WIND` and `TROPICAL` as **non-tornado-labeled**; remaining values as **unknown/other**. These are field-level categories, not independently verified event types. `EFU` is unknown intensity even though it is tornado-labeled. Original label frequencies are preserved in the summary.

`event_id` is generally a human-readable DAT label, not NCEI's numeric `EVENT_ID`. `globalid` identifies a feature, while `path_guid` is a potential association field on points/polygons. Presence of an identifier does not prove that it resolves to an event or that a join is correct. Missing geometry means a null geometry or empty coordinates; this is not a full topology validation.

**Successful downloads establish retrieval completeness for the requested queries, not complete historical survey coverage.** Warnings and counts here do not filter or delete records. Label, category, identifier, and geometry limitations need review during dataset preparation.

## Completion and verification

`data/download_manifest_<start>_<end>.json` records the requested SPC, NCEI, and
DAT outputs, source provenance, and yearly coverage. The quality summary reports
labels and survey-field completeness. Use `--verify-downloads` to independently
check saved checksums, exact NCEI tornado extraction, DAT object-ID coverage,
counts, and dates. The verification report records the manifest hash so a later
collection can be distinguished from the verified snapshot.

## Census Population Estimates Program: county context

Four pinned national files provide July 1 population and housing-unit estimates:

- [Vintage 2020 population CSV](https://www2.census.gov/programs-surveys/popest/datasets/2010-2020/counties/totals/co-est2020-alldata.csv)
  and [housing CSV](https://www2.census.gov/programs-surveys/popest/datasets/2010-2020/housing/HU-EST2020_ALL.csv): select **2010–2019**.
- [Vintage 2025 population CSV](https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv)
  and [housing XLSX](https://www2.census.gov/programs-surveys/popest/tables/2020-2025/housing/totals/CO-EST2025-HU.xlsx): select **2020–2025**.

Retain raw files in `data/census_population/raw/`; write the requested years to
`county_context_<start>_<end>.csv`. Only county/county-equivalent records (`SUMLEV=050`)
are retained, covering the 50 states and DC. The current full-period output has
**50,294 county/year rows** (3,143 per year in 2010–2019; 3,144 in 2020–2025).
Use `county_fips` as a five-character string, preserving leading zeros.

Columns are `year`, `county_fips`, `state_name`, `county_name`, `population`,
`housing_units`, `estimate_vintage`, and `map_2020_fips_present`. Housing units
count residences, not buildings, households, occupied units, or surveyed damage.
The annual values are July 1 estimates, not observations on the tornado date.
These revised releases are suitable for retrospective analysis, not evidence of
what was known when an event occurred. Decade releases use different estimation
bases; changes across 2019/2020 can include a methodology/geography discontinuity.

The 2010s CSVs join by FIPS with exact name checks. The 2025 housing workbook has
county/state names rather than FIPS; the parser requires an exact, unique match
to the population file's county/state inventory before attaching a FIPS code.
It reads the published year headers and validates every count. Benson County,
North Dakota lacks the usual leading dot in that workbook and is still retained.
Missing counties or invalid counts fail collection; they are not zero-filled.

**Geographic vintage matters.** Each estimate release uses its own county definitions,
including retrospective estimates for revised geographies. These are not a historical
county-boundary time series. The nine Connecticut planning regions in vintage 2025
are absent from the fixed 2020 county map and have `map_2020_fips_present=false`.
A `true` value checks only that the code exists in the map; it does not establish
unchanged boundaries or correct compatibility with NCEI county codes. County
name/code/boundary changes require review before an event join. No crosswalk,
spatial allocation, density calculation, or tornado-to-Census join is performed.

## Census Cartographic Boundary Files: small county map

Download the [2020 national 1:5,000,000 county KML ZIP](https://www2.census.gov/geo/tiger/GENZ2020/kml/cb_2020_us_county_5m.zip)
(about 2.1 MB), retaining it under `data/census_boundaries/raw/`. Convert its WGS84
coordinates to `data/census_boundaries/counties_2020_5m.geojson` (about 6.1 MB).
Preserve every polygon part, inner ring, FIPS identifier, name, and source land/water
area attribute. The current map has **3,234 features**, including Puerto Rico and other US territories;
the population/housing estimates cover only the 50 states and DC.

This is one fixed display layer, not annual full-resolution TIGER boundaries.
Generalization makes it unsuitable for precise tornado-path/building intersections.
The notebook previews contiguous-US counties and SPC start locations without a
spatial join. Surveyed path lines and damage polygons remain available in DAT
where surveys exist; an SPC line between endpoints is only an approximate track.

## Census completeness and storage

`census_manifest_<start>_<end>.json` is marked `in_progress` before collection and
`complete` only after all selected raw files and both derived outputs validate.
It records pinned URLs, hashes, sizes, row counts, map feature counts, release rules,
and county codes missing from the map. `--refresh` fetches those same pinned
releases again; it does not silently switch to a newer release or map vintage.
Census collection supports subsets of 2010–2025 and fetches only the relevant decade
files plus the fixed map. Use `--sources noaa` for other NOAA date ranges.

The local verifier checks the selected raw source inventory and checksums, then
reconciles the complete derived table and GeoJSON against the saved CSV/XLSX/KML
inputs. File-format parsers are shared with the collector; the verification run is
local and separate. Regression fixtures independently check the decade transition,
leading-zero FIPS, missing housing failure, map gaps, polygon holes/parts, cache reuse,
and altered derived values even when their stored checksum is updated.

The Census sources download **8,810,819 bytes (8.8 MB)** and occupy about **17 MiB**
with raw files, derived outputs, and metadata. No extra Python dependency is needed:
CSV, ZIP, XLSX XML, and KML parsing use the standard library. The notebook remains
read-only and uses the existing pandas/matplotlib dependencies.
