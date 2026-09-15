# US Tornado Data, 2010–2025

A reproducible collection of U.S. tornado records, damage surveys, and county
population/housing context from NOAA and the U.S. Census Bureau. It supports
exploratory research on recorded tornado intensity, survey coverage, and exposure.
This is an independent compilation, not an official government product.

## Start with the analysis tables

Release **v1.2.0** provides nine consolidated tables, an annual summary, and a
provenance manifest under `analysis/`, totaling about **38 MB** of table data.
Start with **`tornadoes.parquet`**: 20,164 SPC tracks in about 0.9 MB. Additional
files provide NCEI tornado details/fatalities/locations, DAT points/lines/polygons,
Census county/year estimates, and county boundaries. Original source files remain
intact. These tables retain separate record types and do not imply verified
cross-source matches. See [ANALYSIS.md](ANALYSIS.md) for the complete source-to-file
mapping, loading examples, field definitions, and aggregation rules.

With `pandas`, `pyarrow`, and `huggingface-hub` installed:

```python
from pathlib import Path
from huggingface_hub import snapshot_download
import pandas as pd

root = Path(snapshot_download(
    "jakeryderv/us-tornado-data-2010-2025", repo_type="dataset",
    revision="v1.2.0", allow_patterns=["analysis/tornadoes.parquet", "ANALYSIS.md"],
))
tornadoes = pd.read_parquet(root / "analysis/tornadoes.parquet")
```

Use `allow_patterns=["analysis/*", "ANALYSIS.md"]` to fetch all consolidated
tables. The four geometry tables use GeoParquet and load with
`geopandas.read_parquet`; ordinary tables load with `pandas.read_parquet`.

