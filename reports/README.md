# Reports and verification snapshots

This directory holds results and interpretation; executable commands are in
[`scripts/`](../scripts/). Run commands below from the repository root.

## Current verification

```sh
uv run python scripts/verify_downloads.py
```

The standalone verifier writes `verification/latest.json`. Read its `status`,
`scope`, timestamp, and manifest hashes to establish what it checked. The default
scope is all five sources. This command only reads `data/` and does not download.

The download command's `--verify-downloads` option instead saves its report next
to the collection at `data/download_verification_<start>_<end>.json`. That report
travels with the dataset. Both kinds of report identify their checked manifests;
a later collection can make an earlier verification stale. The notebook displays
the collection's saved verification, not the standalone report in this directory.

## DAT coverage study

[`dat_coverage/dat_coverage_report.md`](dat_coverage/dat_coverage_report.md) describes
the NOAA snapshot collected on **2026-09-14**, with its exact timestamp and source
hashes recorded in the report and metrics. It is a coverage study, not a current
five-source completeness certificate. The colocated PNG/SVG figures, metrics JSON,
and compressed candidate-match files support that study.

```sh
uv run python scripts/audit_dat_coverage.py
uv run python scripts/plot_dat_coverage.py
```

The audit regenerates metrics and candidates under `dat_coverage/`; the plotting
command reads those metrics and regenerates the figures. The report prose must be
reviewed if inputs change. Candidate matches are not verified event joins.

## Historical verification

`verification/history/` preserves earlier evidence unchanged:

- `download_verification.json` and `records_verification.json`: NOAA-only checks
  dated **2026-09-15T16:43:35.525425+00:00**, before the Census additions. These two
  files contain duplicate results retained as historical artifacts.
- `noaa_live_verification.json`: an earlier live NOAA inventory comparison. It has
  no top-level verification timestamp/status and is not a current completeness check.

Historical reports retain the paths and identifiers recorded at the time. Use
`verification/latest.json` or a matching collection-side report for current status.
