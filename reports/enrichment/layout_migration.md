# Consolidated table layout

The local dataset now has **17 source/supporting Parquet tables in `data/analysis/`**,
plus `annual_summary.csv`, and **two modeling tables in `data/ml/`**.
The eight additional tables were moved from `data/enrichment/tables/`; that old
directory is removed. Source records remain separate linked tables.

Download/checkpoint internals remain under `data/enrichment/`: its manifest,
progress and verification reports, retained warning texts, HTTP metadata and
checkpoint directories. Original backbone source downloads are unchanged.

The backbone inventory remains `analysis/manifest.json`; the eight additions
remain independently inventoried by `enrichment/manifest.json`, whose
`table_directory` now points to `../analysis`. Loaders resolve that location and
also support historical manifests with the old layout. Isolated benchmark/pilot
collections write their own `analysis/` subdirectory instead of modifying the
active dataset.

The migration checked source/destination hashes, published table files before
committing the new manifest, and removed old names only afterward. Original
extraction definitions are preserved. The manifest records compatibility with
this reviewed layout-only code revision, allowing the completed collection to
be reused without extraction or downloads; future code changes are not implicitly
covered by that compatibility record.

Validation on September 16, 2026:

- All **87 tests passed**, including migration conflicts, idempotence, isolated
  output paths and completed-snapshot reuse without collectors or HTTP.
- All **19 Parquet files** and the backbone manifest are byte-identical to their
  pre-migration versions, including both regenerated ML tables.
- The actual full collection reuse check performed **zero downloads**.
- The notebook's source-table and ML loader cells passed with its normal setup.
- `enrichment.verify --require-full` passed for **20,164 events**, with no failed
  jobs and the same 17 documented NLCD coverage gaps.

[Migration receipt and file hashes](layout_migration.json) and
[full verification](layout_verification.json) identify the checked snapshot.
The earlier [repair audit](full_repair_validation.md) remains evidence of the
source repairs before this layout-only change. This migration did not publish a
new Hugging Face/Kaggle release.
