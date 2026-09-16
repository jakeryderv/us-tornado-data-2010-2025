# Full enrichment repair and verification

Verified September 16, 2026. The local radar, warnings and Annual NLCD collection
now covers all **20,164 backbone events** with **zero failed jobs**. Both ML views
were rebuilt and passed `enrichment.verify --require-full`. These additions remain
local; this repair did not publish a new Hugging Face or Kaggle release.

| Source | Complete jobs | Unavailable jobs | Repaired failures |
| --- | ---: | ---: | ---: |
| NEXRAD-derived radar / SWDI | 20,164 | 0 | 1 |
| IEM NWS warnings | 20,164 | 0 | 11 |
| Annual NLCD | 20,147 | 17 | 121 |

The 17 NLCD gaps are outside the selected CONUS coverage and remain explicitly
unavailable. Complete jobs indicate successful extraction, not that every event
has radar detections, a warning, or valid raster pixels. Missingness is retained.
ERA5, ACS and TIGER/Line tract enrichment remain deferred.

## What failed and what changed

- One radar request exhausted HTTP 503 retries; retrying succeeded.
- Of 121 failed NLCD jobs, 116 requested an extent only one native pixel wide or
  high. GeoServer returned HTTP 200 containing a resolution-error XML document.
  Download bounds now receive native-grid padding for these extents, while the
  original event window and footprint still define the statistics. The other
  five failed requests succeeded when retried.
- Eleven warning jobs encountered different warnings sharing a public product ID
  within one minute. The single-product endpoint returned another warning's text.
  A one-minute archive query now reads duplicate ZIP members individually and
  selects by product header and exact VTEC office, phenomenon and event number.
  Missing or multiple distinct matches still fail explicitly.
- During repair, a regenerated IEM ZIP had different bytes at an existing request
  URL. Compact-cache verification now distinguishes an intentionally discarded
  old version from a replacement verified against its own metadata and checksum.
  Changed required files and corrupt replacement bytes still fail verification.

## Preservation and results

The explicit repair reused **60,359 original jobs** (60,342 complete and 17
unavailable) and re-extracted exactly **133 failed jobs**. Each coverage row keeps
its extraction definition and job ID; the manifest retains both code definitions,
unchanged scientific settings/backbone hashes, and the repair input checksum.

Comparison against saved pre-repair source tables preserved all existing scientific
values exactly. The comparison excluded source asset references, which can identify
newly retrieved copies; previously null warning action/expiry fields could be
resolved from validated texts. All previously populated warning values matched.
The repair added:

- 35 radar detections and 92 tornado–radar links.
- 120 tornado–warning links and validated text-derived fields for 70 previously
  unresolved warning updates already present in the background table.
- 242 NLCD samples, representing two products for each of 121 repaired events.

The final successful repair invocation transferred **12.44 MB** and took
**194.8 seconds inside the collector**, excluding initial event preparation and
ML generation. An earlier interrupted repair attempt also performed some work;
these figures describe the final invocation only. The original full pass took
about two hours and transferred 6.16 GB. Temporary cached source bodies were
cleared after extraction. The interrupted attempt's 52 redundant checkpoints
were also retired after the completed snapshot was saved.

## Validation evidence

- **85 offline tests passed**, including single-pixel sampling equivalence, warning
  identity collisions, preservation of successful jobs, and cache-version checks.
- [Source repair comparison and counts](full_repair_validation.json).
- [Full collection and ML verification](full_collection_verification.json):
  table/source checksums, keys and links, complete requested cohort, preserved
  IDs and EF targets, onset predictor restrictions, and per-job code provenance.
- `ml/events_onset.parquet`: 20,164 rows, 30 columns.
- `ml/events_retrospective.parquet`: 20,164 rows, 53 columns.

The repair-input manifest was JSON-reserialized before being frozen, so its byte
checksum differs from the original collection manifest. Both checksums are recorded
in the comparison report; the manifest data and original table snapshots agree.

Recheck the current local result from the repository root:

```sh
uv run --group enrichment python -m enrichment.verify \
  --require-full --report data/enrichment/verification.json
```

The verification reports are tied to manifest hashes. Later collection or feature
changes require rebuilding and verifying again.
