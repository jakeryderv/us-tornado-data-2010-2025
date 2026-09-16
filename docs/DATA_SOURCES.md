# Data sources, attribution, and reuse

The [MIT code license](../LICENSE) covers the source code and original project
text. It does not claim ownership of, or relicense, source records, government
material, or third-party content. The same distinction applies to source-derived
counts, figures, and candidate records included in `reports/`.

This is an independent research compilation, not an official NOAA, NWS, NCEI,
or U.S. Census Bureau product. Collection, filtering, format conversions, and
interpretations are documented in [DATASET.md](DATASET.md). Preserve the original
source attribution when using those outputs.

| Source organization | Data incorporated | Source reference |
|---|---|---|
| NOAA / NWS Storm Prediction Center | Historical tornado tracks and ratings | [SPC severe weather database](https://www.spc.noaa.gov/wcm/#data) |
| NOAA National Centers for Environmental Information | Storm Events details, fatalities, locations, and tornado extracts | [NCEI Storm Events](https://www.ncei.noaa.gov/stormevents/) |
| NOAA National Centers for Environmental Information / NWS | Event Footprint Catalog: DAT/Storm Events tornado damage footprints | [EFC source and methodology](https://www.ncei.noaa.gov/products/event-footprint-catalog) |
| U.S. Census Bureau, Population Division | County population and housing-unit estimates, vintages 2020 and 2025 | [Population and housing estimates](https://www.census.gov/programs-surveys/popest.html) |
| U.S. Census Bureau | Generalized 2020 county boundaries at 1:5,000,000 | [2020 cartographic boundary files](https://www.census.gov/geographies/mapping-files/2020/geo/carto-boundary-file.html) |

## NOAA and NWS material

The local, unreleased [enrichment pipeline](ENRICHMENT.md) additionally uses
NOAA NCEI SWDI Level III-derived detections and NWS warnings
redistributed by Iowa State University's Iowa Environmental Mesonet, USGS Annual
NLCD land cover/impervious grids, Census ACS 5-year tract estimates, and Census
TIGER/Line tract boundaries. The enrichment guide links each original service
and describes extraction, units and limitations. Credit both NWS and IEM for
the warning archive, and retain USGS and Census product/vintage attribution.
These additions are not included in the existing v2.1.0 publication. Source
material remains subject to its own notices; the project's MIT license applies
to the collection/feature code and original documentation.

The [NWS use policy](https://www.weather.gov/disclaimer) describes its web
information as public domain unless otherwise noted. It permits use without
charge subject to its conditions, including not claiming ownership of NWS
information, implying government endorsement, or presenting modified content as
an official government product. Source NWS material incorporated here is not
subject to the project's copyright claim. Third-party content can have separate
terms; preserve source notices. This project does not collect DAT photos or
commercial basemap imagery.

The Footprint Catalog derives tornado footprints from NWS DAT surveys and NCEI Storm Events. Credit those underlying sources as well as the catalog. Its transformations and our format conversions do not create independent observations or establish complete historical coverage. Retained NOAA documentation describes source limitations and missing-value conventions.

## Census material

Census's [research transparency policy](https://www2.census.gov/foia/ds_policies/ds027.pdf)
states that data and works created by Census Bureau employees generally are not
subject to copyright protection within the United States. Source-specific and
third-party notices still apply; the project's MIT license does not replace them.

Follow the [Census citation guidance](https://www.census.gov/about/policies/citation.html):
identify the Bureau, product/table, vintage, URL, and access date. Credit the
Bureau for original data and identify this project as responsible for derived
outputs. The county/year CSV and converted GeoJSON are project transformations;
Census did not produce or endorse this combined collection.

## Reproducible attribution

For work based on this collection, cite the original sources above and the
specific project commit or dataset release used. The source manifests
and per-file metadata under `data/` retain exact URLs, retrieval times, byte
sizes, and SHA-256 hashes. Those files are generated locally and are not included
in the GitHub code repository. Pinned Census download URLs and extraction rules
are listed in [DATASET.md](DATASET.md).

The GitHub repository is the code and methodology reference:
<https://github.com/jakeryderv/us-tornado-data-2010-2025>.
The frozen data is available on
[Hugging Face](https://huggingface.co/datasets/jakeryderv/us-tornado-data-2010-2025)
and [Kaggle](https://www.kaggle.com/datasets/jakevanslyke/us-tornado-data-2010-2025).
Use the revisions and checksums in the [v2.1.0 release receipt](https://github.com/jakeryderv/us-tornado-data-2010-2025/blob/main/release/v2.1.0.json)
and the `CITATION.cff` included with the snapshot when citing this collection.

## ERA5 attribution for the deferred extension

[ERA5 is deferred](ERA5.md) and excluded from the default/next release scope.
It is Copernicus Climate Change Service data produced by ECMWF. Its CDS
single-level and pressure-level catalogues carry a CC-BY licence; it must not be
presented as U.S. Government Works. Preserve the product citations, retrieval
snapshot and applicable licence in an expanded release. Existing v2.1.0 host
metadata still describes only the published NOAA/Census backbone.

- [ERA5 single levels, DOI 10.24381/cds.adbb2d47](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels)
- [ERA5 pressure levels, DOI 10.24381/cds.bd0915c6](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels)
