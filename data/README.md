# Data

Nothing in this folder is committed. Each notebook downloads what it needs on first run.

## SCADA audit

`la_haute_borne/` comes from the example data bundled with NREL's [OpenOA](https://github.com/NREL/OpenOA) (`examples/data/la_haute_borne.zip`):

- **SCADA:** based on data for the La Haute Borne wind farm, published by ENGIE under the [Etalab Open Licence 2.0](https://www.etalab.gouv.fr/wp-content/uploads/2018/11/open-licence.pdf). Original source: [ENGIE open data](https://opendata-renewables.engie.com).
- **ERA5:** contains modified Copernicus Climate Change Service information. Neither the European Commission nor ECMWF is responsible for any use of this information.
- **MERRA-2:** NASA Global Modeling and Assimilation Office (GMAO).

## FPL load study

`fpl/` is written by `python -m src.fpl_data` (needs a free EIA API key):

- **Load and FPL's day-ahead forecast:** U.S. Energy Information Administration, [Hourly Electric Grid Monitor (EIA-930)](https://www.eia.gov/electricity/gridmonitor/), via the [EIA API](https://www.eia.gov/opendata/).
- **Observed temperatures:** ASOS airport observations from the [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/request/download.phtml), Iowa State University.
- **Temperature forecasts:** [Open-Meteo](https://open-meteo.com/) Previous Runs API (GFS), licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Wind provider study

`wind/` is written by `python -m src.wind_data` (needs a free EIA API key):

- **ERCOT wind generation:** U.S. Energy Information Administration, [Hourly Electric Grid Monitor (EIA-930)](https://www.eia.gov/electricity/gridmonitor/), fuel type WND, via the [EIA API](https://www.eia.gov/opendata/).
- **Archived wind forecasts:** [Open-Meteo](https://open-meteo.com/) Previous Runs API (GFS, ICON, ECMWF IFS and others), licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

`physics/weather.csv` is the example weather file from [windpowerlib](https://github.com/wind-python/windpowerlib) (MIT License), downloaded by the physics notebook. The turbine power curve comes from windpowerlib's turbine library.
