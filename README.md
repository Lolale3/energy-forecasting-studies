# Energy forecasting studies

Hands-on studies of load and wind data, each starting from a practical question and ending with numbers I can defend. They share a few habits:

- **Use only what was known at the time.** A forecast is scored on the weather forecast that existed when it was made, not on what actually happened.
- **Check the clock before anything else.** In the SCADA study, the timestamps turned out to be wrong in a way the file itself didn't reveal.
- **Compare against a baseline, with honest uncertainty.** A result has to beat something simple, and the uncertainty has to account for errors being correlated in time.
- **Say what's observed, what's inferred, and what's unknown.**

## Studies

| Study | Question | Headline | Status |
|---|---|---|---|
| [Wind turbine SCADA audit](scada-audit/) | Why do turbines fall off their power curve, and what does each cause cost? | 1.3% of potential energy lost, 83% of it to stops. The export's time-zone offsets were wrong, which I found by testing against ERA5 at every clock change. | Done |
| FPL load check | How good is a utility's own published day-ahead load forecast? | *Coming* | In progress |
| Wind forecast providers | Which weather model gives the best day-ahead wind power forecast for ERCOT? | *Coming* | In progress |

Related: [Honest Load Forecast](https://github.com/Lolale3/honest-load-forecast), an hourly ERCOT load forecast backtested only on weather that was available at forecast time.

## Layout

```
scada-audit/      notebook, README and figures for each study
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

Each notebook downloads its own data on first run. In Google Colab, clone the repo, `cd` into a study folder, and run its notebook from the top.
