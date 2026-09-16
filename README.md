# US tornado data, 2010–2025

[![Tests](https://github.com/jakeryderv/us-tornado-data-2010-2025/actions/workflows/tests.yml/badge.svg)](https://github.com/jakeryderv/us-tornado-data-2010-2025/actions/workflows/tests.yml)

NOAA tornado records and damage footprints with Census county population/housing
context. Load a frozen release with Python, or collect sources and inspect them
locally in Jupyter. **v2.1.0** provides one enriched tornado table and eight
supporting tables, including an auditable cross-source crosswalk. Earlier releases
retain their original layouts and detailed DAT surveys.

Local development now also includes linked radar, warning, Annual NLCD, ACS tract
and TIGER tract enrichment, plus onset and retrospective ML views. ERA5 is
[deferred and opt-in](docs/ERA5.md).
These are **not yet in the published v2.1.0 release**. See the
[enrichment guide](docs/ENRICHMENT.md) for collection commands, pilot coverage,
table layout and the prediction-time contract.

- [Hugging Face dataset](https://huggingface.co/datasets/jakeryderv/us-tornado-data-2010-2025)
- [Kaggle dataset](https://www.kaggle.com/datasets/jakevanslyke/us-tornado-data-2010-2025)
- [v2.1.0 release receipt](release/v2.1.0.json): pinned revisions and verified checksums.

## Download and load

Install `pandas`, `pyarrow`, and either `huggingface-hub` or `kagglehub` in your
project. Fetch only the 1.1 MB enriched tornado table to begin:

```python
import pandas as pd
from huggingface_hub import hf_hub_download

path = hf_hub_download(
    "jakeryderv/us-tornado-data-2010-2025", repo_type="dataset",
    revision="b2b6c9ddb14a5060c9064d4f1c96459ad140e013", filename="analysis/tornadoes.parquet",
)
tornadoes = pd.read_parquet(path)
```

Or use the equivalent Kaggle release:

```python
import pandas as pd
import kagglehub

path = kagglehub.dataset_download(
    "jakevanslyke/us-tornado-data-2010-2025/versions/6",
    path="analysis/tornadoes.parquet",
)
tornadoes = pd.read_parquet(path)
```

Change the filename to download another table. To download all analysis files on
HF, use `snapshot_download(..., repo_type="dataset", revision="b2b6c9ddb14a5060c9064d4f1c96459ad140e013",
allow_patterns=["analysis/*", "ANALYSIS.md"])`. Both clients reuse local caches.
Full sources are direct files on HF and inside `release.zip.bin` on Kaggle.
See [release/download verification](docs/RELEASING.md) for full-collection checks.

## Tables

| Source | Files under analysis/ |
|---|---|
| SPC + accepted links | `tornadoes.parquet` — 20,164 tracks, EF labels, linkage counts, and county summaries |
| NCEI Storm Events | `storm_events.parquet`, `storm_fatalities.parquet`, `storm_locations.parquet` |
| NOAA Event Footprint Catalog | `tornado_footprints.parquet` — 24,858 damage regions from DAT/Storm Events |
| Census | `county_context.parquet`, `county_boundaries.parquet` |
| Link evidence | `source_crosswalk.parquet`, `tornado_counties.parquet` |
| Summary/provenance | `annual_summary.csv`, `manifest.json` |

There are **nine main tables**, plus the annual summary, totaling **29.3 MB**. Use `pandas.read_parquet`
for ordinary tables and `geopandas.read_parquet` for footprints and boundaries.
The [field dictionary](docs/ANALYSIS.md) covers types, units, and missing values.

Footprints are not unique tornadoes. The crosswalk records accepted, ambiguous,
and unmatched links to SPC. Some footprints are
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
The release contains no radar, photos, individual building footprints, or fixed
train/test split. The crosswalk is automatic research evidence, not verified identity.

To collect the new enrichment separately and rebuild its ML views:

```sh
uv sync --locked --group enrichment
# Configure CENSUS_API_KEY in the ignored .env file. No CDS setup is needed.
uv run --group enrichment python -m enrichment.pipeline --all-events --dry-run
uv run --group enrichment python -m enrichment.pipeline --all-events --storage compact --cache-gb 5 --max-download-gb 100
uv run --group enrichment python -m enrichment.features
uv run --group enrichment python -m enrichment.verify --require-full --report data/enrichment/verification.json
```

Plan many hours to a few days for full collection; API latency and retries vary.
The byte ceiling limits new downloads per invocation; rerunning resumes extraction checkpoints. All 20,164 tornadoes remain in the
ML views even when enrichment is missing. Inspect source statuses before modeling.
Radar/warnings are screened at reported onset. Path-based land cover and exposure
appear only in the retrospective view. ERA5 tables and columns are excluded by default. Compact storage keeps a bounded
temporary cache and preserves extracted source tables and provenance. See [methods and limitations](docs/ENRICHMENT.md).

## One build, one starting table

`uv run python scripts/build_analysis.py` creates all nine tables under
`data/analysis/`. Start with `tornadoes.parquet`; use accepted crosswalk links for
narratives, locations, fatalities, or footprint detail. Every SPC row and its
original columns remain intact. No duplicate `tornadoes_linked.parquet` is supplied.

The [linkage methods](docs/LINKAGE.md) explain conservative acceptance, unresolved
candidates, county aggregation, and suggested split groups. The
[audit](reports/linkage/audit.md) records coverage and limitations. Rebuild after
refreshing sources; do not use record availability as a physical intensity feature.

## Repository

```text
download_data.py          Collection CLI and HTTP/cache helpers
footprint_data.py         EFC inventory, generation-pinned download, validation
census_data.py            Pinned Census sources and derivations
dataset_inspection.py     Read-only inspection helpers
enrichment/              Optional source adapters, resumable collection, offline ML views
notebooks/               Local inspection and Kaggle getting-started example
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
loads v2.1.0 / Kaggle 6 with `kagglehub` and reads the nine consolidated tables.
It runs on Kaggle or locally; the local inspection notebook additionally checks
the repository collection and analysis provenance.

```sh
uv run --group enrichment python -m unittest discover -s tests -v
```

GitHub Actions runs offline tests and a download dry run with locked dependencies.
Full source/host verification is performed separately for each published release.
Publishing clients live in the optional `publish` dependency group.

[MIT](LICENSE) covers project code and original text. NOAA and Census records have
separate [attribution and reuse notices](docs/DATA_SOURCES.md). This compilation is
not endorsed by the source agencies. See the [dataset card](docs/DATASET_CARD.md)
and [release workflow](docs/RELEASING.md) for reproducible citation and packaging.
