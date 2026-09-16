# Research use and limitations

The frozen dataset supports **conditional EF-rating research on recorded tornadoes**
and retrospective study of recorded damage, warnings, nearby radar indicators and
land cover. It is not a population of all storms, an operational tornado detector,
or an independent measurement of physical tornado intensity.

v2.2.1 retains the same 19 Parquet tables and values as v2.2.0. It adds a source
citation register, reproducible coverage reports and independent temporal checks.
The feature dictionary moves to schema 2: conditional onset eligibility is distinct
from verified availability, and `post_nlcd_valid_fraction` is quality metadata,
excluded from the default retrospective predictors.

## Unit of analysis, targets and links

Both ML views contain all **20,164 SPC catalog rows once**, with unique, stable
`tornado_id` values derived from SPC source year/event ID. Stability means the
same source key remains the same ID; later upstream corrections can change catalog
content. Source segments, damage regions, warnings and radar detections have
separate identities and may have many-to-many relationships with these rows.

`target_ef_rating` is the final SPC EF label; unknown ratings remain null, never
EF0. `target_known`, IDs, source statuses, grouping fields and quality indicators
are metadata. Use the dictionary's explicit predictor lists rather than every
numeric column. Final target values are carried in the onset table for supervised
learning but are never onset predictors.

Conservative source matching retains accepted, ambiguous, review-required,
outside-acceptance and unmatched records. It does not force a best match or drop
unlinked tornadoes. Inspect `source_crosswalk`, acceptance counts and unresolved
candidate counts. Candidate-row counts are not event counts. SPC, Storm Events
and the Footprint Catalog partly share reporting/survey origins: agreement among
them is not independent corroboration, and EFC damage regions are not direct
observations of an entire vortex path.

## Timing and leakage boundary

The onset view is **conditional on the final catalog's reported onset and start
location**. Those anchors were not necessarily known operationally at that time.
Radar records qualify when observation time plus an **assumed five-minute latency**
is no later than onset. Warning states use only updates issued by onset, with
known expiry after onset and a polygon covering the reported start. Actual receipt
or delivery timestamps are not available, so `available_by_onset` is null for the
candidate fields; `eligible_for_conditional_onset` records their narrower meaning.

The independent verifier reconstructs all eight radar and three warning aggregates
from linked source records, checks radar observation/availability times and the
configured radius/window, and rejects selected future warning polygon times.
For this snapshot it checks **827,103 qualifying radar links** and **22,166 active
warning/event rows**, with no selected observations after the cutoff. This verifies
the recorded-time contract, not operational availability or archive completeness.
The IEM export omits standalone cancellations, which can overstate active warnings;
issuance time is not receipt time. Warning decisions also reflect forecaster
judgment, technology and office practice, not just storm physics.

Final path length/width, endpoint, duration, fatalities, injuries, narratives,
damage footprints and footprint-based land cover must not enter onset predictors.
The retrospective view may include post-event features, but its radar window ends
at **onset +60 minutes**, not necessarily tornado dissipation. Recorded paths and
damage-related features can encode the EF assessment itself. Compare models with
and without these fields and avoid interpreting predictive importance as causation.
**1,030 rows have equal reported start/end times**; derived zero duration is not
proof of zero physical duration.

## Coverage, missingness and class balance

Reports in [`reports/research/`](../reports/research/README.md), packaged as
`research/`, are regenerated from the release tables. They provide counts and
fractions by **year, SPC state/territory and EF class**, including unknown labels,
for source job statuses, accepted/unresolved links, positive detections, feature
missingness, county context and NLCD sampling. Input hashes and denominator
interpretations are in `summary.json`. These are sample coverage statistics,
not radar uptime maps or an estimate of the unreported tornado population.

| Final EF | Events |
|---|---:|
| EF0 | 9,197 |
| EF1 | 7,149 |
| EF2 | 1,776 |
| EF3 | 426 |
| EF4 | 83 |
| EF5 | 8 |
| Unknown | 1,525 |

All 60,492 requested radar/warnings/NLCD event jobs finished: **60,475 complete,
17 outside NLCD coverage, zero failed**. Download completion does not imply
nonmissing predictors. Land-class fractions are missing for **317 events**:
17 outside coverage, 299 completed areas with no pixel centers and one with no
valid class pixels. Impervious means are missing for **316**. Onset shear is
missing for **13,260 (65.8%)**, rotation velocity for **5,236 (26.0%)**, and tornado
warning lead time for **8,819 (43.7%)**. Null radar maxima can mean no qualifying
product detection; zero counts are not evidence of no rotation or functioning
radar coverage. A null warning lead can mean no active warning, not a failed query.

