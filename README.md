# US tornado data, 2010–2025

[![Tests](https://github.com/jakeryderv/us-tornado-data-2010-2025/actions/workflows/tests.yml/badge.svg)](https://github.com/jakeryderv/us-tornado-data-2010-2025/actions/workflows/tests.yml)

A focused **2010–2025** dataset for exploring recorded EF ratings, source agreement,
damage-survey coverage, and county population/housing context. Load a frozen release
with Python, or collect current source records and inspect them locally in Jupyter.

The **v1.2.0** snapshot is published on:

- [Hugging Face](https://huggingface.co/datasets/jakeryderv/us-tornado-data-2010-2025): direct files, convenient for Python loading.
- [Kaggle, version 4](https://www.kaggle.com/datasets/jakevanslyke/us-tornado-data-2010-2025/versions/4): analysis tables available directly; full sources inside `release.zip.bin`.

The [public Kaggle getting-started notebook](https://www.kaggle.com/code/jakevanslyke/us-tornado-data-getting-started)
demonstrates the original **v1.0.0 / Kaggle 2** snapshot and shows how to read
tables directly from the archive without extraction, verify the
release, and inspect coverage, EF ratings, surveys, and county context. It runs
on CPU without internet; [its source](notebooks/kaggle_getting_started.ipynb) is
included here.

The [release receipt](release/v1.2.0.json) records pinned versions, source snapshot
dates, and verified checksums for all **2,876 shared files (493 MB)**. Kaggle's
archive is about **292 MB**; the extracted content matches Hugging Face exactly.
Kaggle version 1 was superseded because its automatic archive extraction changed
original source paths and bytes.

For example, with `huggingface-hub`, `pandas`, and `pyarrow` installed in your project
(`uv sync --locked --group publish` installs the clients in this repository):

```python
from pathlib import Path
from huggingface_hub import snapshot_download
import pandas as pd

root = Path(snapshot_download(
    "jakeryderv/us-tornado-data-2010-2025",
    repo_type="dataset",
    revision="813d8d7e9be0bc992ef48fe93598737cad263af3",
    allow_patterns=["analysis/tornadoes.parquet", "ANALYSIS.md"],
))
tornadoes = pd.read_parquet(root / "analysis/tornadoes.parquet")
```

This downloads only the **0.9 MB tornado table** and its documentation. Use
`allow_patterns=["analysis/*", "ANALYSIS.md"]` to fetch all consolidated tables
(about **38 MB** in v1.2.0), or remove the filter for the complete source collection. To fetch just the
tornado table from Kaggle with `kagglehub`:

```python
import kagglehub

path = kagglehub.dataset_download(
    "jakevanslyke/us-tornado-data-2010-2025/versions/4",
    path="analysis/tornadoes.parquet",
)
tornadoes = pd.read_parquet(path)
```

See [Kaggle download, extraction, and integrity checks](docs/RELEASING.md#verify-consumer-downloads)
for the alternative client workflow. Both downloads use local caches. These are
separate source tables and survey files; loading them does not perform event joins.

To collect from NOAA and Census yourself instead:

```sh
uv sync --locked
uv run python download_data.py --dry-run
uv run python download_data.py --verify-downloads
uv run jupyter notebook notebooks/tornado_dataset.ipynb
```

The consolidated analysis layer provides a simple starting point:

```sh
uv run python scripts/build_analysis.py
```

```python
tornadoes = pd.read_parquet("data/analysis/tornadoes.parquet")
```

Start with the 20,164 SPC tracks. Add the following tables as your work needs them:

| Source | Files under `analysis/` |
|---|---|
| SPC | `tornadoes.parquet` |
| Census | `county_context.parquet`, `county_boundaries.parquet` |
| NCEI | `storm_events.parquet`, `storm_fatalities.parquet`, `storm_locations.parquet` |
| DAT | `survey_points.parquet`, `survey_lines.parquet`, `survey_polygons.parquet` |
| Summary/provenance | `annual_summary.csv`, `manifest.json` |

These nine main tables plus the annual summary total about **38 MB**. Ordinary
tables load with `pandas.read_parquet`; survey and boundary tables use
`geopandas.read_parquet` to restore their geometry and coordinate reference system.
Identifiers and missing values are preserved. New consolidated rows carry source
file/record references. NCEI related tables support explicit event-ID joins;
SPC/NCEI/DAT event matching remains separate research work. See the
[analysis field dictionary](docs/ANALYSIS.md) for examples, units, mappings, and
limitations. After refreshing source downloads, rebuild the analysis layer.

The dataset contains:

- **SPC:** historical tornado tracks and EF labels.
- **NCEI Storm Events:** annual details, fatalities, and locations, with tornado extracts.
- **NWS DAT:** survey points, lines, and polygons; no photographs.
- **Census Population Estimates Program:** annual county population and housing units.
- **Census Cartographic Boundary Files:** one simplified 2020 national county map.

All SPC events in the requested period are retained, including unknown EF ratings.
The full data directory currently occupies roughly **470 MiB (493 MB)**. The Census
additions required 8.8 MB of source downloads and occupy about 17 MiB including
the derived county/year CSV and GeoJSON map. Counts across
sources describe different record types and must not be added to count tornadoes.

Rerun the download command after interruption to reuse verified files. Use
`--refresh` to deliberately fetch revised upstream data, and `--help` to view year,
destination, batch-size, and timeout settings. Changing settings can leave older
files on disk; manifests define the active selection.

`--verify-downloads` independently checks checksums, exact NCEI tornado extraction,
DAT object IDs, counts, and dates, plus Census source-to-output reconciliation after collection. Its report is saved as
`data/download_verification_2010_2025.json`; `pending_download` means it has not run yet.
The report states its scope (`all`, `noaa`, or `census`); a subset pass does not
verify the other sources. To verify the full existing dataset without downloading anything (writes
`reports/verification/latest.json`, leaving `data/` untouched):

```sh
uv run python scripts/verify_downloads.py
```

To add or update only the small Census sources while keeping the NOAA snapshot:

```sh
uv run python download_data.py --sources census --verify-downloads
uv run python scripts/verify_downloads.py
```

The default `--sources all` collects all five sources. `--sources noaa` collects
SPC/NCEI/DAT only and permits years outside the pinned Census range of 2010–2025.
Census estimates use vintage 2020 for 2010–2019 and vintage 2025 for 2020–2025.
The map is a fixed 2020 display layer. Geographic differences are flagged;
no tornado-to-Census joins or building-footprint downloads are performed.

`--coverage-audit` additionally regenerates local DAT audit metrics under
`reports/dat_coverage/` for 2010–2025 in the repository's default `data/`. It does not update
the written audit interpretation or chart.

`notebooks/tornado_dataset.ipynb` only reads local files. It shows collection status, source
inventory, annual coverage, EF-label balance, survey quality, county estimates,
record samples, and a county map with tornado start locations.
It can be opened while downloads are incomplete. Event matching, feature preparation,
and modeling remain separate work.

Repository layout:

```text
README.md                       Setup and usage
pyproject.toml / uv.lock         Python dependencies
download_data.py                Download CLI (at repository root)
census_data.py                  Census collection helpers
dataset_inspection.py           Read-only loading helpers
notebooks/                      Dataset inspection notebook
scripts/                        Verification, coverage audit, plotting
tests/                          Offline regression tests
docs/DATASET.md                 Source definitions and limitations
docs/DATASET_CARD.md            Shared public dataset description
release/                       Release configuration and publication receipts
dist/                          Local release staging; ignored by Git
reports/
  dat_coverage/                 Coverage report, figures, metrics, candidates
  verification/latest.json      Most recent standalone verification
  verification/history/         Preserved earlier NOAA-only checks
data/                           Local source collection; ignored by Git
```

See [reports/README.md](reports/README.md) for snapshot dates and the distinction
between current verification and historical findings. Commands above run from the
repository root; the inspection notebook also resolves the root from `notebooks/`.

Implementation files:

- `download_data.py`: command-line collection and download validation.
- `census_data.py`: pinned Census sources, table parsing, and KML-to-GeoJSON conversion.
- `dataset_inspection.py`: local loading helpers used by the notebook.
- `scripts/verify_downloads.py`: independent local integrity and completeness checks.
- [DATASET.md](docs/DATASET.md): source definitions, collection rules, and interpretation limits.

`data/download_manifest_<start>_<end>.json` covers the three NOAA sources, and
`data/quality_summary_<start>_<end>.json` describes label and survey quality.
`data/census_manifest_<start>_<end>.json` covers both Census sources, their raw
files, derived outputs, and map coverage flags.
Successful retrieval does not establish complete historical survey coverage or
verified event joins.

Dependencies are locked in `uv.lock`: Jupyter/ipykernel for notebooks, pandas
for tables, matplotlib for inspection plots, pyarrow for Parquet, and GeoPandas for the spatial analysis tables. The downloader and source
verification script use only the Python standard library. Analysis/release builds
also use pandas and pyarrow.

Offline regression checks:

```sh
uv run python -m unittest discover -s tests -v
```

GitHub Actions runs the offline tests and download dry run on Python 3.12 with
locked dependencies. CI installs Python packages but does not fetch NOAA/Census
sources or require the local dataset. Full snapshot verification is a separate
local command documented above.

The source code and original project documentation are licensed under the
[MIT License](LICENSE). NOAA/NWS and Census source material is credited separately
in [data sources and reuse](docs/DATA_SOURCES.md); the code license does not
relicense government records or third-party material. `data/` is excluded from
Git. This repository provides the collection workflow and reports; hosted
dataset releases are versioned separately from the code repository.

The [shared dataset card](docs/DATASET_CARD.md) documents contents, schemas, and
limitations for both hosts. See [release instructions](docs/RELEASING.md) for
verified packaging, platform metadata, authentication, and download checks.
Publishing clients are isolated in the optional `publish` dependency group.
