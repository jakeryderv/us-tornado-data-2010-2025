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
| NOAA National Weather Service | DAT survey points, lines, and polygons; no photos | [DAT service](https://services.dat.noaa.gov/arcgis/rest/services/nws_damageassessmenttoolkit/DamageViewer/MapServer) |
| U.S. Census Bureau, Population Division | County population and housing-unit estimates, vintages 2020 and 2025 | [Population and housing estimates](https://www.census.gov/programs-surveys/popest.html) |
| U.S. Census Bureau | Generalized 2020 county boundaries at 1:5,000,000 | [2020 cartographic boundary files](https://www.census.gov/geographies/mapping-files/2020/geo/carto-boundary-file.html) |

## NOAA and NWS material

The [NWS use policy](https://www.weather.gov/disclaimer) describes its web
information as public domain unless otherwise noted. It permits use without
charge subject to its conditions, including not claiming ownership of NWS
information, implying government endorsement, or presenting modified content as
an official government product. Source NWS material incorporated here is not
subject to the project's copyright claim. Third-party content can have separate
terms; preserve source notices. This project does not collect DAT photos or
commercial basemap imagery.

The saved DAT service description identifies the survey data as preliminary and
points to NCEI Storm Data for official severe-weather statistics. Successful
retrieval and local validation do not change that status. Refer to the respective
source pages and retained metadata for product-specific notices.

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
Use the revisions and checksums in the [v1.0.0 release receipt](../release/v1.0.0.json)
and the `CITATION.cff` included with the snapshot when citing this collection.
