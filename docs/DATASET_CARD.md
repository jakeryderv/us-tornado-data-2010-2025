# US Tornado Data, 2010–2025

**v2.2.0** combines **20,164 recorded U.S. tornadoes**, linked damage footprints
and county context with radar-derived indicators, NWS warning histories and
land-cover summaries. It provides **17 linked source/supporting Parquet tables**
under `analysis/` and **two event-level modeling tables** under `ml/`.

This is an independent research compilation for recorded EF-rating analysis,
not an official government product or a complete record of every tornado.
All original tornado rows and unknown EF ratings are retained. The nine v2.1.0
backbone tables remain unchanged; this release adds radar, warnings, NLCD and
reproducible feature views. ERA5 and ACS/TIGER tract enrichment are deferred.

## Start with one modeling table

`ml/events_onset.parquet` has **20,164 rows and 30 columns** (about **1.05 MB**).
`ml/events_retrospective.parquet` has **20,164 rows and 53 columns** (about **2.82 MB**).
Download either table and `ml/feature_dictionary.json` without fetching the full
source collection. Python needs `pandas` and `pyarrow`; geometry tables additionally
use `geopandas`.

<!-- platform:huggingface -->
### Hugging Face

Install `huggingface_hub`, `pandas` and `pyarrow`, then:

```python
from pathlib import Path
from huggingface_hub import snapshot_download

root = Path(snapshot_download(
    "jakeryderv/us-tornado-data-2010-2025",
    repo_type="dataset", revision="v2.2.0",
    allow_patterns=["ml/*"],
))
```

Use `allow_patterns=["analysis/*", "ml/*"]` for all 19 tables. For strict
reproducibility, replace the tag with the immutable HF commit recorded in the
GitHub release receipt.
<!-- /platform -->

<!-- platform:kaggle -->
### Kaggle

Install `kagglehub`, `pandas` and `pyarrow`, then download just the ML files:

```python
from pathlib import Path
import kagglehub

# Kaggle numeric version 7 corresponds to shared release v2.2.0.
DATASET = "jakevanslyke/us-tornado-data-2010-2025/versions/7"
paths = {}
for filename in ("events_onset.parquet", "events_retrospective.parquet",
                 "feature_dictionary.json"):
    paths[filename] = Path(kagglehub.dataset_download(
        DATASET, path=f"ml/{filename}",
    ))
root = paths["events_onset.parquet"].parent.parent
```

`DATASET` pins this release. Kaggle numeric versions differ from the shared
release label; the release receipt records the mapping. The public getting-started notebook pins
the released version and can also run from its attached Kaggle input without
network access. Both `analysis/` and `ml/` are directly available in Data Explorer.
<!-- /platform -->

### Select features explicitly

The following works after either download example:

```python
import json
import pandas as pd

spec = json.loads((root / "ml/feature_dictionary.json").read_text())
events = pd.read_parquet(root / "ml/events_onset.parquet")
labelled = events.loc[events.target_known & events.target_ef_rating.notna()]
X = labelled[spec["onset_predictor_columns"]]
y = labelled[spec["target"]]
groups = labelled[spec["group"]]
```

Use grouped/temporal evaluation and fit imputers/scalers only on training data.
Do not use every numeric column as a predictor: targets, metadata and retrospective
fields have distinct roles. No fixed train/test split or fitted model is supplied.
See `ml/feature_dictionary.json` and `ENRICHMENT.md` for the feature contract.

## Tables and sources

All filenames below are Parquet. Tables retain their different units of observation.
`tornado_id` links event-level tables; bridge tables connect source-specific IDs.

