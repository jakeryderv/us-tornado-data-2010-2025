# Dataset collection notes

`download_data.py` collects SPC, NCEI Storm Events, NOAA Event Footprint Catalog,
and two Census context products. `notebooks/tornado_dataset.ipynb` only reads data.
The default period is 2010–2025. Use [README.md](../README.md) for commands.

## Scope and intended use

SPC is the primary tornado catalog. NCEI provides event details and related records;
EFC supplies optional survey/report-derived footprints. Neither footprint presence
nor a known EF rating is required for inclusion in the original SPC table.
2010 is a manageable scope choice and EFC's first available year, not a completeness
threshold or the EF transition. The EF scale began February 1, 2007.
[NWS explanation](https://www.weather.gov/mob/aboutEFScale)

Version 2 replaces the standalone DAT collection and its three analysis tables
with the Footprint Catalog. Earlier releases retain the detailed surveys.
No cross-source event matching, model splits, or exposure estimates are supplied.
Unknown ratings must not become EF0; exclude rating-revealing fields from predictors.

## Downloads and provenance

Downloads use atomic temporary files, retries for transient failures, and sidecars
with URL, retrieval time, byte size, and SHA-256. Verified caches are reused;
`--refresh` deliberately fetches revised data. SPC/NCEI catalog pages are refreshed
to discover published files. Manifests define the active selection, not arbitrary
leftover files from previous runs. The downloader and source verifier use the
Python standard library; analysis and notebooks use the locked table/GIS libraries.

## SPC: historical tornado tracks

Discover the latest `actual_tornadoes` file from SPC's catalog, then retain only `--start-year` through `--end-year`. The full archive is fetched into a temporary directory when needed. A verified existing full archive can be reused during migration; it is removed only after the subset, provenance, and final manifest have been verified. Subsequent runs reuse the filtered CSV when its checksum, source URL, and year selection match. Use `--refresh` to check for revised bytes at the same URL.

The subset's sidecar preserves the full archive's URL, retrieval time, and SHA-256 checksum, plus the subset's checksum. Yearly row counts are recorded in the run manifest. Both `yr` and `date` are checked. Requested years outside the published archive are flagged. See the [SPC format specification](https://www.spc.noaa.gov/wcm/data/SPC_severe_database_description.pdf) for column definitions, units, time conventions, and segment flags.

## NCEI: annual Storm Events tables

Choose the newest creation date (`cYYYYMMDD`) separately for each year/table. Preserve the original compressed tables. Extract tornado details using `EVENT_TYPE == Tornado`, then retain matching `EVENT_ID` rows from locations and fatalities. No cross-source joins or deduplication are performed. Missing annual tables are reported in the final manifest.

The [bulk archive](https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/) also provides the data dictionary and README. Times and damage estimates are retained exactly as published; they are not converted to UTC or numeric dollars here.

Every raw row is checked against the requested years and its annual partition using `BEGIN_YEARMONTH` (details), `YEARMONTH` (locations), or `FAT_YEARMONTH` (fatalities). The same checks therefore cover the tornado extracts. These are the source-native record dates; end times and later survey/edit timestamps are not used to define the study period.

## NOAA Event Footprint Catalog

Read the public GCS inventory for `noaa-ncei-ipg/datasets/event-catalog/tornado/`.
Require one annual GeoJSON for every requested year. Save the inventory and NOAA's
README/download documentation. Pin each data URL to the listed object generation,
then check its byte size and upstream MD5 as well as local SHA-256. A truncated
inventory, missing annual file, invalid source, duplicate annual source/object ID,
or invalid partition timestamp fails collection. Anonymous HTTPS works; no cloud
account or additional client dependency is required.

Files live under `data/event_footprints/<year>_tornado_footprint.geojson`. They
remain byte-for-byte NOAA outputs. All features are retained, including nested
regions and unknown labels. EFC source annual years select scope; UTC storm dates
may cross New Year by at most one day. Later edit/survey dates do not select events.

NOAA prioritizes DAT and fills gaps with SED (Storm Events). These remain damage
survey/report-derived footprints; they are not continuous radar-observed tornado
paths. Raw `width=0.99` means missing width filled for display; zero and negative
values also require explicit handling. Original SED EVENT_ID is not included.
The analysis layer preserves source values and adds a nullable usable-width field.
See [ANALYSIS.md](ANALYSIS.md) and the [audit](../reports/footprints/catalog_audit.md).

The upstream catalog overwrites files on updates and does not maintain a historical
catalog archive. Our hosted releases freeze exact source bytes and checksums.
[NOAA catalog and methodology](https://www.ncei.noaa.gov/products/event-footprint-catalog)

## Completion and quality

`download_manifest_<start>_<end>.json` contains all three NOAA sources and the
nested EFC inventory, documentation references, generations, counts, and quality
checks. `quality_summary_<start>_<end>.json` records SPC EF counts and annual EFC
source/placeholder-width/relationship counts. `--verify-downloads` checks the
selected sources and saves `download_verification_<start>_<end>.json`; inspect both
scope and status. A full release requires a current all-source verification.
Successful retrieval does not prove that every tornado has an accurate footprint.

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
spatial join. Surveyed/reconstructed damage regions are available in the Footprint Catalog; an SPC line between endpoints is only an approximate track.

## Census completeness and storage

`census_manifest_<start>_<end>.json` is marked `in_progress` before collection and
`complete` only after all selected raw files and both derived outputs validate.
It records pinned URLs, hashes, sizes, row counts, map feature counts, release rules,
and county codes missing from the map. `--refresh` fetches those same pinned
releases again; it does not silently switch to a newer release or map vintage.
Census collection supports subsets of 2010–2025 and fetches only the relevant decade
files plus the fixed map. Use `--sources noaa` for NOAA-only ranges starting in 2010.

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

## Consolidated analysis layer

`scripts/build_analysis.py` verifies the sources and writes seven Parquet tables
plus an annual summary under `data/analysis/`. Start with `tornadoes.parquet`.
Optional tables supply NCEI details/fatalities/locations, EFC footprints, and Census
context/boundaries. Both spatial tables use GeoParquet with original geometry.
The manifest records input/output hashes, field types, mappings, and reconciliation.

A successful rebuild removes only the three superseded generated `survey_*.parquet`
files. It does not delete source collections. Existing v1 users should use a fresh
data directory or move `data/nws_dat/` out of the active collection after verifying
v2. The release packager excludes it even if a local historical cache remains.
