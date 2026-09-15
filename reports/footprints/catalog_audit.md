# Footprint Catalog audit and v2 migration

Checked September 15, 2026. Exact generations, source hashes, and comparison counts
are recorded in [catalog_audit_metrics.json](catalog_audit_metrics.json).

All 16 annual 2010–2025 GeoJSON files downloaded and passed upstream size/MD5 and
local SHA-256 checks. The original files total **70,204,056 bytes**. There are
**24,858 footprint regions**: 16,465 DAT-derived and 8,393 SED-derived. They include
nested damage regions, not a unique tornado catalog. All 24,858 polygon/multipolygon
geometries load; none are null or invalid in this snapshot.

**16,424 of 16,465 DAT primary GUIDs (99.75%) match** a line or polygon in the v1.2.0
source snapshot. The remaining 41 require investigation; revisions or processing
changes are possible explanations, not established findings. All SED event-name
fields are blank, and the catalog lacks original NCEI EVENT_IDs. Generated IDs
therefore cannot be used as an exact NCEI join. No SPC matching was performed and
complete coverage of the 20,164 SPC tracks is not established.

The catalog uses DAT first, supplementing with Storm Events according to published
time/distance rules (two hours / 1,000 meters). Footprints can be reconstructed from
lines/endpoints. It supplies no individual DAT damage-indicator observations.
[NOAA methodology](https://www.ncei.noaa.gov/sites/default/files/2026-08/EFC%20Data%20Information%20Sheet_v6.pdf)

Observed limitations retained in v2:

- 2,682 raw widths are the documented 0.99 missing-width display placeholder;
  4,114 are zero. Nullable `path_width_yards` marks all 6,796 as unavailable.
- 4,402 regions have parents and 3,157 have children. Counts and ratings must not
  be interpreted as independent tornado observations.
- One 2010-file record has a 2011-01-01 UTC timestamp. Source annual partitions
  and UTC years are not interchangeable at New Year.
- Some edit timestamps contain -99 sentinels; one contains epoch milliseconds.
  Original values are retained as text and parsed UTC helpers handle these explicitly.

The v2 footprint table totals **11,651,740 bytes**; all seven Parquet tables plus
the annual CSV total **24,104,783 bytes**. The six retained SPC/NCEI/Census Parquet
tables are byte-identical to v1.2.0. All 104 retained non-DAT source payload files
(excluding discovery HTML and download sidecars) are also byte-identical.

The standalone DAT collection and three survey tables are removed from the active
v2 dataset. Previous hosted releases retain them. The new table is optional mapping
context; it does not replace the SPC model catalog or perform verified event joins.
Upstream EFC overwrites files during updates; our releases freeze generation-pinned
snapshots and checksums. Full release verification is recorded in the release receipt.

Sources: [NOAA EFC](https://www.ncei.noaa.gov/products/event-footprint-catalog),
[NOAA field dictionary](https://storage.googleapis.com/noaa-ncei-ipg/datasets/event-catalog/README.md).
