# Releasing the dataset to Hugging Face and Kaggle

[`DATASET_CARD.md`](DATASET_CARD.md) is the platform-neutral description.
[`release/dataset.json`](../release/dataset.json) holds the shared title, version,
period, and reuse designation. Platform metadata is generated from these files;
edit the canonical files rather than maintaining two separate descriptions.

The dataset designation is U.S. Government Works. Kaggle supports that identifier;
Hugging Face uses `license: other` with an explanatory name and link to the shared
source notices. MIT applies to code/original project text, not government records.

## Authenticate locally

Install the optional publishing clients without adding them to the core workflow:

```sh
uv sync --locked --group publish
uv run --group publish hf auth login
uv run --group publish hf auth whoami
```

Use a Hugging Face token permitted to create/write the intended dataset repository.
Enter credentials only into the local CLI prompt. For Kaggle, obtain a token in
Kaggle account settings and configure its CLI using the supported local credential
file or environment variable; do not commit credentials or put them in dataset
metadata. Existing credentials can be checked with:

```sh
uv run --group publish python -c 'import kagglehub; print(kagglehub.whoami(verbose=False))'
```

## Build a frozen release

Commit the code, card, and config first. Build requires a clean working tree,
full source verification, and matching collection-side verification manifests.
It selects active source files and supporting provenance rather than copying
arbitrary caches from `data/`. The source collection is read-only.

```sh
uv run python scripts/release_data.py build
uv run python scripts/release_data.py verify dist/v1.0.0/payload
```

The build copies sources, re-verifies the staged collection, and creates a schema
inventory, portable documentation, citation metadata, release manifest, and
checksums. The manifest records the code commit and both source snapshot dates.
Existing output directories are never overwritten; intentional data or schema
updates should receive a new release version. `dist/` is ignored by Git.

## Prepare both hosts

Replace the example account names with the authenticated owners when needed:

```sh
uv run python scripts/release_data.py stage huggingface --owner jakeryderv --payload dist/v1.0.0/payload --output dist/v1.0.0/huggingface
uv run python scripts/release_data.py stage kaggle --owner jakevanslyke --payload dist/v1.0.0/payload --output dist/v1.0.0/kaggle
```

All shared payload files, `release_manifest.json`, and `SHA256SUMS` are identical.
Only host-facing README/metadata differ. HF's viewer is disabled because the
collection contains heterogeneous source tables and GeoJSON; it is not one
rectangular training table. No automatic train/test splits are declared.

## Publish

```sh
uv run --group publish hf repos create jakeryderv/us-tornado-data-2010-2025 --repo-type dataset --public
uv run --group publish hf upload jakeryderv/us-tornado-data-2010-2025 dist/v1.0.0/huggingface . --repo-type dataset --commit-message 'Release v1.0.0'
uv run --group publish kaggle datasets create -p dist/v1.0.0/kaggle --public --keep-tabular --dir-mode zip
```

These commands create public resources. Run them only for an authorized release.
On Kaggle, `--dir-mode zip` transports nested source directories; verify that the
processed/downloaded tree preserves the release paths. Wait for processing to
complete before calling the release published. For later Kaggle releases, use
`datasets version` with version notes and preserve prior versions. Do not silently
replace an existing release or delete old versions.

After upload, record the immutable Hugging Face commit and the Kaggle numeric
version alongside the shared release version and manifest hash in
`release/<version>.json`. Tag the HF commit with the shared version. A tag alone
is not the trust anchor; consumer examples should pin the commit. The GitHub
code commit is separate from the two data-host revisions.

## Verify consumer downloads

Use an empty cache or new output location, then validate against the trusted
manifest SHA-256 from the release receipt:

```python
from pathlib import Path
from huggingface_hub import snapshot_download
import kagglehub

hf_root = Path(snapshot_download(
    "jakeryderv/us-tornado-data-2010-2025",
    repo_type="dataset", revision="HF_COMMIT_FROM_RELEASE_RECEIPT",
))
kg_root = Path(kagglehub.dataset_download(
    "jakevanslyke/us-tornado-data-2010-2025/versions/KAGGLE_VERSION_FROM_RELEASE_RECEIPT",
))
```

```sh
uv run python scripts/release_data.py verify /path/to/download --manifest-sha256 TRUSTED_MANIFEST_SHA256
```

Load tables directly from the returned cache path; do not modify cached source files:

```python
import pandas as pd
spc = pd.read_csv(hf_root / "spc/tornadoes_2010_2025.csv")
counties = pd.read_csv(
    hf_root / "census_population/county_context_2010_2025.csv",
    dtype={"county_fips": str},
)
```

Use `kg_root` in place of `hf_root` for Kaggle. Loading a table does not join it
with the other sources. Inspect `nws_dat/<year>/<layer>/index.json` to load all
survey batches. Record successful checksum verification and sample loading in
the release receipt before updating README download links as available.
