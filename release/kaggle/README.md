# Kaggle presentation and usability metadata

These files describe the Kaggle listing, independently of the frozen data payload.
The listing reached **10.00/10 usability** on September 15, 2026. The current data is Kaggle version **3**, corresponding to dataset release
**v1.1.0**, also verified at **10.00/10**; see [its receipt](../v1.1.0.json).
The initial score receipt below describes version 2.

- `metadata.json`: source provenance, fixed-snapshot update frequency, descriptions
  for all fifteen downloadable files and all analysis table columns, and cover-image reference.
- `dataset-cover-image.png`: a map of actual SPC start locations against the
  collected Census county map, cropped to the contiguous U.S.; no additional data source.
- `kernel-metadata.json`: publishing configuration for the public CPU-only example.
- `usability.json`: verified listing score, data version, and notebook run identity.

The [public notebook](https://www.kaggle.com/code/jakevanslyke/us-tornado-data-getting-started)
reads tables directly from the ZIP archive without extraction, checks the pinned
archive/manifest hashes, and inspects all five sources. Its source is
[`notebooks/kaggle_getting_started.ipynb`](../../notebooks/kaggle_getting_started.ipynb).
It is standalone and intentionally pins the original v1.0.0 / Kaggle 2 snapshot.
The current analysis layer is demonstrated by the repository quickstart and local
inspection notebook. Do not rerun the older public notebook against version 3
without updating its archive/manifest pins.

## Refresh listing metadata

The saved `metadata.json` is the source for file and column descriptions. For
**v1.1.0**, the API rejected column metadata with nested file paths; basename
requests returned success without changing the live descriptions. All new or
changed file descriptions and all **50 analysis column descriptions** were saved
and verified through the Data Explorer's **Edit file description** UI instead.
Select all displayed columns before editing each table. The listing returned to
**10.00/10 usability** without changing any data files.

The export/update commands below remain useful for general listing metadata.
Do not assume API success means file or column descriptions were saved: verify
those fields in the UI. The initial v1.0.0 listing accepted size-inclusive file
entries; current nested-file behavior requires the UI fallback described above.

```sh
uv run --group publish kaggle datasets metadata jakevanslyke/us-tornado-data-2010-2025 -p dist/kaggle-listing
```

The following uses the current version-3 staging folder. Set
`staged` to the output of `release_data.py stage kaggle` for a future release.

```python
import json
import shutil
from pathlib import Path

folder = Path("dist/kaggle-listing")
staged = Path("dist/v1.1.0/kaggle")
path = folder / "dataset-metadata.json"
exported = json.loads(path.read_text())
metadata = exported.get("info", exported)
overrides = json.loads(Path("release/kaggle/metadata.json").read_text())
metadata.update(overrides)
metadata["data"] = [
    {"name": resource["path"], "description": resource["description"],
     "totalBytes": (staged / resource["path"]).stat().st_size}
    for resource in overrides["resources"]
]
metadata.pop("resources", None)
path.write_text(json.dumps(metadata, indent=2) + "\n")
shutil.copy2("release/kaggle/dataset-cover-image.png", folder)
```

```sh
uv run --group publish kaggle datasets metadata jakevanslyke/us-tornado-data-2010-2025 --update -p dist/kaggle-listing
```

The update frequency is `never`: this is a fixed study snapshot with no scheduled
refresh. Deliberate future corrections can still be separately versioned. Do not
promise recurring data updates just to fill the field.

Kaggle's separate **Collection methodology** field was saved through **Edit
Provenance** in the UI; the installed CLI does not expose that field. Its text is:

> Collected from public NOAA/NWS and U.S. Census Bureau endpoints with a reproducible Python workflow. SPC tracks are filtered to 2010–2025, retaining unknown ratings. NCEI keeps 48 original annual all-hazard tables and derives tornado details plus fatalities/locations matched by EVENT_ID. DAT selects every date-matching point, line, and polygon using complete object-ID inventories and verified batches; photos are excluded. Census population and housing tables are reconciled by county FIPS and year, using vintage 2020 for 2010–2019 and vintage 2025 for 2020–2025. The generalized 2020 county KML is converted to GeoJSON. Source URLs, retrieval times, schemas, file hashes, and validation reports are preserved. The release is verified against a shared checksum manifest. No cross-source tornado join, geographic crosswalk, or model split is supplied. Coverage gaps and unknown values remain explicit. Collection code and release receipt: https://github.com/jakeryderv/us-tornado-data-2010-2025

## Reproduce the cover and notebook

```sh
uv run python scripts/plot_dataset_cover.py
TORNADO_RELEASE_ARCHIVE="$PWD/dist/v1.0.0/kaggle-preserved/release.zip.bin" uv run jupyter nbconvert --to notebook --execute notebooks/kaggle_getting_started.ipynb --output /tmp/tornado-kaggle-example.ipynb
uv run --group publish kaggle kernels push -p release/kaggle --timeout 300
uv run --group publish kaggle kernels status jakevanslyke/us-tornado-data-getting-started
```

The notebook publishing configuration attaches the dataset, disables GPU/internet,
and makes the notebook public. Its hash checks pin the input bytes even though
Kaggle's CLI attachment uses the dataset slug. When releasing changed data, update
those pins intentionally and validate the notebook before publishing a new version.
Kaggle kernel outputs stay on Kaggle; the repository stores the notebook without outputs.

Usability measures listing completeness. It is not a scientific validation score
or evidence of complete tornado coverage.
