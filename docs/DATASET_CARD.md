# US Tornado Data, 2010–2025

A focused collection of NOAA tornado records and damage footprints with Census
county population/housing context. It supports retrospective exploration of
recorded EF ratings, geographic patterns, and source coverage. This is an
independent compilation, not an official government product.

## Start with the analysis tables

**v2.0.0** provides seven main Parquet tables, an annual summary, and a provenance
manifest under `analysis/`. The tables total **24.1 MB**. Start with `tornadoes.parquet`: 20,164 SPC tracks in
about 0.9 MB. The footprint and county boundary tables are GeoParquet; use GeoPandas
for geometry. Other tables load with pandas. See [ANALYSIS.md](ANALYSIS.md) for
all fields, units, missing-value conventions, and loading examples.

```python
from pathlib import Path
from huggingface_hub import snapshot_download
import pandas as pd

root = Path(snapshot_download(
    "jakeryderv/us-tornado-data-2010-2025", repo_type="dataset",
    revision="v2.0.0", allow_patterns=["analysis/tornadoes.parquet", "ANALYSIS.md"],
))
tornadoes = pd.read_parquet(root / "analysis/tornadoes.parquet")
```

Use `allow_patterns=["analysis/*", "ANALYSIS.md"]` to fetch all analysis tables.
Remove the filter for the complete source collection. Both hosts expose analysis
files directly. Kaggle additionally packages complete sources in `release.zip.bin`,
a ZIP archive whose extra suffix preserves original compressed source files.
The two hosts' shared payload bytes and checksums are identical after extraction.

## Tables and units

| File under analysis/ | Source | Rows | Unit |
|---|---|---:|---|
| `tornadoes.parquet` | SPC | 20,164 | Historical track, including 1,525 unknown EF ratings |
| `storm_events.parquet` | NCEI Storm Events | 23,189 | Tornado event/county segment |
| `storm_fatalities.parquet` | NCEI Storm Events | 1,352 | Related fatality record |
| `storm_locations.parquet` | NCEI Storm Events | 35,920 | Related location record |
| `tornado_footprints.parquet` | NOAA Event Footprint Catalog | 24,858 | Damage region; not a unique tornado |
| `county_context.parquet` | Census estimates | 50,294 | County/year population and housing |
| `county_boundaries.parquet` | Census cartographic map | 3,234 | Fixed 2020 county polygon |
| `annual_summary.csv` | Source counts | 16 | Year |

NCEI's 48 original annual all-hazard gzip tables are retained alongside exact
tornado extracts: 1,037,691 details, 14,711 fatalities, and 950,862 locations rows.
Counts across sources describe different units and must not be added to count
tornadoes. No cross-source tornado join, train/test split, or model is supplied.

## What changed in v2

The three standalone DAT survey tables and original DAT batches have been replaced
by one Footprint Catalog table and 16 annual source GeoJSON files. EFC prioritizes
DAT geometry and supplements it with Storm Events records. It contains 16,465
DAT-derived and 8,393 SED-derived footprint regions in this snapshot.

Detailed survey points, damage indicators/degrees, and original standalone DAT
lines/polygons are no longer included. Earlier v1.2.0 / Kaggle 4 remains available
for that survey detail. SPC, NCEI, and Census table schemas are unchanged. The
annual summary replaces DAT point/line/polygon counts with EFC DAT/SED footprint
counts. The [public Kaggle example](https://www.kaggle.com/code/jakevanslyke/us-tornado-data-getting-started)
loads these v2 tables with `kagglehub`; its earlier notebook versions retain the
original v1.0.0 examples.

## Scope, provenance, and limitations

The study period is 2010–2025. SPC uses its source start year/date; NCEI uses
source annual record dates; EFC uses annual file years. One 2010 EFC record has a
2011-01-01 UTC timestamp. Local/convective year and UTC year can differ at New Year.
2010 is a scope choice and EFC's first year, not the EF transition (February 2007)
or a completeness threshold.

Census July 1 estimates use vintage 2020 for 2010–2019 and vintage 2025 for
2020–2025. They cover the 50 states and DC. The fixed 2020 map also contains
territories and is generalized at 1:5,000,000. Nine newer Connecticut county codes
are absent from it. This is not a historical boundary series or a precise exposure
map. County housing counts are residences, not buildings directly struck.

EFC footprints remain damage-survey/report derived; some are reconstructed from
lines/endpoints. Multiple nested damage regions can belong to the same tornado.
The catalog uses proximity rules to supplement DAT, not verified SPC event joins.
Its generated SED IDs are not original NCEI EVENT_IDs. Keep source/year/ID provenance
and reconcile associations before modeling or combining records.

The footprint table preserves raw values, including EFU, EF3+, -99, nulls, and
blank strings. Nullable `ef_rating`/`max_ef_rating` accept only exact EF0–EF5 labels.
`width=0.99` is a missing-width display placeholder (2,682 rows); 4,114 other
widths are zero. `path_width_yards` makes placeholder, nonpositive, and missing
widths null without altering original `width`. Valid polygons are not proof of
accurate observed surface paths. Footprint area is not automatically a reliable
exposure measurement.

Keep EF-revealing narratives, rating codes, and survey-derived wind estimates out
of predictors. Post-event dimensions/impacts support retrospective classification,
not advance forecasting. Rare EF classes, related records, geographic differences,
and reporting/survey biases need explicit treatment in modeling.

## Files and reproducibility

```text
analysis/                  Seven Parquet tables, annual summary, manifest
spc/                       Selected tornado CSV and full-archive provenance
ncei_storm_events/raw/      Original annual all-hazard gzip CSVs
ncei_storm_events/tornado/  Exact tornado extracts by year/table
event_footprints/          Annual GeoJSON, cloud inventory, NOAA docs, sidecars
census_population/         Source estimates and combined county/year CSV
census_boundaries/         Source KML ZIP and converted GeoJSON
```

Collection/quality/verification manifests accompany these folders. Every download
has source URL, retrieval time, byte size, and SHA-256. EFC data URLs additionally
pin GCS object generations and are checked against upstream MD5. `schema.json`
and `analysis/manifest.json` document fields, types, mappings, and derived outputs.
`release_manifest.json` and `SHA256SUMS` inventory the frozen shared payload.

Source verification checks exact NCEI extraction, EFC annual completeness and
checksums, and Census reconciliation; Parquet round trips check values/types and
geometry bytes. Retrieval completeness does not establish historical tornado
completeness. Unreferenced caches and credentials are excluded from releases.

NOAA overwrites upstream EFC files as they change. Our releases retain snapshots;
this fixed study dataset has no scheduled refresh. Pin a host revision and use its
release receipt for reproducibility. Version identifiers need not match between
Hugging Face tags and Kaggle's automatic numeric versions.

## Sources, reuse, and citation

- [NOAA/NWS SPC historical database](https://www.spc.noaa.gov/wcm/#data) and
  [field specification](https://www.spc.noaa.gov/wcm/data/SPC_severe_database_description.pdf).
- [NOAA NCEI Storm Events](https://www.ncei.noaa.gov/stormevents/) and
  [bulk archive](https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/).
- [NOAA Event Footprint Catalog](https://www.ncei.noaa.gov/products/event-footprint-catalog), derived from NWS DAT and NCEI Storm Events.
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
