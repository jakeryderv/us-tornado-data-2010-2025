# SPC-centered linkage audit

Algorithm `spc-time-geometry-v1`; generated 2026-09-16T00:32:56.519546+00:00.
The input/output hashes and rules are recorded in `data/analysis/manifest.json` (SHA-256 `d1f00ea6789277d70f4bf5a78fc2a01bc7e7d4fc897d7d323ff0128d2ec82a48`).

This is a coverage and implementation audit of automatic research links, **not an independently measured precision/recall score**. The six supporting source tables remain unchanged; the main tornado table retains every original SPC column and adds linkage summaries. No EF rating was used to select a match.

| Source records | Total | Accepted | Plausible, unresolved | Only outside acceptance | No candidates |
|---|---:|---:|---:|---:|---:|
| NCEI / storm_events | 23,189 | 20,842 | 2,216 | 99 | 32 |
| DAT / tornado_footprints | 16,465 | 9,639 | 4,048 | 752 | 2,026 |
| SED / tornado_footprints | 8,393 | 7,562 | 779 | 37 | 15 |

All **20,164 SPC tornadoes** and **48,047 NCEI/footprint source records** are represented. The crosswalk contains 95,222 candidate/no-candidate rows.
Accepted NCEI links cover **18,313** SPC tracks; footprints cover **14,488**; **14,341** have both. **1,704** have neither and remain in the linked table.
The nine main tables plus annual summary total **29,329,113 bytes**.

## Sensitivity

The acceptance rule is 2 minutes/2 km with a unique candidate inside the wider 10-minute/5-km ambiguity guard. Tightening the accepted set to 1 minute/1 km gives the following counts; this tests threshold dependence, not accuracy. The wider ambiguity guard stays fixed.

| Maximum interval extension / directed distance | NCEI records | Footprint regions | SPC tracks with any link |
|---|---:|---:|---:|
| 1 min / 1 km | 20,204 | 15,640 | 18,229 |
| 2 min / 2 km | 20,842 | 17,201 | 18,460 |

## Snapshot review notes (v2.0.0 source tables)

- **Moore, May 20, 2013:** `efc:2013:DAT:47712` is a unique plausible candidate for `spc:2013:1305201356-01`, with matching event timestamp and close geometry. It remains review-required because its only event time is `stormdate`; no valid polygon/line interval is present. This documents a deliberate false-negative risk from strict timestamp eligibility, not absence of the footprint.
- **NCEI 309725:** a 5-minute interval discrepancy and about 4.25 km directed geometry discrepancy make this a borderline candidate for `spc:2011:1104270137-01`. Its narrative describes a substantially longer multi-county track and its recorded rating differs. It is retained for review, not accepted. The narrative/rating observations are diagnostic only.
- **NCEI 1297797:** endpoint geometry agrees closely, but the interval extends 10 minutes beyond the SPC record. It remains review-required instead of accepting location alone.
- **Joplin, May 22, 2011:** inspect the accepted NCEI segments and footprint regions linked to `spc:2011:1105221634-01` in the sample. Their separate rows remain separate; county totals deduplicate county/year keys.

These targeted inspections were made against the v2.0.0 source tables on September 15, 2026. They are historical notes if this audit is regenerated with newer inputs, not a random labeled validation set. No per-event manual override was added. `review_sample.csv` includes five deterministically sampled candidate rows per available source/status stratum plus these examples, including alternate candidates.

## Label differences and split groups

Among 19,430 accepted NCEI/SPC pairs with both ratings known, 1,083 have different recorded ratings. Segment-level maxima, later source revisions, and linkage errors can all contribute; disagreement alone cannot identify a wrong link. These labels were inspected only after matching and remain unchanged.
The suggested split grouping has 2,277 groups, with up to 400 SPC tracks in one group. It combines UTC days and plausible episode/family links, can span several days, and is not a verified outbreak catalog.

## Coverage by SPC year

| Year | SPC tracks | With NCEI | With footprints | With any accepted link |
|---|---:|---:|---:|---:|
| 2010 | 1,302 | 1,151 | 939 | 1,151 |
| 2011 | 1,704 | 1,537 | 1,261 | 1,539 |
| 2012 | 948 | 867 | 760 | 867 |
| 2013 | 916 | 850 | 606 | 851 |
| 2014 | 928 | 833 | 610 | 837 |
| 2015 | 1,182 | 1,078 | 716 | 1,078 |
| 2016 | 976 | 914 | 649 | 914 |
| 2017 | 1,442 | 1,331 | 929 | 1,333 |
| 2018 | 1,138 | 1,044 | 757 | 1,045 |
| 2019 | 1,534 | 1,409 | 1,040 | 1,429 |
| 2020 | 1,090 | 1,006 | 828 | 1,018 |
| 2021 | 1,328 | 1,200 | 1,010 | 1,226 |
| 2022 | 1,167 | 1,092 | 912 | 1,096 |
| 2023 | 1,321 | 1,178 | 970 | 1,184 |
| 2024 | 1,805 | 1,600 | 1,389 | 1,622 |
| 2025 | 1,383 | 1,223 | 1,112 | 1,270 |

## Reproduce

```sh
uv run python scripts/build_crosswalk.py
uv run python scripts/audit_crosswalk.py
uv run python -m unittest discover -s tests -v
```

See [linkage methods](../../docs/LINKAGE.md) for thresholds, source time definitions, county context, loading examples, and limitations. This canonical linkage is included in v2.1.0 / Kaggle 6.
