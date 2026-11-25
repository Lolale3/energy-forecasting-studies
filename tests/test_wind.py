"""Tests for src/wind.py and src/wind_data.py on small synthetic data."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src import wind, wind_data  # noqa: E402


def hourly(n, start="2025-01-01"):
    return pd.date_range(start, periods=n, freq="h", tz="UTC")


# ---------- download helpers ----------

def test_hour_ending_average():
    h = pd.DataFrame({"time": ["2025-01-01T00:00", "2025-01-01T01:00", "2025-01-01T02:00"],
                      "wind_speed_100m_previous_day1": [4.0, 6.0, 10.0]})
    out = wind_data.to_hour_ending(h)["wind_speed_100m_previous_day1"]
    assert np.isnan(out.iloc[0])
    assert out.iloc[1] == 5.0 and out.iloc[2] == 8.0      # mean of the instants at t-1 and t


def test_to_100m_between_80_and_120():
    v80, v120 = pd.Series([8.0, 0.0]), pd.Series([10.0, 2.0])
    v100 = wind_data.to_100m(v80, v120)
    assert 8.0 < v100.iloc[0] < 10.0                       # power law, sheared case
    assert v100.iloc[1] == 1.0                             # falls back to linear when a speed is ~0


def test_tidy_eia_keeps_only_wind():
    df = pd.DataFrame({"period": ["2025-01-01T01", "2025-01-01T01"], "fueltype": ["WND", "SUN"],
                       "value": ["15000", "3000"]})
    s = wind_data.tidy_eia_wind(df)
    assert list(s) == [15000.0]


# ---------- scoring and bootstrap ----------

def test_block_bootstrap_flags_a_clearly_worse_provider():
    idx = hourly(24 * 7 * 20)
    rng = np.random.default_rng(0)
    good = pd.Series(rng.normal(0, 1000, len(idx)), index=idx)
    similar = pd.Series(rng.normal(0, 1000, len(idx)), index=idx)   # same accuracy, different errors
    errs = pd.DataFrame({"good": good, "similar": similar, "bad": good * 2})
    best, boot = wind.block_bootstrap_vs_best(errs)
    assert best in ("good", "similar")
    other = "similar" if best == "good" else "good"
    assert boot.loc["bad", "clearly_worse"] and not boot.loc[other, "clearly_worse"]


def test_fit_weights_sum_to_one_and_find_the_better_input():
    idx = hourly(500)
    rng = np.random.default_rng(1)
    y = pd.Series(rng.normal(10000, 3000, len(idx)), index=idx)
    P = pd.DataFrame({"a": y + rng.normal(0, 200, len(idx)), "b": y + rng.normal(0, 2000, len(idx))})
    w = wind.fit_weights(P, y)
    assert abs(w.sum() - 1) < 1e-6 and (w >= -1e-9).all()
    assert w["a"] > 0.9


def test_recent_bias_fix_only_uses_old_errors():
    idx = hourly(24 * 30)
    y = pd.Series(10000.0, index=idx)
    pred = y + 500                                          # steady over-forecast
    fixed = wind.recent_bias_fix(pred, y)
    assert abs(fixed.iloc[-1] - y.iloc[-1]) < 1             # bias learned and removed
    assert (fixed.iloc[:48] == pred.iloc[:48]).all()        # nothing known in the first 48 hours


# ---------- worst days ----------

def test_diagnose_finds_a_timing_shift():
    idx = hourly(24 * 6, "2025-06-01 05:00")                # starts at local midnight (Central)
    hours = np.arange(len(idx))
    actual = 10000 + 5000 * np.sin(hours / 24 * 2 * np.pi)
    early = 10000 + 5000 * np.sin((hours + 4) / 24 * 2 * np.pi)   # forecast 4 hours early
    P = pd.DataFrame({c: early for c in wind.PROVIDERS_D1 + wind.PROVIDERS_D2}, index=idx)
    w = pd.DataFrame({"wind_mw": actual, "ws_gfs_seamless_a_d1": 8.0,
                      "ws_icon_seamless_a_d1": 8.0, "ws_ecmwf_ifs025_a_d1": 8.0}, index=idx)
    local = wind.forecast_table(P, w)
    row = wind.diagnose(local, "2025-06-03")
    assert row["best shift (h)"] == 4
    assert row["mae at best shift"] < 1


def test_daily_errors_drops_short_days():
    idx = hourly(30, "2025-06-01 05:00")                     # one full local day plus 6 hours
    local = pd.DataFrame({"err": 100.0, "wind_mw": 5000.0, "blend_d1": 5100.0},
                         index=idx.tz_convert(wind.TZ))
    all_days, full = wind.daily_errors(local)
    assert len(all_days) == 2 and len(full) == 1
