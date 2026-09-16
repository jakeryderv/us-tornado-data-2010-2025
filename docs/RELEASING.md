# Releasing to Hugging Face and Kaggle

`release/dataset.json` defines the shared release version and scope.
`docs/DATASET_CARD.md` is the canonical dataset description: it includes both
platform examples in marked blocks. Staging renders only the matching quick
start into each platform's README/listing, while the packaged shared card retains
both examples. Source facts, tables and limitations come from the same text.

## Prepare and validate

Use the locked Python 3.12 environment:

```sh
uv sync --locked --group publish --group era5
uv run --locked --group era5 python -m unittest discover -s tests -v
uv run --locked --group enrichment python -m enrichment.verify --require-full --report data/enrichment/verification.json
```

The optional ERA5 dependency group exercises deferred-adapter tests; it does not
download ERA5. This release includes radar, warnings and NLCD. ACS/TIGER tract
and ERA5 sources remain excluded. The current scope flag `include_enrichment`
requires full collection/ML verification and rejects `--backbone-only`.

Commit the code, card, configuration and listing metadata before building. A
clean code revision is required. The build verifies original backbone sources,
checks the full enrichment snapshot and copies manifest-listed files without
regenerating or changing table bytes. It refuses existing staging destinations.

```sh
uv run --group enrichment python -m scripts.release_data build
uv run python -m scripts.release_data verify dist/v2.2.0/payload
```

The shared payload has all 17 `analysis/` tables and both `ml/` views, their
manifests/dictionaries, original backbone sources, portable documentation,
`schema.json`, `CITATION.cff`, `release_manifest.json` and `SHA256SUMS`.
Working caches/checkpoints/benchmarks are omitted. Required warning texts and
schema documents are bundled in `enrichment/supporting_assets.zip`; every member
is checked against its original asset hash. The bundle preserves original paths.
To run the standalone source verifier on a full consumer download:

```sh
uv run python -m scripts.release_data restore-support /path/to/download
uv run --group enrichment python -m enrichment.verify --data-dir /path/to/download --require-full
```

Offline ML regeneration needs the linked tables, not the expanded support bundle.
The release checksum verifier can verify the bundle without expanding it.

## Stage both hosts

```sh
uv run python -m scripts.release_data stage huggingface --owner jakeryderv --payload dist/v2.2.0/payload --output dist/v2.2.0/huggingface
uv run python -m scripts.release_data stage kaggle --owner jakevanslyke --payload dist/v2.2.0/payload --output dist/v2.2.0/kaggle
```

Both share identical payload bytes and checksums. HF stores them directly.
Kaggle exposes `analysis/` and `ml/` directly and places the complete payload in
`release.zip.bin`, preserving the original compressed source files. Unpack it
with Python `zipfile` or the `unpack` command, supplying the trusted manifest and
archive checksums. No full archive is needed to load an individual modeling table.

Kaggle descriptions/resources/source notes are supplied by
`release/kaggle/metadata.json`; the cover comes from `scripts/plot_dataset_cover.py`.
The staged listing description is the Kaggle-rendered shared card. The HF README
uses the HF-rendered card plus host metadata. MIT covers code/original text; data
reuse notices remain in `DATA_SOURCES.md`.

## Publish and verify

Authenticate through local clients; never place tokens in code, metadata or chat:

```sh
uv run --group publish hf auth whoami
uv run --group publish python -c 'import kagglehub; print(kagglehub.whoami(verbose=False))'
```

Only publish an authorized, verified release. Update the existing listings,
preserve historical versions, and wait for processing to finish. HF folder uploads
should remove only explicitly identified obsolete files in the new revision;
never delete the repository or historical revisions. Kaggle uses `datasets version`.

```sh
uv run --group publish hf upload jakeryderv/us-tornado-data-2010-2025 dist/v2.2.0/huggingface . --repo-type dataset --commit-message 'Release v2.2.0'
uv run --group publish kaggle datasets version -p dist/v2.2.0/kaggle --keep-tabular --dir-mode zip -m 'Release v2.2.0: radar, warnings, NLCD and onset/retrospective ML views'
```

Export the existing listing metadata, then merge the staged description, source
notes, resources, frequency and cover into a copy. Preserve the exported display
license name: this update endpoint rejects the creation-time license slug.
Refresh that copy using `kaggle datasets metadata --update` and verify
the saved description, sources and file notes; API success alone is not proof of
rendered metadata. Both listings must show the same version, sources, 19-table
inventory, coverage gaps and limitations, with the matching platform's code.

Download the full HF payload at its immutable commit and the Kaggle transport
archive at its numeric version into fresh locations. Compare every file against
the same trusted release manifest, then test direct Parquet downloads and the
examples. Save host revisions, tag, sizes, checksums and verification results in
`release/v2.2.0.json`. HF's `v2.2.0` tag and Kaggle's automatic numeric version are
separate identifiers for the same shared release.

Update the public Kaggle notebook with the actual numeric version and manifest
hash. Run it locally against staged data, publish it with the matching pinned
attachment, wait for hosted completion, and retrieve its executed output/source.
Record the notebook run/version in `release/kaggle/notebook-v2.2.json`.
Finally push code/receipts and confirm GitHub CI passes. Historical releases and
receipts remain available; remove obsolete local staging only after verification.
