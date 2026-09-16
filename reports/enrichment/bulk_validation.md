# Batched acquisition validation

The final collector completed **200 events / 600 radar, warning and NLCD jobs**
in **125.8 seconds**, transferring **128.6 MB** through **1,641 HTTP requests**.
All jobs passed. The full 20,164-event collection was **not started**; the active
four-event pilot and the published backbone remain unchanged.

The [machine-readable report](bulk_validation.json) records the extraction/code
hashes, cohort, source checks, timings, comparison tolerances and full request plan.
The final bounded output/cache is separate at `data/benchmarks/bulk_validation/`.

## What changed

- Nearby radar event queries share API requests, retaining API precision and
  native record identities. Capped responses subdivide instead of truncating.
- Warning geometry uses overlapping monthly exports where the selection warrants
  them, and smaller requests for sparse months. Original two-day event windows
  are reconstructed locally. Dense days use separate daily TOR/SVR/SVS text ZIPs;
  only selected texts remain in compact storage, with container provenance.
- NLCD requests share bounded areas in the same vintage. Event windows are read
  as native integer raster windows before masking, without resampling.
- Independent source queues can run concurrently under one total worker limit
  and the existing per-host limit. A slow warning request cannot stall the other
  source queues. Temporary connection/read failures retry automatically.

For the full cohort, the deterministic request plan is:

| Requests | Previous plan | Batched plan |
|---|---:|---:|
| Radar, three products | 60,492 | 36,318 before any cap-driven splits |
| Warning geometry exports | 2,855 | 196 |
| NLCD, two products, 20,147 CONUS events | 40,294 | 35,000 |

These counts exclude shared inventories, warning-text retrieval, retries and any
radar response subdivisions. They are acquisition plans, not downloaded coverage.

## Equivalence and limits

Compared with the frozen 200-event event-query reference:

- All **22,998 radar detections** and **20,445 radar links** match exactly apart
  from their acquisition asset references.
- All **93,867 warning updates/background rows** and **1,312 warning links** match
  under the same comparison. Original text action/expiry values match.
- All **400 NLCD summaries** have identical pixel counts, class counts, means and
  missingness. Affine metadata differs by at most approximately **8.1 micrometres**
  in origin and **0.033 micrometres** in pixel size because WCS serializes slightly
  different transforms for different extents. The test allows 1 mm origin and
  1 micrometre pixel-size drift; it compares all pixel statistics exactly.
- Both ML views match **exactly across all 20,164 rows**, including targets,
  split groups and missingness. Only 200 events are enriched in these benchmark
  views; the remaining rows retain their unrequested-source statuses.
- Changed source URLs/hashes and record provenance are verified independently,
  rather than incorrectly requiring a bulk request to hash like a smaller one.
  Job IDs necessarily change when extraction code or acquisition mode changes.
- An offline replay made **zero HTTP requests**, finished in **48.0 seconds** and
  reproduced the new source/provenance tables and both ML views exactly, excluding
  only code-dependent coverage job IDs. **81 offline tests passed**, including
  month boundaries, exact raster windows, capped responses, duplicate text IDs,
  compact member retention, retries and independent queue scheduling.

A fresh event-query control also completed all 600 jobs with equivalent source
and ML values. It used 2,217 HTTP requests and transferred 93.7 MB. Its observed
174.4 seconds partly overlapped the offline replay, so it is **not an isolated
speedup baseline**. Bulk requests trade some extra irrelevant bytes for fewer
round trips; those additional records are not added to the retained tables.

The live 125.8-second sample projects linearly to approximately **3.5 hours** for
the full cohort. Allow **3–6 hours** for planning, with longer runs possible during
throttling/outages. Full-scale request reuse and consolidation differ from this
sample; no full-run duration or persistent footprint has been measured.

## Why annual radar CSVs were not substituted

[NOAA publishes annual archives](https://www.ncei.noaa.gov/pub/data/swdi/database-csv/v2/),
but inspection found they are not interchangeable with the current API cohort.
For 2010-01-01, the annual TVS CSV contains 18 rows while the matching API day query
returns four. The extra archive rows have `CELL_ID=??`. The CSV also rounds point
coordinates and has different fields. The report preserves the probe URLs and
checksums. Grouped API requests preserve the existing dataset; adopting the
annual CSV cohort would be a separate source-definition change.

## Warning archive caveat

Batch filtering follows the [IEM export implementation](https://github.com/akrherz/iem/blob/main/pylib/iemweb/request/gis/watchwarn.py):
`addsvs=1` includes follow-up polygons, dates filter `coalesce(issue, polygon_begin)`,
and standalone `CAN` rows are excluded. This limitation also exists in the prior
collector. Thus these linked polygon histories are **not a complete VTEC message
timeline**; warning activity can be overstated after a cancellation absent from
the export. Batching preserves that cohort rather than silently changing features.
Missing/ambiguous [bulk text](https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?help=)
identities fall back to individual product retrieval; unresolved errors fail.

See [collection commands and storage policy](../../docs/ENRICHMENT.md).