Unknown EF labels are absent in the selected 2010–2015 rows but constitute
**268/1,321 (20.3%) in 2023** and **234/1,383 (16.9%) in 2025**. This observed
change needs investigation before combining years or comparing performance;
it does not identify the cause. Do not silently restrict to complete cases.
Report the cohort, year/class composition, excluded unknowns and feature missingness.

## Biases and interpretation limits

- **Damage-based labels and exposure:** the [NWS EF scale](https://www.weather.gov/oun/efscale)
  estimates winds from damage indicators; it is not a direct wind measurement.
  Sparse buildings/indicators, rural exposure, construction quality and survey
  access can leave strong tornadoes underrated or unrated. Reporting and damage
  assessment depend on population, communications, observers and practices.
- **Changes over time:** reporting, survey coverage, EF assignment and radar/warning
  technology/practices vary through 2010–2025. This dataset does not homogenize
  those changes. The EF scale began February 1, 2007; 2010 is a study-scope choice,
  not the scale transition or a completeness threshold. See NOAA-hosted research on
  [reporting bias](https://repository.library.noaa.gov/view/noaa/25686) and
  [damage-rating bias](https://repository.library.noaa.gov/view/noaa/45079/noaa_45079_DS1.pdf).
- **Radar association and geography:** proximity within 20 km of the final start
  is not confirmed parent-storm tracking. Radar range, beam height, terrain,
  outages, algorithm changes and scan opportunities affect detections. Counts
  are not normalized by scans; shared detections do not create independent cases.
- **NLCD resolution and revision:** prior-year 30 m maps summarize pixel centers
  in final accepted footprint unions or a fixed 500 m endpoint-track/point buffer.
  The buffer is an approximation, not observed damage width. Land-cover class is
  not building condition/count. Annual maps can be released/revised after events.
  Exact WCS requests, layer names, years, grid and hashes are retained, but the
  saved metadata does **not resolve the NLCD collection revision**. Do not infer
  that revision from the service's current catalog; raw re-extraction may differ.
- **Census resolution:** county July 1 estimates use vintages 2020 and 2025 and
  whole-county totals; these are not event-date measurements or people/buildings
  struck. The generalized 2020 map is not a historical boundary series. Boundary
  changes and unmatched county context remain flagged. Tract ACS/TIGER is deferred.
- **Evaluation dependence and imbalance:** only eight EF5 labels cannot support
  stable fine-grained subgroup estimates alone. Keep shared-source
  `ml_split_group` values together, consider temporal/geographic holdouts and
  exclude groups crossing split boundaries. The 2,255 groups (largest 400 events)
  are conservative linkage components, not verified outbreaks. Fit preprocessing
  on training data only; report class-specific metrics and uncertainty.

Do not use this collection alone for live warnings, public safety decisions,
property-level risk/insurance assessments, causal claims about exposure or warnings,
a tornado climatology corrected for underreporting, or claims that an EF model
measures true wind intensity. It contains no non-tornadic control cohort and no
current environmental reanalysis. ERA5 and ACS/TIGER remain deferred.

## Rebuild and audit

Use the code commit recorded in `release_manifest.json` and locked Python 3.12
dependencies. Both ML tables and their dictionary are regenerated offline from
the linked tables and extraction configuration; no network or expanded support
archive is needed for that step:

```sh
uv run --locked --group enrichment python -m enrichment.features --data-dir /path/to/release --output /tmp/rebuilt-ml
uv run --locked --group enrichment python -m scripts.research_report --data-dir /path/to/release --output /tmp/rebuilt-research
```

Compare the two Parquet files and dictionary to the release checksums. The newly
generated ML manifest has a new build timestamp. Re-extracting normalized source
tables from upstream services is a separate operation: retained URL/hash provenance
detects changed bytes but cannot recover removed source artifacts. The compact
release retains linked records and required warning texts/schema documents;
large temporary raster/query responses need upstream access for raw re-extraction.
See [source citations/terms](DATA_SOURCES.md) and [validation](ENRICHMENT.md).
