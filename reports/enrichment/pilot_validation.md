# Default non-ERA5 enrichment pilot

Validated September 15, 2026 (America/Chicago); machine timestamps use UTC.
This is a four-event pilot, **not full enrichment coverage or a new publication**.
ERA5 is deferred; its earlier validation is preserved under
[`history/era5/`](history/era5/pilot_validation.md).

```sh
uv run --group enrichment python -m enrichment.pipeline \
  --event-id spc:2010:1001201718-01 \
  --event-id spc:2020:2001101034-01 \
  --event-id spc:2020:2003030148-01 \
  --event-id spc:2025:2502121943-01 \
  --storage compact --cache-gb 0.05 --max-download-gb 2
uv run --group enrichment python -m enrichment.features
uv run --group enrichment python -m enrichment.verify
```

- All 20 source/event jobs complete for radar, warnings, NLCD, ACS and TIGER.
- The active collection has 11 source/link/provenance tables (1,546,990 bytes).
  ERA5 tables, status columns and environmental features are absent.
- Both ML views retain every one of the 20,164 backbone IDs and final EF targets;
  enrichment outside the four-event cohort remains explicitly unrequested.
- Source provenance identifies 63 assets, originally totaling 35,265,399 bytes.
  The 29 retained supporting files (249,301 bytes) passed checksum checks;
  34 optional source bodies were intentionally removed after extraction.
- ML generation, verification and zero-download resume passed with `cdsapi`,
  ecCodes and MetPy uninstalled. The collection manifest remained unchanged
  during resume. `--require-full` correctly rejected the successful pilot.
- All 61 tests passed with optional ERA5 dependencies. The default dependency
  group passed 56 tests and skipped the five explicitly optional ERA5 tests.
- All 11 code cells of the local inspection notebook executed successfully.
- Removed 412 unreferenced pilot artifacts (92,565,364 bytes) across the active
  and archived pilot folders, then reverified both retained snapshots.

The [machine report](pilot_validation.json) records exact counts and manifest
hashes. The [full-run instructions](../../docs/ENRICHMENT.md) use all 20,164 events
and `--require-full`. Full collection is left for the user. Publication to HF and
Kaggle, and updating the hosted example notebook, follow full-run verification.
Existing public releases and notebook pins are unchanged.
