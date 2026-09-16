# ERA5 and compact-storage pilot

Validated September 15, 2026 (America/Chicago); machine timestamps use UTC.
This is a four-event pilot, **not full enrichment coverage or a new publication**.
Earlier HRRR results are preserved separately in the [HRRR history](../README.md).

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

- All 24 source/event jobs complete. The final retry reused the 20 completed
  non-ERA5 jobs after CDS dataset terms were accepted.
- ERA5 returned 73 samples per event: seven surface variables and three variables
  at 22 pressure levels. All 292 values passed the sampling/distance checks.
- CAPE, 0–1/0–6 km bulk shear, and 0–1/0–3 km SRH are nonmissing for all four
  pilot events. They appear only in the retrospective view.
- Both ML views retain all 20,164 backbone IDs and EF targets. Enrichment outside
  this four-event cohort remains explicitly unrequested/missing.
- The manifest references 71 source assets totaling 35,556,240 original bytes.
  Of these, 29 retained supporting files (249,301 bytes) passed checksum checks;
  42 optional bodies were intentionally removed. All downloaded ERA5 GRIBs
  were removed after durable extraction; the disposable cache ended at zero.
- The 12 linked source/provenance tables total 1,580,011 bytes. These sizes exclude
  backbone data, ML tables, HTTP metadata and retained pilot checkpoints.
- An identical resume ran with HTTP and CDS submission mocked to fail: zero new
  bytes, no re-extraction, and an unchanged collection manifest. ML regeneration
  and verification succeeded after optional source bodies had been removed.
- All 60 offline tests passed with the locked enrichment dependencies.
- All 11 code cells of the 22-cell local inspection notebook executed successfully.
  The executed copy was saved outside the repository; the source notebook remains clear of outputs.

Eight small CDS requests downloaded 290,840 bytes in 433.6 seconds of ERA5
collection time. Full batches can contain many days/hours, so this is not a
full-run timing or transfer estimate. The complete cohort currently forms 2,867
batches (5,734 requests). At one minute per request, ERA5 alone would take about
four days; queues and larger batches can increase this substantially.

The [machine report](pilot_validation.json) records exact table counts, retention
checks and manifest hashes. Full collection remains for the user to run.
HF/Kaggle still contain the previously published backbone release.
