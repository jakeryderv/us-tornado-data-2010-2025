# ACS and TIGER/Line: planned tract exposure extension

ACS 5-year tract estimates and TIGER/Line tract boundaries are deferred. They
are excluded from default downloads, default ML columns and the next enrichment
release scope. No Census API key is needed for radar, warnings or NLCD.

The existing backbone retains its annual **county population/housing estimates**
and fixed **2020 simplified county map**. Those are separate products and are not
tract-level estimates or exact counts of people/buildings struck.

## Intended contribution

- [ACS 5-year estimates](https://www.census.gov/programs-surveys/acs/data/data-via-api.html):
  tract population, housing and mobile-home estimates with margins of error.
- [TIGER/Line tracts](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html):
  boundaries matching the ACS geography vintage, for intersection with event areas.

The optional adapters and tests remain available for future work. They use the
ACS period ending one year before the event, preserve missing-value sentinels
and margins of error, and retain exact-vintage tract links. The 2009 TIGER vintage
uses county archives; unavailable geography must remain explicit.

If enabled later, separate `acs_tracts`, `tract_boundaries` and `tornado_tracts`
tables would support retrospective exposure summaries. Area-weighted counts
assume uniform within-tract distribution and require adequate geometry/estimate
coverage. They do not count actual people or buildings struck, and use of the
final tornado footprint prevents treating them as strict onset predictors.

Before adding them, configure Census access, rerun an isolated pilot, reassess
storage/runtime and coverage, and update release provenance and attribution.
Large source archives should remain temporary or optionally cached.
