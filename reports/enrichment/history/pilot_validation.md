# Enrichment implementation pilot

Validated September 15, 2026 (America/Chicago); machine timestamps use UTC.
This is a four-event pilot, **not full enrichment coverage**.

The selected cohort spans early and late years and several ratings:

```sh
uv run --group enrichment python -m enrichment.pipeline \
  --event-id spc:2010:1001201718-01 \
  --event-id spc:2020:2001101034-01 \
  --event-id spc:2020:2003030148-01 \
  --event-id spc:2025:2502121943-01 \
  --max-download-gb 2
uv run --group enrichment python -m enrichment.features
uv run --group enrichment python -m enrichment.verify
```

- 23 source/event jobs complete; one expected HRRR gap before the operational era.
- 108 source assets, 84,563,095 bytes, all rechecked against stored SHA-256 digests.
- Both ML views retain all 20,164 backbone IDs and EF targets, with unrequested
  enrichment explicitly missing outside the selected cohort.
- Cached resume completed with HTTP access mocked to fail and zero new bytes.
- All 54 offline tests passed under the locked enrichment dependency group.
- All 22 cells of the read-only local inspection notebook executed successfully.
- Source keys, linked-record references, provenance, HRRR cutoff times and the
  onset predictor allowlist passed the standalone verifier.

The [machine report](pilot_validation.json) records exact table counts and
manifest hashes. Full collection was left for the user; HF/Kaggle remain at the
existing published release. Future full-run source failures or missing coverage
must be assessed from their own verification report, not this pilot.
