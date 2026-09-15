# Kaggle listing maintenance

The current collection is v2.0.0 / Kaggle 5. `metadata.json` supplies source notes,
20 file-description templates, and column descriptions. The new footprint table
replaces the three standalone DAT survey tables. The cover is generated from SPC
start points and the Census county map and labels the current source scope.

`usability.json` is historical unless its recorded revision matches the current
release; use `release/v2.0.0.json` for current publication verification. File metadata
API success does not guarantee saved UI descriptions; verify the live listing.
Nested analysis files previously required the Data Explorer UI for descriptions.

The public getting-started notebook remains a historical v1.0.0 / Kaggle 2 example.
Its stored code and hashes intentionally reference that older dataset. Use the
repository quickstart and `notebooks/tornado_dataset.ipynb` for v2. Do not attach
new data to that older notebook without updating its pins and validating it.

## Listing fields

Source attribution is in `metadata.json` under `userSpecifiedSources`. The fixed
study snapshot uses update frequency `never`; future deliberate changes get new
versions. The collection methodology should describe:

> SPC tracks are filtered to 2010–2025, retaining unknown ratings. NCEI preserves
> annual all-hazard archives and exact tornado details/fatality/location extracts.
> NOAA Event Footprint Catalog annual files replace standalone DAT surveys;
> generation-pinned downloads are verified with upstream MD5 and local SHA-256.
> Catalog geometry, origin (DAT/SED), relationship arrays, and original sentinels
> are preserved; nullable EF and usable-width helpers are added. Footprints are
> damage regions, not unique tornadoes, and SED IDs are generated. Census estimates
> are reconciled by county/year and the fixed 2020 KML map is converted to GeoJSON.
> Seven main analysis tables plus an annual summary are verified with typed and
> geometry round trips. No cross-source event join or model split is supplied.

## Update workflow

Export current metadata before applying the saved source/frequency fields:

```sh
uv run --group publish kaggle datasets metadata jakevanslyke/us-tornado-data-2010-2025 -p dist/kaggle-listing
```

Use a fresh export so unrelated listing fields are preserved. Apply source text,
current dataset-card description, and cover; upload new versions using the commands
in [RELEASING.md](../../docs/RELEASING.md). Check file descriptions and collection
methodology in the UI, especially after removing or renaming tables.

```sh
uv run python scripts/plot_dataset_cover.py
```

The cover uses existing data only. Usability is listing completeness, not a measure
of scientific validity or complete tornado coverage.