| File under `analysis/` | Source | Rows | Contents |
|---|---|---:|---|
| `tornadoes.parquet` | SPC + linkage summaries | 20,164 | Stable tornado ID, final EF label, timing, endpoints, dimensions, casualties and county/link summaries |
| `storm_events.parquet` | NCEI Storm Events | 23,189 | Tornado county segments, narratives, damage, casualties, timing and locations |
| `storm_fatalities.parquet` | NCEI Storm Events | 1,352 | Related fatality records linked by NCEI event ID |
| `storm_locations.parquet` | NCEI Storm Events | 35,920 | Additional reported event locations |
| `tornado_footprints.parquet` | NOAA Event Footprint Catalog | 24,858 | DAT/Storm Events-derived damage regions and associated track attributes |
| `county_context.parquet` | Census Population Estimates Program | 50,294 | Annual county population and housing estimates |
| `county_boundaries.parquet` | Census cartographic boundaries | 3,234 | Generalized 2020 county polygons and identifiers |
| `source_crosswalk.parquet` | Derived time/geometry matching | 95,222 | Accepted, ambiguous and unmatched source links with decision evidence |
| `tornado_counties.parquet` | NCEI links + Census | 20,842 | Tornado-to-county associations and county/year context |
| `radar_detections.parquet` | NCEI SWDI / NEXRAD Level III-derived products | 1,805,706 | TVS, mesocyclone and storm-structure detections, locations/times and available radar indicators |
| `tornado_radar.parquet` | Derived proximity/time matching | 2,104,931 | Tornado–detection links, distances and assumed availability times |
| `warning_updates.parquet` | NWS warnings archived by Iowa Environmental Mesonet | 846,391 | TO/SV warning polygons, issuance times, native fields and resolved VTEC/text fields where linked |
| `tornado_warnings.parquet` | Derived warning association | 109,133 | Tornado–warning links, start-point coverage and issue lead times |
| `event_areas.parquet` | Footprint Catalog + SPC | 20,164 | Accepted footprint union or documented buffered-track sampling geometry |
| `nlcd_samples.parquet` | USGS Annual NLCD | 40,294 | Two prior-year products per covered event: land-cover pixel counts and impervious percentage |
| `source_coverage.parquet` | Collection bookkeeping | 60,492 | Per-event/source status, reason, job ID and extraction definition |
| `record_provenance.parquet` | Collection bookkeeping | 4,610,010 | Record-to-source-asset references |

`analysis/annual_summary.csv` contains 16 yearly backbone/coverage summaries.
The backbone inventory is `analysis/manifest.json`; the eight added tables are
inventoried by `enrichment/manifest.json`, which points to `../analysis`.

| File under `ml/` | Rows | Contents |
|---|---:|---|
| `events_onset.parquet` | 20,164 | EF target, normalized onset/location, pre-cutoff radar aggregates, warning counts/lead time and missingness |
| `events_retrospective.parquet` | 20,164 | Onset fields plus final track/impact data, radar through onset +60 minutes and land-cover summaries |

## Coverage and timing limitations

All **60,492 requested event/source jobs** were processed: radar and warnings
completed for 20,164 events each; NLCD completed for **20,147**, with **17 outside
selected CONUS coverage** explicitly marked unavailable. There are no failed jobs.
Completion does not guarantee a detection, warning, usable raster pixels or a
complete historical observing record. Inspect source statuses and feature missingness.

- **EF is a post-event damage rating**, not a direct measurement of peak tornado
  wind. Exposure, construction, survey practice and missing damage indicators
  affect the recorded label. Keep narratives, other EF fields and survey-derived
  wind estimates out of onset predictors.
- **Reported onset is a hindsight anchor.** The onset view uses final-catalog
  time/location. Radar availability is observation time plus an **assumed five-minute
  latency**, not verified real-time arrival. This supports conditional onset
  analysis, not an operational forecasting claim.
- **Radar is a nearby association.** Queries cover 20 km around the reported start
  and ±60 minutes; the onset view filters by its availability cutoff. Nearby
  detections are not confirmed parent-storm identities. Missing detections do not
  establish an absence of radar coverage. These are derived records, not Level II scans.
- **Warnings have archive limits.** Relevant TO/SV histories are linked when at
  least one polygon covers the start point. Background query rows remain in the
  source table. The IEM polygon export omits standalone cancellation rows, so it
  is not a complete VTEC timeline and some active-warning counts can be overstated.
