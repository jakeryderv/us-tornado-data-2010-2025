# Sources, attribution and reuse

This is an independent compilation, not an official NOAA, NWS, NCEI, USGS,
Census Bureau or Iowa Environmental Mesonet product. Original records remain
attributed to their providers. Filtering, cross-source matching, geometry
construction and event-level feature aggregation are this project's derived work.
The [MIT license](../LICENSE) covers project code and original documentation;
it does not claim ownership of government records or replace upstream terms.

The host designation **U.S. Government Works** describes the federal source data.
USGS marks Annual NLCD as **CC0 1.0 Universal**; IEM makes its materials public
domain and requests attribution. Source-specific notices and third-party
exceptions still apply. No DAT photos or commercial basemap imagery are included.
Preserve original notices and do not imply provider endorsement.

The released `source_registry.json` is the machine-readable source register:
provider, exact product, URL, coverage/version, actual acquisition dates, terms,
recommended citation, source-to-table mapping and supporting metadata paths.
Its template is [release/sources.json](../release/sources.json); the release builder
attaches dates from retained acquisition metadata rather than inventing an access date.

## spc: U.S. historical actual tornado tracks, 1950–2025 archive

**Provider:** NOAA / National Weather Service, Storm Prediction Center.

**Product:** [U.S. historical actual tornado tracks, 1950–2025 archive](https://www.spc.noaa.gov/wcm/#data). 2010–2025 selected from 1950-2025_actual_tornadoes.csv; exact upstream/subset hashes in sidecar.

**Coverage:** Recorded U.S. tornadoes; source year defines selection.

**Acquisition:** 2026-09-14 to 2026-09-15; exact per-file times, URLs and hashes accompany the release.

**Terms:** [NWS public-domain terms, except identified third-party material](https://www.weather.gov/disclaimer).

**Derived tables:** `tornadoes`, `events_onset`, `events_retrospective`. SPC source year/event ID defines tornado_id; final EF target, reported times/coordinates, dimensions and impacts.

**Recommended citation:** NOAA/NWS Storm Prediction Center. U.S. Tornado Database, 1950–2025 actual tornadoes, subset 2010–2025. https://www.spc.noaa.gov/wcm/#data Accessed 2026-09-14 to 2026-09-15; exact per-file timestamps and hashes in the release metadata.

## ncei: Storm Events Database: details, fatalities and locations

**Provider:** NOAA National Centers for Environmental Information / National Weather Service.

**Product:** [Storm Events Database: details, fatalities and locations](https://www.ncei.noaa.gov/stormevents/). Annual 2010–2025 export v1.0; exact per-table cYYYYMMDD revisions retained in filenames/manifest.

**Coverage:** All-hazard annual archives plus exact Tornado EVENT_ID extracts.

**Acquisition:** 2026-09-14 to 2026-09-15; exact per-file times, URLs and hashes accompany the release.

**Terms:** [NWS terms and NCEI accuracy/source constraints](https://www.ncei.noaa.gov/metadata/geoportal/rest/metadata/item/gov.noaa.ncdc:C00510/html).

**Derived tables:** `storm_events`, `storm_fatalities`, `storm_locations`, `source_crosswalk`, `tornado_counties`, `tornadoes`. Original event IDs join NCEI tables; accepted time/geometry links join SPC. Narratives/impacts remain retrospective.

**Recommended citation:** NOAA NCEI. Storm Events Database (C00510 / DSI 3910_03), details, fatalities and locations, 2010–2025; annual revision filenames identify the snapshot. https://www.ncei.noaa.gov/stormevents/ Accessed 2026-09-14 to 2026-09-15; exact per-file timestamps and hashes in the release metadata.

## efc: Event Footprint Catalog: tornado footprints

**Provider:** NOAA National Centers for Environmental Information / National Weather Service.

**Product:** [Event Footprint Catalog: tornado footprints](https://www.ncei.noaa.gov/products/event-footprint-catalog). Catalog v1.0 launched 2026; annual objects pinned by GCS generation, MD5 and SHA-256.

**Coverage:** 2010–2025 annual tornado files; DAT surveys supplemented by Storm Events reconstructions.

**Acquisition:** 2026-09-15 to 2026-09-15; exact per-file times, URLs and hashes accompany the release.

**Terms:** [NWS public-domain terms and retained source notices](https://www.weather.gov/disclaimer).

**Derived tables:** `tornado_footprints`, `source_crosswalk`, `event_areas`, `nlcd_samples`, `events_retrospective`. Catalog region IDs remain distinct from tornado IDs; accepted regions form retrospective area unions. Underlying DAT/SED attribution is preserved.

**Recommended citation:** NOAA NCEI. Event Footprint Catalog, tornado footprints, annual files 2010–2025, frozen object generations. Derived from NWS Damage Assessment Toolkit and Storm Events Database. https://www.ncei.noaa.gov/products/event-footprint-catalog Accessed 2026-09-15 to 2026-09-15; exact per-file timestamps and hashes in the release metadata.

## census_estimates: Population Estimates Program: county population and housing-unit estimates

**Provider:** U.S. Census Bureau, Population Division.

**Product:** [Population Estimates Program: county population and housing-unit estimates](https://www.census.gov/programs-surveys/popest.html). Vintage 2020: co-est2020-alldata.csv, HU-EST2020_ALL.csv; vintage 2025: co-est2025-alldata.csv, CO-EST2025-HU.xlsx.

**Coverage:** July 1 annual estimates; 2010–2019 use vintage 2020; 2020–2025 use vintage 2025; 50 states and DC.

**Acquisition:** 2026-09-15 to 2026-09-15; exact per-file times, URLs and hashes accompany the release.

**Terms:** [Census employee works generally not copyrighted in the U.S.; third-party exceptions](https://www2.census.gov/foia/ds_policies/ds027.pdf).

**Derived tables:** `county_context`, `tornado_counties`, `tornadoes`. Original county totals joined by county FIPS/year; deduplicated linked county totals are context, not counts struck.

**Recommended citation:** U.S. Census Bureau, Population Division. County population and housing-unit estimates, vintages 2020 and 2025, selected years 2010–2025; four source files identified above. https://www.census.gov/programs-surveys/popest.html Accessed 2026-09-15 to 2026-09-15; exact per-file timestamps and hashes in the release metadata.

## census_map: 2020 Cartographic Boundary Files: national county KML, 1:5,000,000

**Provider:** U.S. Census Bureau.

**Product:** [2020 Cartographic Boundary Files: national county KML, 1:5,000,000](https://www.census.gov/geographies/mapping-files/2020/geo/carto-boundary-file.html). cb_2020_us_county_5m.zip; fixed 2020 geography.

**Coverage:** U.S. states and included territories; generalized display geometry, not a historical boundary series.

**Acquisition:** 2026-09-15 to 2026-09-15; exact per-file times, URLs and hashes accompany the release.

**Terms:** [Census employee works generally not copyrighted in the U.S.; third-party exceptions](https://www2.census.gov/foia/ds_policies/ds027.pdf).

**Derived tables:** `county_boundaries`. Original KML converted to GeoJSON/GeoParquet preserving county identifiers and parts; used as contextual map.

**Recommended citation:** U.S. Census Bureau. 2020 Cartographic Boundary Files, Counties, United States, 1:5,000,000, KML (cb_2020_us_county_5m.zip). https://www.census.gov/geographies/mapping-files/2020/geo/carto-boundary-file.html Accessed 2026-09-15 to 2026-09-15; exact per-file timestamps and hashes in the release metadata.

## swdi: Severe Weather Data Inventory: NEXRAD Level III TVS, MDA and storm structure detections

**Provider:** NOAA National Centers for Environmental Information.

**Product:** [Severe Weather Data Inventory: NEXRAD Level III TVS, MDA and storm structure detections](https://www.ncei.noaa.gov/swdiws/). Products nx3tvs, nx3mda, nx3structure; exact queries/native rows and asset hashes identify the snapshot.

**Coverage:** 2010–2025 event windows, onset ±60 minutes and 20 km radius; selected detections, not all Level III products or radar uptime.

**Acquisition:** 2026-09-16 to 2026-09-16; exact per-file times, URLs and hashes accompany the release.

**Terms:** [NWS public-domain terms and source notices](https://www.weather.gov/disclaimer).

**Derived tables:** `radar_detections`, `tornado_radar`, `events_onset`, `events_retrospective`. Source product/native record IDs → proximity links → count/max aggregates. TVS MAX_SHEAR divided by 1000; other units preserved.

**Recommended citation:** NOAA NCEI. Severe Weather Data Inventory (SWDI), NEXRAD Level III Tornado Vortex Signature, Mesocyclone Detection Algorithm and Storm Structure products (nx3tvs, nx3mda, nx3structure), selected 2010–2025 event windows. https://www.ncei.noaa.gov/swdiws/ Accessed 2026-09-16 to 2026-09-16; exact per-file timestamps and hashes in the release metadata.

## iem: NWS storm-based TO/SV warning polygons, follow-up updates and original VTEC product text

**Provider:** NOAA / National Weather Service; archive provided by Iowa State University, Iowa Environmental Mesonet.

**Product:** [NWS storm-based TO/SV warning polygons, follow-up updates and original VTEC product text](https://mesonet.agron.iastate.edu/request/gis/watchwarn.phtml). Exact export parameters, product IDs, VTEC keys, original texts and asset hashes identify the snapshot.

**Coverage:** 2010–2025 event-day/previous-day archive windows; standalone CAN rows absent from this export.

**Acquisition:** 2026-09-16 to 2026-09-16; exact per-file times, URLs and hashes accompany the release.

**Terms:** [IEM materials public domain with attribution requested; NWS terms apply to original warnings](https://mesonet.agron.iastate.edu/disclaimer.php).

**Derived tables:** `warning_updates`, `tornado_warnings`, `events_onset`, `events_retrospective`. Product/polygon rows → warning/event links → latest-issued active state and lead time; receipt/complete cancellation timeline not established.

**Recommended citation:** NOAA/NWS. Tornado and Severe Thunderstorm Warnings and updates, 2010–2025. Archived by the Iowa Environmental Mesonet, Iowa State University; storm-based polygon export and original product text. https://mesonet.agron.iastate.edu/request/gis/watchwarn.phtml Accessed 2026-09-16 to 2026-09-16; exact per-file timestamps and hashes in the release metadata.

## nlcd: Annual National Land Cover Database Collection 1: Land Cover and Fractional Impervious Surface

**Provider:** U.S. Geological Survey, Earth Resources Observation and Science Center.

**Product:** [Annual National Land Cover Database Collection 1: Land Cover and Fractional Impervious Surface](https://www.usgs.gov/data/annual-national-land-cover-database-nlcd-collection-1-products). WCS Land-Cover-Native and Fractional-Impervious-Surface-Native; collection revision unresolved in saved service metadata; current catalog revision must not be assumed to identify this snapshot.

**Coverage:** Prior years 2009–2024 for tornadoes 2010–2025; CONUS native 30 m EPSG:5070 pixels.

**Acquisition:** 2026-09-16 to 2026-09-16; exact per-file times, URLs and hashes accompany the release.

**Terms:** [CC0 1.0 Universal, as marked on the USGS data release](https://creativecommons.org/publicdomain/zero/1.0/).

**Derived tables:** `nlcd_samples`, `events_retrospective`. Native WCS pixels intersect final event_areas using pixel centers; class fractions and impervious means are retrospective. Exact requests/description hashes preserve extraction identity.

**Recommended citation:** U.S. Geological Survey (USGS), 2024. Annual National Land Cover Database (NLCD) Collection 1 Products. U.S. Geological Survey data release, https://doi.org/10.5066/P94UXNTS. Land Cover and Fractional Impervious Surface, years 2009–2024; WCS collection revision unresolved. https://www.usgs.gov/data/annual-national-land-cover-database-nlcd-collection-1-products Accessed 2026-09-16 to 2026-09-16; exact per-file timestamps and hashes in the release metadata.

## Cite the compilation and the inputs used

> Van Slyke, Jake (2026). *US Tornado Data, 2010–2025*, v2.2.1 [Data set].
> https://huggingface.co/datasets/jakeryderv/us-tornado-data-2010-2025
> Specify the immutable host revision from the release receipt and your access date.

The same version is mirrored on
[Kaggle](https://www.kaggle.com/datasets/jakevanslyke/us-tornado-data-2010-2025).
`CITATION.cff` in the snapshot supplies release date, version, data URL and code
revision. No DOI has been assigned to this compilation. Cite the original products
above for the sources actually used, in addition to the compiled snapshot.
See the [release workflow](RELEASING.md) and
[v2.2.1 receipt](https://github.com/jakeryderv/us-tornado-data-2010-2025/blob/main/release/v2.2.1.json).

Raw backbone archives, source-specific extracts, linked Parquet tables, crosswalk
candidates and ML views are distinct layers. The enrichment manifest records exact
queries, asset hashes and extraction definitions; `record_provenance` maps source
rows to those assets. `ml/manifest.json` identifies source and code hashes, and
`ml/feature_dictionary.json` documents each field's sources, units, aggregation,
window and availability basis. See [research limitations](RESEARCH_READINESS.md).

ERA5 and tract-level ACS/TIGER are **not included**. Their prospective requirements
remain in [ERA5.md](ERA5.md) and [CENSUS_TRACTS.md](CENSUS_TRACTS.md); those documents
are planning references, not claims of present coverage or licenses for this release.
