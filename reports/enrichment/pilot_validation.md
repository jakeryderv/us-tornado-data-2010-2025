# Radar, warnings and NLCD pilot

The active four-event pilot spans 2010–2025 and contains only radar, warnings and
NLCD. All **12 source/event jobs** completed, and the source/ML verifier passed.
The full 20,164-event enrichment download has not started.

- Eight source/link/provenance tables retain the selected radar detections,
  warning histories, land-cover summaries, event areas and source coverage.
- Both ML views preserve all 20,164 backbone IDs and EF targets. Outside the
  four-event cohort, source coverage remains explicitly unrequested.
- ACS/TIGER tables, status columns and tract-exposure features are absent.
  ERA5 remains excluded. Backbone county estimates and the simplified map remain.
- The NLCD refresh retried two timed-out USGS requests successfully; completed
  jobs resumed from checkpoints.
- Obsolete mixed-source pilot jobs, shared checkpoints and unreferenced raw cache
  artifacts were removed after the new tables committed and verification passed.
- All 70 offline tests passed, including narrowed defaults, optional-table
  retirement, absent deferred ML columns and preserved NLCD features.
- All 11 code cells in the updated local inspection notebook executed successfully.

The separate 200-event replay completed all **600 source jobs** without network
requests. Its eight remaining source/link/provenance tables exactly match the
corresponding historical benchmark rows. Both ML views exactly match all retained
columns after removing only ACS/TIGER statuses and tract-exposure features.
See [tract_deferral_validation.json](tract_deferral_validation.json).

The [machine report](pilot_validation.json) identifies the active verified
snapshot. Earlier five-source results remain as historical evidence under
[history/tracts/](history/tracts/pilot_validation.md), and the old mixed-source
benchmark files were replaced by the verified three-source collection at
`data/benchmarks/three_sources/`.

Use the [full-run instructions](../../docs/ENRICHMENT.md) when ready. HF/Kaggle
publication and hosted notebook updates follow full collection and verification.
