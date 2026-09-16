# Research coverage

All 20,164 backbone tornadoes remain included. CSV counts/fractions use the entire
stratum, including unknown labels, as denominator. Each file is split by year,
SPC state/territory, or EF class. These are sample/feature summaries, not observing-system coverage estimates.

- Unknown EF targets: 1,525.
- Land-cover fractions missing: 317.
- Impervious means missing: 316.
- NLCD outside selected coverage: 17.
- Completed land-cover samples with zero pixel centers: 299.
- Onset shear missing: 13,260.
- Onset tornado-warning lead missing: 8,819.

EF counts: {"0": 9197, "1": 7149, "2": 1776, "3": 426, "4": 83, "5": 8, "unknown": 1525}.

`summary.json` records input hashes and metric definitions. `counts_by_*` and
`fractions_by_*` cover known targets, accepted/unresolved links, county context,
radar/warning feature missingness and positive detections, source job status,
footprint method, and usable NLCD sampling. `linkage_by_source_year.csv` retains
all decision categories; candidate-row counts must not be interpreted as event counts.

A zero radar count is no qualifying product record, not proof of no rotation or
working radar coverage. A zero active-warning count is the reconstructed archive
state, not an independently complete warning history. Missing lead time can mean
no active tornado warning or missing original issuance. No coverage adjustment,
label correction, imputation, causal inference or train/test fitting is performed.

Regenerate with `python -m scripts.research_report --data-dir DATA --output OUTPUT`.
