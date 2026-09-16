# SPC-centered crosswalk and linked analysis view

This optional **local research layer** connects NCEI records and NOAA footprint
regions to the SPC tornado catalog. It preserves all seven source-oriented
analysis tables and all SPC rows, including unknown EF ratings. It is generated
separately under `data/linkage/`; the frozen v2.0.0 / Kaggle 5 release does not
contain it. The published Kaggle example continues to demonstrate that release.

```sh
uv run python scripts/build_crosswalk.py
```

The command needs four existing Parquet tables and `analysis/manifest.json`, not
a new download. It verifies their checksums, builds the links, checks cardinality
and Parquet round trips, and writes a manifest with input/output hashes, rules,
software versions, and the builder's hash. `--data-dir` and `--output` select other
locations. Rebuild after rebuilding the analysis tables; the local inspection
notebook detects stale linkage inputs. These files are deliberately outside the
current release packager's source/analysis inventory.

## Three derived tables

| File | Row unit | Purpose |
|---|---|---|
| `source_crosswalk.parquet` | Source record/candidate SPC pair; one null-target row if no candidates | Complete linkage evidence and decisions for every NCEI detail record and footprint region |
| `tornado_counties.parquet` | Accepted NCEI event's county/year context | Traceable county codes and left-joined Census estimates; repeated county segments stay visible |
| `tornadoes_linked.parquet` | One SPC tornado | All original SPC columns plus link counts, county-context summaries, and a suggested split group |

The [audit](../reports/linkage/audit.md) records coverage and unresolved cases.
`data/linkage/manifest.json` supplies the exact columns, dtypes, hashes, counts,
and rules for a run. No source values or EF labels are overwritten.

## How decisions work

1. **Normalize event times.** SPC code 3 is fixed CST (UTC−6); code 9 is UTC.
   Unknown codes are ineligible. NCEI uses its signed `source_timezone` offset.
   These are recorded standard-time offsets, so local daylight-saving rules are
   not added. UTC conversion can cross year boundaries.
2. **Choose footprint timestamps.** Use a valid polygon start/end interval,
   otherwise its associated line interval. Durations must be 0–360 minutes.
   A reversed/invalid original interval or a start more than one hour from
   `stormdate` prevents automatic acceptance. With only `stormdate`, preserve
   candidate links for review; do not assume timestamp precision from the field.
3. **Find candidates.** Intervals must overlap or be within 120 minutes; source
   geometry must be within 20 km of an eligible SPC endpoint track. Geometry
   distances use a local WGS84 azimuthal-equidistant projection, including Alaska
   and Hawaii. SPC endpoints define an approximate straight track, not a survey.
4. **Keep nearby alternatives.** A candidate is *plausible* if the source interval
   extends at most 10 minutes beyond the SPC interval and every source geometry
   vertex is within 5 km of the SPC track. This directed measure permits a county
   segment or nested footprint inside a longer track. A crossing intersection
   alone is insufficient.
5. **Accept conservatively.** There must be exactly one plausible candidate, valid
   source time/geometry, at most **2 minutes** outside the SPC interval, and at
   most **2 km** of directed geometry distance. Borderline unique candidates
   remain `review_required`; multiple plausible candidates remain `ambiguous`.
   The evidence score ranks candidates for inspection and never breaks a tie
   into an accepted match.
6. **Check footprint families.** Explicit parent/child object IDs and nonzero path
   GUIDs group regions within their source/year. If independently accepted
   regions in a family point to different SPC tracks, those plausible links
   are demoted to ambiguity. Accepted identity is not propagated to other regions.

EF ratings, narratives, damage, casualties, population, and housing are **not
matching inputs**. The thresholds are transparent research choices, not learned
or calibrated accuracy guarantees. Exact agreement can reflect shared upstream
reporting; these sources are not independent confirmations.

## Reading the crosswalk

- `source_table`, `source_id`, `source_origin`, `source_year`, `source_file`, and
  `source_row` trace each original record. `source_group_id` identifies an NCEI
  episode or an EFC family; an episode can contain multiple tornadoes.
- `tornado_id` is the candidate SPC key, null only on a no-candidate row.
- `source_start_utc`, `source_end_utc`, `spc_start_utc`, `spc_end_utc`, `time_basis`,
  `source_auto_eligible`, and `input_issue` expose time interpretation and issues.
