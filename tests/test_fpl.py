"""Tests for src/fpl.py on small synthetic data."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src import fpl  # noqa: E402


def hourly(n=24 * 30, start="2025-01-01"):
    return pd.date_range(start, periods=n, freq="h", tz="UTC")


def daily_load(idx, rng=None):
    hours = np.arange(len(idx))
    load = 17000 + 3000 * np.sin(hours / 24 * 2 * np.pi)
    if rng is not None:
        load = load + rng.normal(0, 100, len(idx))
    return pd.Series(load, index=idx)


def test_flag_load_catches_glitches_not_ramps():
    load = daily_load(hourly())
    load.iloc[100] = 39          # drop
    load.iloc[200] = 32000       # spike
    flagged = fpl.flag_load(load)
    assert flagged.iloc[100] and flagged.iloc[200]
    assert flagged.sum() == 2    # the normal daily ramp is never flagged


def test_flag_temps():
    idx = hourly(48)
    obs = pd.DataFrame({"obs_SRQ": 75.0, "obs_MIA": 80.0}, index=idx)
    obs.iloc[10, 0] = -73        # impossible
    obs.iloc[20, 1] = 50         # 30 F away from its own neighbors
    bad = fpl.flag_temps(obs)
    assert bad.iloc[10, 0] and bad.iloc[20, 1]
    assert bad.values.sum() == 2


def test_half_hour_shift_on_a_ramp():
    f = pd.Series(np.arange(10, dtype=float), index=hourly(10))
    later = fpl.shifted_half_hour(f, "later")
    earlier = fpl.shifted_half_hour(f, "earlier")
    assert later.iloc[5] == 4.5 and earlier.iloc[5] == 5.5


def test_alignment_tests_find_a_half_hour_lead():
    # actual load is an hourly average; the "forecast" is the instantaneous value at the hour mark,
    # i.e. it leads the actuals by 30 minutes. Moving it 30 min later should score best.
    t = np.arange(0, 24 * 60, 0.5)                                 # half-hour grid
    curve = 17000 + 3000 * np.sin(t / 24 * 2 * np.pi)
    inst = pd.Series(curve[::2], index=hourly(len(curve[::2])))   # value at each hour mark
    avg = pd.Series((curve[::2] + np.roll(curve, 1)[::2]) / 2, index=inst.index)  # centered 30 min earlier
    res = fpl.alignment_tests(inst, avg)
    assert res.idxmin() == "moved 30 min later"


def test_lag_features_dont_leak():
    idx = hourly(24 * 20)
    local = pd.DataFrame({"load_mw": daily_load(idx), "temp": 70.0}, index=idx)
    X1 = fpl.features(local, "temp")
    bumped = local.copy()
    bumped.iloc[-47:, 0] += 5000          # change the last 47 hours of load
    X2 = fpl.features(bumped, "temp")
    # the final hour's lags must not see any of those 47 hours
    assert X1.iloc[-1][["lag48", "lag168"]].equals(X2.iloc[-1][["lag48", "lag168"]])


def test_benchmark_perfect_forecast():
    y = daily_load(hourly(), np.random.default_rng(0))
    table, n = fpl.benchmark({"perfect": y, "persistence": y.shift(168)}, y, "persistence")
    assert n == len(y) - 168
    assert table.loc["perfect", "MAPE_%"] == 0
    assert table.loc["perfect", "skill vs persistence (%)"] == 100
