# US tornado data, 2010–2025

[![Tests](https://github.com/jakeryderv/us-tornado-data-2010-2025/actions/workflows/tests.yml/badge.svg)](https://github.com/jakeryderv/us-tornado-data-2010-2025/actions/workflows/tests.yml)

NOAA tornado records and damage footprints with Census county population/housing
context. Load a frozen release with Python, or collect sources and inspect them
locally in Jupyter. **v2.0.0** replaces standalone DAT surveys with NOAA's Event
Footprint Catalog. Earlier releases retain the detailed survey data.

- [Hugging Face dataset](https://huggingface.co/datasets/jakeryderv/us-tornado-data-2010-2025)
- [Kaggle dataset](https://www.kaggle.com/datasets/jakevanslyke/us-tornado-data-2010-2025)
- [v2.0.0 release receipt](release/v2.0.0.json): pinned revisions and verified checksums.

## Download and load

Install `pandas`, `pyarrow`, and either `huggingface-hub` or `kagglehub` in your
project. Fetch only the 0.9 MB SPC table to begin:

```python
import pandas as pd
from huggingface_hub import hf_hub_download

path = hf_hub_download(
    "jakeryderv/us-tornado-data-2010-2025", repo_type="dataset",
    revision="v2.0.0", filename="analysis/tornadoes.parquet",
)
tornadoes = pd.read_parquet(path)
```

Or use the equivalent Kaggle release:

```python
import pandas as pd
import kagglehub

path = kagglehub.dataset_download(
    "jakevanslyke/us-tornado-data-2010-2025/versions/5",
    path="analysis/tornadoes.parquet",
)
tornadoes = pd.read_parquet(path)
```

Change the filename to download another table. To download all analysis files on
HF, use `snapshot_download(..., repo_type="dataset", revision="v2.0.0",
allow_patterns=["analysis/*", "ANALYSIS.md"])`. Both clients reuse local caches.
Full sources are direct files on HF and inside `release.zip.bin` on Kaggle.
See [release/download verification](docs/RELEASING.md) for full-collection checks.

## Tables

| Source | Files under analysis/ |
|---|---|
| SPC | `tornadoes.parquet` — 20,164 tracks, including unknown EF ratings |
| NCEI Storm Events | `storm_events.parquet`, `storm_fatalities.parquet`, `storm_locations.parquet` |
| NOAA Event Footprint Catalog | `tornado_footprints.parquet` — 24,858 damage regions from DAT/Storm Events |
| Census | `county_context.parquet`, `county_boundaries.parquet` |
| Summary/provenance | `annual_summary.csv`, `manifest.json` |

There are **seven main tables**, plus the annual summary, totaling **24.1 MB**. Use `pandas.read_parquet`
for ordinary tables and `geopandas.read_parquet` for footprints and boundaries.
The [field dictionary](docs/ANALYSIS.md) covers types, units, and missing values.

Footprints are not unique tornadoes and have not been matched to SPC. Some are
nested regions or reconstructed paths. Raw placeholder widths and unknown ratings
remain explicit; a separate usable-width column marks unusable widths as null.
The catalog does not include individual DAT damage-indicator points. See the
[scope migration and limitations](docs/DATASET_CARD.md) and [audit](reports/footprints/catalog_audit.md).

## Collect and inspect locally

Python 3.12 is pinned in `.python-version`; dependencies are locked in `uv.lock`.
The downloader and source verifier use the standard library. Notebook/analysis
packages are pandas, matplotlib, Jupyter/ipykernel, pyarrow, and GeoPandas.

```sh
uv sync --locked
uv run python download_data.py --dry-run
uv run python download_data.py --verify-downloads
uv run python scripts/build_analysis.py
uv run jupyter notebook notebooks/tornado_dataset.ipynb
```

Downloads resume using verified caches; `--refresh` fetches revised source bytes.
`--sources noaa` collects SPC/NCEI/EFC; `--sources census` collects only context.
The default is all five sources for 2010–2025. Census supports that fixed range;
NOAA-only periods must start in 2010 or later. EFC uses source annual partitions,
which can cross UTC New Year. Use `--help` for destination and timeout settings.

`--verify-downloads` saves a scope-specific report in `data/`. A full release needs
an all-source pass. To verify existing sources without downloading:

```sh
uv run python scripts/verify_downloads.py
```

The inspection notebook is read-only and detects stale analysis files. After any
source refresh, rebuild the analysis tables. A successful build removes the three
superseded generated `survey_*.parquet` files. Existing v1 users can use a fresh
`data/` directory, or move `data/nws_dat/` outside it after verifying v2. Releases
select manifest-backed sources and exclude old DAT caches.

Census supplies annual county population/housing estimates and one fixed 2020
simplified county map. These are context, not exact people/buildings struck.
No radar, photos, building footprints, geographic crosswalk, cross-source event
join, or model split is provided.

## Repository

```text
download_data.py          Collection CLI and HTTP/cache helpers
footprint_data.py         EFC inventory, generation-pinned download, validation
census_data.py            Pinned Census sources and derivations
dataset_inspection.py     Read-only inspection helpers
notebooks/               Local inspection and historical Kaggle example
scripts/                 Analysis build, verification, release packaging
tests/                   Offline regression tests
docs/                    Dataset card, field dictionary, collection/release notes
release/                 Configuration and immutable publication receipts
reports/                 Current verification and historical source audits
data/                    Local active dataset, ignored by Git
dist/                    Local staged releases, ignored by Git
```

[Reports](reports/README.md) distinguish current verification from the historical
DAT study. The [public Kaggle example](https://www.kaggle.com/code/jakevanslyke/us-tornado-data-getting-started)
is intentionally pinned to the original v1.0.0 / Kaggle 2 snapshot; use the examples
above and the local inspection notebook for v2.

```sh
uv run python -m unittest discover -s tests -v
```

GitHub Actions runs offline tests and a download dry run with locked dependencies.
Full source/host verification is performed separately for each published release.
Publishing clients live in the optional `publish` dependency group.

[MIT](LICENSE) covers project code and original text. NOAA and Census records have
separate [attribution and reuse notices](docs/DATA_SOURCES.md). This compilation is
not endorsed by the source agencies. See the [dataset card](docs/DATASET_CARD.md)
and [release workflow](docs/RELEASING.md) for reproducible citation and packaging.