- `interval_gap_minutes` measures separation between intervals;
  `interval_outside_minutes` measures the source interval's maximum extension
  beyond the SPC interval. `start_delta_minutes` is a diagnostic absolute start
  difference, not a required zero difference for county segments.
- `geometry_gap_km` measures minimum separation;
  `source_extent_distance_km` is the maximum source-vertex distance to the track.
- `candidate_count`, `plausible_candidate_count`, and `candidate_rank` expose
  alternatives. `evidence_score = outside_minutes / 10 + extent_km / 5`; lower is
  closer, with no probabilistic meaning.
- `evidence_tier` is `strong`, `borderline`, `weak`, or `none` based on proximity.
  `confidence` is `high` only for accepted rule-based links, otherwise `none`.
  **High means this rule passed, not manual confirmation or known precision.**
- `accepted`, `match_status`, and `decision_reason` control inclusion. Other
  statuses are `ambiguous`, `review_required`, `outside_acceptance`, and
  `unmatched`. Every source record remains represented regardless of status.

```python
from pathlib import Path
import pandas as pd

root = Path("data/linkage")
crosswalk = pd.read_parquet(root / "source_crosswalk.parquet")
linked = pd.read_parquet(root / "tornadoes_linked.parquet")
accepted = crosswalk.loc[crosswalk["accepted"]]
review = crosswalk.loc[crosswalk["plausible"] & ~crosswalk["accepted"]]

# Attach SPC IDs to NCEI details without duplicating accepted event rows.
events = pd.read_parquet("data/analysis/storm_events.parquet")
event_links = accepted.loc[
    accepted["source_table"].eq("storm_events"), ["source_id", "tornado_id"]
].rename(columns={"source_id": "event_id"})
matched_events = events.merge(event_links, on="event_id", how="left", validate="one_to_one")
```

Related NCEI fatalities and locations can join `event_links` through `event_id`
with `validate="many_to_one"`. Do not merge those one-to-many tables together
and then sum duplicated event impacts. EFC IDs do not equal NCEI event IDs.

## County context and modeling

The county bridge uses **accepted NCEI county codes** and the NCEI record year,
not polygon intersections with the generalized map. It only uses county-type
records. `context_available` flags whether a Census county/year exists; raw
missing population/housing values also remain null.

The SPC view adds accepted NCEI record and footprint-region counts, unresolved
plausible candidate counts, separate DAT/SED footprint counts, linked county-year
counts, and missing Census-join counts. Population/housing sums first deduplicate
`(tornado_id, year, county_fips)`, require every linked value to be present, and
stay null if no accepted county context exists. These are **totals across the
linked county-years**, not population or buildings struck and not guaranteed
complete tornado-path coverage. They can overcount a county if an event has
records in two calendar years. Use the bridge for finer analysis.

`linkage_available` and count columns describe data availability, not physical
storm strength; survey/reporting coverage can itself leak rating information.
The SPC view retains post-event dimensions and impacts because it preserves the
original table. **It is not a ready-selected predictor matrix.** Choose features
based on when predictions are intended, retain the SPC EF label, and document
unknown-label exclusions. Do not train on identifiers, confidence, or coverage
flags without assessing their selection bias.

`suggested_split_group` groups all SPC starts on the same UTC day and joins days
connected by plausible NCEI episode/EFC family links. This keeps competing
candidates and known related records together conservatively. It can join several
days into large groups and does not identify all outbreaks. Use grouped or
chronological validation appropriate to the question; no train/test split is
created automatically.

## Evidence and limits

Source definitions: [SPC database fields](https://www.spc.noaa.gov/wcm/data/SPC_severe_database_description.pdf),
[NCEI bulk format](https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/Storm-Data-Bulk-csv-Format.pdf),
and [NOAA footprint documentation](https://storage.googleapis.com/noaa-ncei-ipg/datasets/event-catalog/README.md).
The tests cover fixed offsets/New Year, contained segments, competing tracks,
crossing geometries, missing observations, borderline cases, family conflicts,
regional distances, county deduplication, nulls, and snapshot safety. The audit
includes threshold sensitivity and deterministic review samples. These checks
validate implementation and reveal uncertainty; they do not measure matching
precision against independent, manually labeled ground truth.