- **Footprints are damage-derived.** DAT/Storm Events regions may be nested or
  reconstructed. The crosswalk retains uncertainty; it is not independently
  verified tornado identity. Where no accepted footprint is available, land cover
  uses a fixed **500 m buffer** around the reported endpoint track/point.
- **NLCD summaries are retrospective.** Prior-year, native **30 m** land-cover and
  impervious grids are sampled within the final event area. No-data and areas
  containing no pixel centers stay missing. No national raster grids are bundled.
- **County context is coarse.** Population/housing values describe linked whole
  counties, not people/buildings struck. Estimates use vintages 2020 and 2025;
  the fixed 2020 map is not a historical county-boundary series.
- **2010 is a study-scope choice**, not the EF transition or a completeness threshold.
  Rare EF classes, spatial/reporting biases and related records require care.

## Reproducibility and files

Both hosts share the same release manifest and checksummed payload. Kaggle also
provides the complete payload in `release.zip.bin`; extract it with Python
`zipfile`. The extra suffix preserves upstream compressed source files. HF stores
the payload files directly. Core analysis/modeling does not require the full archive.

Original backbone source files remain included. The compact additions retain
normalized source records, geometry, extraction settings, URLs, timestamps and
hashes. Required warning texts and small schema documents are bundled in
`enrichment/supporting_assets.zip` to avoid tens of thousands of tiny downloads.
Working caches, checkpoints and benchmarks are excluded.

Offline ML regeneration uses the retained linked tables. Full raw re-extraction
requires upstream downloads; checksums cannot recover subsequently removed upstream
files. To run the standalone enrichment verifier on a full downloaded payload,
first expand the support bundle with the repository's
`python -m scripts.release_data restore-support PATH` command. Ordinary release
verification checks the bundle checksum without requiring extraction.

Use `release_manifest.json`, `SHA256SUMS`, `schema.json`, `ANALYSIS.md`,
`ENRICHMENT.md`, `LINKAGE.md`, and `ml/feature_dictionary.json`. The [GitHub release receipt](https://github.com/jakeryderv/us-tornado-data-2010-2025/blob/main/release/v2.2.0.json) records the shared v2.2.0 label, immutable HF revision and Kaggle numeric
version. This is a fixed snapshot with no scheduled refresh; later changes receive
new versions, and historical releases remain available.

## Sources, reuse and citation

- [NOAA/NWS SPC tornado database](https://www.spc.noaa.gov/wcm/#data).
- [NOAA NCEI Storm Events](https://www.ncei.noaa.gov/stormevents/).
- [NOAA Event Footprint Catalog](https://www.ncei.noaa.gov/products/event-footprint-catalog), incorporating DAT and Storm Events.
- [Census Population Estimates Program](https://www.census.gov/programs-surveys/popest.html).
- [Census 2020 cartographic boundaries](https://www.census.gov/geographies/mapping-files/2020/geo/carto-boundary-file.html).
- [NOAA NCEI SWDI](https://www.ncei.noaa.gov/swdiws/), NEXRAD-derived products.
- [Iowa Environmental Mesonet warning archive](https://mesonet.agron.iastate.edu/request/gis/watchwarn.phtml), archiving NWS warning products.
- [USGS Annual NLCD](https://www.usgs.gov/centers/eros/science/annual-nlcd-data-access).

The data designation is **U.S. Government Works**, subject to source-specific
notices and third-party exceptions described in `DATA_SOURCES.md`. MIT covers
project code and original documentation, not ownership of government records.
Credit NWS/NOAA, Census, USGS and IEM's archive service as applicable. The compilation
and transformations are not endorsed by these organizations.

Cite Jake Van Slyke, *US Tornado Data, 2010–2025*, **v2.2.0**, the relevant host
revision and access date, together with the original sources. `CITATION.cff`
contains machine-readable citation information. No DOI is assigned. Code,
methodology and release receipts are maintained in
[the GitHub repository](https://github.com/jakeryderv/us-tornado-data-2010-2025).
