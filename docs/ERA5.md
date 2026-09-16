# ERA5: planned environmental extension

ERA5 is a future target, excluded from the current dataset, default downloads,
ML tables and next release scope. The earlier local pilot downloads, extracted
tables, checkpoints and experimental ML views have been removed. Historical
validation reports remain under
[`reports/enrichment/history/era5/`](../reports/enrichment/history/era5/)
as development evidence, not an available dataset.

## Intended contribution

[Copernicus/ECMWF ERA5](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels)
would provide consistent hourly environmental context for 2010–2025: CAPE,
near-surface temperature, moisture and winds, plus pressure-level winds and
geopotential for derived shear and storm-relative helicity. Its coarse grid
would describe the surrounding environment, not tornado-scale winds.

ERA5 reanalysis is produced after the event. Any future ERA5 features belong in
the retrospective view and must remain excluded from strict onset predictors.
Source samples, units, matching methods, missingness and provenance should stay
in separate linked tables; large downloaded subsets should remain disposable.

## Before adding it

The optional adapter and tests remain available for future work. Enabling ERA5
would require a new isolated pilot, confirmed CDS access and dataset terms,
updated coverage/runtime estimates, and explicit release attribution and licence
review. See [CDS setup](https://cds.climate.copernicus.eu/how-to-api) and
[attribution notes](DATA_SOURCES.md#era5-attribution-for-the-deferred-extension).
No CDS access is needed for the [current three-source enrichment run](ENRICHMENT.md).
