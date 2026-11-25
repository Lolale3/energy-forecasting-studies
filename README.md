# Energy forecasting studies

Hands-on studies of load and wind data, each starting from a practical question and ending with numbers I can defend. They share a few habits:

- **Use only what was known at the time.** A forecast is scored on the weather forecast that existed when it was made, not on what actually happened.
- **Check the clock before anything else.** In two of these studies, the timestamps were off in ways the data itself didn't reveal.
- **Compare against a baseline, with honest uncertainty.** A result has to beat something simple, and the uncertainty has to account for errors being correlated in time.
- **Say what's observed, what's inferred, and what's unknown.**

## Studies

| Study | Question | Headline | Status |
|---|---|---|---|
| [Wind turbine SCADA audit](scada-audit/) | Why do turbines fall off their power curve, and what does each cause cost? | 1.3% of potential energy lost, 83% of it to stops. The export's time-zone offsets were wrong, which I found by testing against ERA5 at every clock change. | Done |
| [FPL day-ahead load forecast](fpl-load/) | How good is a utility's own published day-ahead forecast, and how does an honest model compare? | Most of the apparent error was a 30-minute convention difference (3.21% to 2.40% MAPE, confirmed with controls). On the same 2026 hours, FPL scores 2.53% and my honest model 4.19%. | Done |
| [Wind forecast providers](wind-providers/) | Which weather models earn their place in a day-ahead ERCOT wind forecast, and what happens when feeds fail? | Tied one day ahead, separated two days ahead. An equal blend beats the best single model by 15%, and every provider adds value. The 10 worst days were mostly shared misses blending can't fix. | Done |

Related: [Honest Load Forecast](https://github.com/Lolale3/honest-load-forecast), an hourly ERCOT load forecast backtested only on weather that was available at forecast time.

## Layout

```
scada-audit/      one folder per study: notebooks, README and figures
fpl-load/
wind-providers/
src/              functions the notebooks import
tests/            pytest tests on small synthetic data
data/             downloaded data (not committed); see data/README.md for sources and credits
```

## Running it

```bash
git clone https://github.com/Lolale3/energy-forecasting-studies.git
cd energy-forecasting-studies
pip install -r requirements.txt
pytest tests
```

The SCADA audit and the wind physics notebook download their data on first run. The FPL and wind studies need a free [EIA API key](https://www.eia.gov/opendata/):

```bash
EIA_API_KEY=your_key python -m src.fpl_data     # FPL study
EIA_API_KEY=your_key python -m src.wind_data    # wind study (add --surface for GEM and 10 m winds)
```

Keep the key in an environment variable (or Colab Secrets), never in code. In Google Colab, clone the repo, run the download from the repo root, then `cd` into a study folder and run its notebooks from the top.
