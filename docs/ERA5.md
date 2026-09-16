# ERA5: deferred environmental extension

ERA5 is planned for later use. It is **excluded from the default collection, the
next non-ERA5 release scope, and default ML table columns**. Its implementation
has passed a four-event live pilot, including 2010 and 2025, and remains available
as an explicit opt-in. HRRR is retired.

The normal workflow is in [ENRICHMENT.md](ENRICHMENT.md). Do not run the following
commands for the current non-ERA5 collection. For a future separate experiment:

```sh
uv sync --locked --group era5
uv run --group era5 python -m enrichment.pipeline --limit 4 \
  --sources radar warnings era5 nlcd acs tiger \
  --output data/experiments/era5/enrichment
uv run --group era5 python -m enrichment.features \
  --enrichment-dir data/experiments/era5/enrichment \
  --output data/experiments/era5/ml
```

Configure a free CDS account through [CDS setup](https://cds.climate.copernicus.eu/how-to-api)
in `~/.cdsapirc`, or use `CDSAPI_KEY` in the ignored `.env`. Accept both
[single-level](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels?tab=download)
and [pressure-level](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels?tab=download)
dataset terms. Tokens and signed download URLs are never saved in provenance.

- **[Copernicus/ECMWF ERA5](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels):**
  hourly 0.25° reanalysis, covering the full 2010–2025 event cohort. Requests group
  events into 5° tiles by UTC month, separately for single and pressure levels.
  Only selected days and hours are requested (CDS returns their Cartesian
  product); extract the nearest grid point within 25 km at the hour at/before
  reported onset. Decode a shared batch once for its events. Retain the seven
  single-level samples and 22 pressure levels × three variables per event;
  downloaded subset GRIBs are disposable. This is retrospective environmental
  context, not an operational forecast or a tornado-scale wind measurement.

### ERA5 fields and derived profile features

Single levels: native ECMWF CAPE (J/kg), 2 m temperature and dewpoint (K), 10 m
u/v wind (m/s), surface pressure (Pa), and surface geopotential (m²/s²).
CAPE is not renamed SBCAPE/MLCAPE; those parcel definitions are not interchangeable.
CIN and HRRR-specific mixed-layer CAPE are not supplied by this implementation.

Pressure levels: u/v wind and geopotential at 22 levels from 1000 to 200 hPa.
Keep each sampled level in `era5_samples`, including values below the local
surface in the source table. Feature generation masks below-ground levels using
surface pressure and positive AGL height. Height is geopotential minus surface
geopotential, divided by standard gravity 9.80665 m/s². Use the 10 m wind as an
explicit surface (0 m AGL) anchor approximation; pressure-level vertical
resolution limits low-level detail.

Derived retrospective fields include 10 m wind speed, 0–1/0–6 km bulk wind
changes (linear interpolation in AGL height), and 0–1/0–3 km total SRH using
MetPy's Bunkers right-moving storm motion. This is an estimated storm motion,
not the tornado's observed motion. Profiles must reach 6 km for storm-motion/SRH
calculations; incomplete or invalid profiles have null features and a status.
Units, source indices, missingness, configuration and dependency versions remain
explicit. The pipeline requests final reanalysis and rejects ERA5T when its
preliminary experiment-version marker is present.

See [ECMWF documentation](https://confluence.ecmwf.int/pages/viewpage.action?pageId=669811810),
[MetPy Bunkers method](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.bunkers_storm_motion.html)
and [SRH method](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.storm_relative_helicity.html).

## Storage, coverage and publication

The optional `era5_samples.parquet` contains one row per event/hour/variable/level,
with values, units, grid location/distance, ECMWF parameter IDs, dataset, version
and source asset IDs. Seven single-level fields plus 22 pressure levels × three
variables produce 73 samples per event. Raw GRIB subsets are disposable under
the same compact-storage policy; original request payloads and checksums remain.

`post_era5_*` fields appear only in the retrospective ML view when this source
was explicitly requested. Reanalysis was produced after the event and must not
be treated as information available at reported onset.

The earlier four-event pilot is preserved locally at
`data/experiments/era5_pilot/`; reports are under
[`reports/enrichment/history/era5/`](../reports/enrichment/history/era5/).
These are excluded from release payloads.

The full cohort would currently require 2,867 tile/month batches and 5,734 CDS
requests. Eight pilot requests took 433.6 seconds. At one minute per request,
ERA5 alone would take about four days; larger requests and queues can add much
more time. This delay does not apply to the default non-ERA5 run.

An ERA5 release would require its own attribution and licence updates; it is
Copernicus/ECMWF data, not U.S. Government Works. See [DATA_SOURCES.md](DATA_SOURCES.md).