The [Kaggle mirror](https://www.kaggle.com/datasets/jakevanslyke/us-tornado-data-2010-2025)
also exposes these analysis files directly. The complete original-source collection
is supplied in `release.zip.bin` on Kaggle and as direct files on Hugging Face.
The [public getting-started notebook](https://www.kaggle.com/code/jakevanslyke/us-tornado-data-getting-started)
demonstrates inspecting the original v1.0.0 snapshot; its older release pins are
intentional. Use this card's v1.2.0 analysis paths for the newer convenience layer.

This is a fixed 2010–2025 study snapshot with no scheduled refresh. Deliberate
corrections or additions receive a new release version. Pin a host revision and
retain the matching release receipt when reproducing an analysis.

## Contents and units of observation

| Source | Unit | Snapshot contents |
|---|---|---|
| SPC historical tornado database | Historical single-track record | 20,164 tracks, including 1,525 unknown ratings |
| NCEI Storm Events | County/event segment and related records | 23,189 tornado detail rows, 1,352 fatalities rows, 35,920 locations rows |
| NWS Damage Assessment Toolkit (DAT) | Survey feature | 218,231 points, 11,585 lines, 10,009 polygons |
| Census Population Estimates Program | County/year | 50,294 population/housing rows across 2010–2025 |
| Census Cartographic Boundary Files | County/county-equivalent geometry | 3,234 simplified map features from 2020 |

NCEI's 48 original annual compressed tables are also retained. These contain all
hazards: 1,037,691 details rows, 14,711 fatalities rows, and 950,862 location rows.
Their tornado extracts are provided separately. **Counts across sources must not
be added to count tornadoes.** No cross-source event join, deduplication, or model
train/test split has been performed.

## Time and geographic scope

Events and survey features are selected for **2010–2025 inclusive**. This fixed
study window lies within the Enhanced Fujita era; 2010 is not the EF transition
date or a demonstrated DAT-completeness threshold. Census estimates are July 1
values, using vintage 2020 for 2010–2019 and vintage 2025 for 2020–2025. The county
map is a fixed **2020**, **1:5,000,000** display layer, not an annual boundary series.

The NOAA collection uses the source's geographic scope without an additional
state filter. Census estimates cover the 50 states and DC; the map additionally
contains Puerto Rico and other U.S. territories. DAT coverage is geographically
and temporally uneven. Dataset availability is not evidence that every tornado
or damage observation was recorded.

## File layout

Paths below are relative to the Hugging Face dataset root or the extracted Kaggle archive:

- `analysis/`: nine main tables, an annual summary, and an input/output checksum manifest.
- `ANALYSIS.md`: field dictionary, units, missingness, and aggregation rules.
- `spc/tornadoes_2010_2025.csv`: primary historical track catalog.
- `ncei_storm_events/raw/*.csv.gz`: original annual all-hazard archives.
- `ncei_storm_events/tornado/<year>_<table>.csv`: details/fatalities/locations extracts.
- `nws_dat/<year>/<points|lines|polygons>/index.json`: complete batch inventory;
  read all listed GeoJSON batches, not one sample batch.
- `nws_dat/*_schema.json`: source field definitions; `service.json`: service metadata.
- `census_population/raw/`: four pinned population/housing CSV/XLSX sources.
- `census_population/county_context_2010_2025.csv`: combined county/year context.
- `census_boundaries/raw/`: original Census KML ZIP.
- `census_boundaries/counties_2020_5m.geojson`: converted county map.
- `download_manifest_2010_2025.json`, `census_manifest_2010_2025.json`,
  `quality_summary_2010_2025.json`, and `download_verification_2010_2025.json`:
  collection provenance and saved verification.
- `release_manifest.json`: version, code revision, source snapshots, file sizes,
  and SHA-256 for every shared release file except the manifest itself.
- `SHA256SUMS`: checksums for those files plus the release manifest.
- `schema.json`: complete CSV headers and preserved DAT field definitions.

A release contains the active files named by the collection manifests plus their
supporting metadata. Unreferenced cache files, incomplete transfers, and local
credentials are excluded. The release manifest supplies the exact file count and
size; the full local collection is approximately 493 MB including the consolidated tables before host compression.

## Fields and interpretation

**SPC:** `(yr, om)` identifies a track within this snapshot. `mag` retains the
published rating code; `-9` is unknown and must not become EF0. `slat/slon` and
`elat/elon` are endpoints, `len` is path length in miles, and `wid` is maximum
width in yards. `inj/fat` record injuries/fatalities. Preserve `date`, `time`,
and `tz`; do not assume times are UTC. The schema also retains source damage and
segment flags. See the SPC specification linked below for all fields.

**NCEI:** `EVENT_ID` and `EPISODE_ID` identify source records/episodes, not SPC IDs.
`EVENT_TYPE` selects the tornado details; related tables are retained by matching
`EVENT_ID`. `TOR_F_SCALE`, start/end times, `STATE_FIPS`, `CZ_TYPE`, `CZ_FIPS`,
coordinates, casualties, damage strings, and narratives remain source-native.
Check geography type and code vintage before county joins. Source damage strings
are not converted into numeric dollars by this collection.

**DAT:** GeoJSON geometry uses WGS84 longitude/latitude; date attributes such as
`stormdate` remain epoch milliseconds. Fields include `objectid`, `globalid`,
`event_id`, `efscale`, damage indicator/degree fields, and available wind estimates.
Fields differ by layer; the saved schemas are authoritative. DAT `event_id` is
not NCEI's numeric `EVENT_ID`, and a point rating is not necessarily the maximum
rating of its parent tornado. Track geometry is surveyed/estimated, not a claim
of exact nationwide paths. Connecting SPC endpoints only approximates a track.

**Census context:** `county_fips` is a five-character string; preserve leading
zeros. `population` and `housing_units` are counts. Housing units are residences,
not buildings, households, occupancy, or actual storm exposure. `estimate_vintage`
identifies the release. `map_2020_fips_present` indicates only code presence in the
map. Nine newer Connecticut planning regions are absent from the fixed map;
matching codes elsewhere do not prove unchanged boundaries. No geographic
crosswalk or spatial exposure estimate is supplied. Map `ALAND/AWATER` attributes
retain source area in square meters.

## Collection and transformations

SPC's full archive is temporarily read and filtered by year; its full-archive
provenance is retained, but the full archive is not packaged. NCEI selects the
newest published creation date per requested year/table, retaining raw gzip files
and exact tornado extracts. DAT collects every date-matching feature in all three
layers, including unknown and non-tornado categories, through complete object-ID
inventories and verified batches. Photos are not downloaded.

Census files are pinned by release. Population and housing are reconciled by
FIPS/name inventories, with explicit release years. KML is converted to GeoJSON
while preserving polygon parts and holes. Unknown ratings and geographic gaps
are preserved rather than zero-filled. No weather radar, forecast model,
satellite, building-footprint, ACS tract, or land-cover data is included.

## Quality, limitations, and intended use

The collection has been locally checked for hashes, lengths, year selection,
exact NCEI filtering, DAT object-ID completeness, and Census source-to-output
agreement. Release verification independently checks the packaged bytes.
These checks establish retrieval and transformation integrity, not historical
completeness or verified associations between sources. Source agencies may
revise records after the saved snapshot; use a pinned host revision/version.

SPC has 18,639 known EF ratings: EF0 9,197; EF1 7,149; EF2 1,776; EF3 426;
EF4 83; EF5 8. Class imbalance and unequal DAT adoption materially affect analysis.
Do not use DAT presence as an inclusion requirement for a representative master
catalog. The DAT service describes survey data as preliminary.

The dataset suits retrospective descriptive research and carefully designed
experiments predicting recorded EF ratings. Damage, casualties, wind estimates,
and narratives can encode the rating process and leak the target. Establish
feature eligibility and group related events before train/test splitting. It
contains no predefined benchmark split and does not provide atmospheric inputs
for forecasting tornado intensity before damage occurs. County averages cannot
identify the people or buildings actually struck. Fixed generalized boundaries
are unsuitable for precise path/building intersections.

## Sources, reuse, and citation

- [NOAA/NWS SPC historical database](https://www.spc.noaa.gov/wcm/#data) and
  [field specification](https://www.spc.noaa.gov/wcm/data/SPC_severe_database_description.pdf).
- [NOAA NCEI Storm Events](https://www.ncei.noaa.gov/stormevents/) and
  [bulk archive](https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/).
- [NOAA/NWS DAT service](https://services.dat.noaa.gov/arcgis/rest/services/nws_damageassessmenttoolkit/DamageViewer/MapServer).
- [U.S. Census Bureau population estimates](https://www.census.gov/programs-surveys/popest.html).
- [U.S. Census Bureau 2020 cartographic boundaries](https://www.census.gov/geographies/mapping-files/2020/geo/carto-boundary-file.html).

The data designation is **U.S. Government Works**, subject to source-specific
notices and third-party exceptions. The MIT license covers project code and
original documentation, not ownership of government records. See the packaged
`DATA_SOURCES.md`, the [NWS reuse policy](https://www.weather.gov/disclaimer), and
[Census citation guidance](https://www.census.gov/about/policies/citation.html).
The compilation and transformations are not endorsed by the source agencies.

Cite the original agencies and the specific release, for example: Jake Van Slyke,
*US Tornado Data, 2010–2025*, version identified in `release_manifest.json`,
with its host revision and access date. `CITATION.cff` contains release citation
metadata. Code and methodology:
[us-tornado-data-2010-2025 on GitHub](https://github.com/jakeryderv/us-tornado-data-2010-2025).
No DOI is assigned. Future revisions will have a new release identifier and
checksum manifest; old versions should remain available.
