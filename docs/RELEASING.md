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
uv run python scripts/release_data.py verify dist/v2.0.0/payload
```

The build copies sources, re-verifies the staged collection, regenerates the
analysis tables with Parquet round-trip and annual-total checks, and creates a schema
inventory, portable documentation, citation metadata, release manifest, and
checksums. The manifest records the code commit and both source snapshot dates.
Existing output directories are never overwritten; intentional data or schema
updates should receive a new release version. `dist/` is ignored by Git.

## Prepare both hosts

Replace the example account names with the authenticated owners when needed:

```sh
uv run python scripts/release_data.py stage huggingface --owner jakeryderv --payload dist/v2.0.0/payload --output dist/v2.0.0/huggingface
uv run python scripts/release_data.py stage kaggle --owner jakevanslyke --payload dist/v2.0.0/payload --output dist/v2.0.0/kaggle
```

All shared payload files, `release_manifest.json`, and `SHA256SUMS` are identical
after extracting Kaggle's transport archive. Hugging Face stores the files directly;
Kaggle stores `release.zip.bin`, a ZIP with an extra `.bin` suffix. Kaggle otherwise
automatically extracts the original NOAA `.gz` and Census `.zip` source files,
which invalidates their paths and checksums. The archive needs one local extraction;
its SHA-256 and size are recorded separately from the shared manifest.
Kaggle also exposes identical `analysis/` files directly, so those tables can be
downloaded individually without the source archive. Host-facing README/metadata
differ. HF's viewer is disabled because the
collection contains heterogeneous source tables and GeoJSON; it is not one
rectangular training table. No automatic train/test splits are declared.

## Publish

Kaggle's cover, file descriptions, provenance, and public example notebook are
managed separately from the data version. See the
[Kaggle listing workflow](../release/kaggle/README.md) for the saved metadata,
verified update commands, and notebook publishing instructions.

```sh
uv run --group publish hf upload jakeryderv/us-tornado-data-2010-2025 dist/v2.0.0/huggingface . --repo-type dataset --delete 'nws_dat/*' --delete 'analysis/survey_*.parquet' --commit-message 'Release v2.0.0'
uv run --group publish kaggle datasets version -p dist/v2.0.0/kaggle --keep-tabular --dir-mode zip -m "Release v2.0.0: replace DAT surveys with NOAA footprints"
```

These commands update the existing public datasets. The v2 HF upload must delete
only the retired `nws_dat/` paths and three survey tables from the new revision;
prior commits and tags remain available. Verify the resulting remote file inventory.
 Run them only for an authorized
release. On Kaggle, `--dir-mode zip` transports the direct `analysis/` folder, which
Kaggle expands. The complete `release.zip.bin` archive remains a single file.
Wait for processing to complete before calling the release published. For later Kaggle releases, use
`datasets version` with version notes and preserve prior versions. Do not silently
replace an existing release or delete old versions.

After upload, record the immutable Hugging Face commit and the Kaggle numeric
version alongside the shared release version and manifest hash in
`release/<version>.json`. Tag the HF commit with the shared version. A tag alone
is not the trust anchor; consumer examples should pin the commit. The GitHub
code commit is separate from the two data-host revisions.

## Verify consumer downloads

Use an empty cache or new output location, then validate against the trusted
manifest SHA-256 from the [release receipt](../release/v2.0.0.json).
These examples pin the published `v2.0.0` revisions:

```python
from pathlib import Path
from huggingface_hub import snapshot_download
import kagglehub

hf_root = Path(snapshot_download(
    "jakeryderv/us-tornado-data-2010-2025",
    repo_type="dataset", revision="dbe9453af95255c77de5d4616fba2f924a2f80ad",
))
kg_archive = Path(kagglehub.dataset_download(
    "jakevanslyke/us-tornado-data-2010-2025/versions/5",
    path="release.zip.bin",
))
```

For Kaggle, extract and verify into a new directory using hashes from the release
receipt (the ordinary Python `zipfile` module can also extract the archive):

```sh
uv run python scripts/release_data.py unpack /path/to/kaggle/download/release.zip.bin /path/to/extracted-data --manifest-sha256 TRUSTED_MANIFEST_SHA256 --archive-sha256 TRUSTED_ARCHIVE_SHA256
```

Set `kg_root = Path("/path/to/extracted-data")`. Use Kaggle version 5 for
`v2.0.0`; v1.2.0 maps to Kaggle version 4, v1.1.0 maps to Kaggle version 3 and v1.0.0 maps to version 2.
Kaggle version 1 is retained as upload history but failed original
archive integrity checks and is superseded. Hugging Face and Kaggle version numbers
do not need to match; the receipt maps both to the shared release.

In a separate project, the equivalent extraction needs only the standard library:

```python
import hashlib
from zipfile import ZipFile

with kg_archive.open("rb") as handle:
    assert hashlib.file_digest(handle, "sha256").hexdigest() == (
        "cc6f2884290985e126d224e97432986c4646ffd8f8d0712320573fe1166e5b11"
    )  # v2.0.0 archive hash from the receipt
kg_root = Path("tornado-data-v2.0.0")
kg_root.mkdir(exist_ok=False)  # Extract once into a new folder; reuse it afterward.
with ZipFile(kg_archive) as archive:
    archive.extractall(kg_root)
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

Use the extracted `kg_root` in place of `hf_root` for Kaggle. Loading a table does not join it
with the other sources. Read `event_footprints/<year>_tornado_footprint.geojson` for original annual footprints. Record successful checksum verification and sample loading in
the release receipt before updating README download links as available.

For the small analysis-only download and direct Kaggle Parquet example, see the
[repository quickstart](../README.md). A partial download cannot pass full-release
verification; compare the selected file hashes with the trusted release manifest.
