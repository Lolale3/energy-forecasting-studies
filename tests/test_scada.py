"""Tests for src/scada.py on small synthetic data, so they run without downloading anything."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.scada import SPEC, check, label_points, load_tidy, parse_time, reference_curve  # noqa: E402


# ---------- loading ----------

def write_raw(tmp_path, rows):
    cols = ["Wind_turbine_name", "Date_time", "Ba_avg", "P_avg", "Ws_avg", "Va_avg",
            "Ot_avg", "Ya_avg", "Wa_avg"]
    path = tmp_path / "scada.csv"
    pd.DataFrame(rows, columns=cols).to_csv(path, index=False)
    return path


def test_parse_time_wall_vs_offset():
    s = pd.Series(["2014-07-01T12:00:00+02:00"])
    assert parse_time(s, "offset")[0] == pd.Timestamp("2014-07-01 10:00", tz="UTC")
    assert parse_time(s, "wall")[0] == pd.Timestamp("2014-07-01 12:00", tz="UTC")
    with pytest.raises(ValueError):
        parse_time(s, "local")


def test_load_tidy_cleans(tmp_path):
    rows = [
        # normal row
        ["T1", "2014-01-01T00:00:00+01:00", -1, 500, 8, 0, 5, 180, 180],
        # sentinel temperature -> NaN, row kept
        ["T1", "2014-01-01T00:10:00+01:00", -1, 510, 8, 0, -273.2, 180, 180],
        # impossible pitch -> NaN, row kept
        ["T1", "2014-01-01T00:20:00+01:00", 262, 520, 8, 0, 5, 180, 180],
        # duplicated stamp: both copies dropped
        ["T1", "2014-01-01T00:30:00+01:00", -1, 530, 8, 0, 5, 180, 180],
        ["T1", "2014-01-01T00:30:00+01:00", -1, 900, 9, 0, 5, 180, 180],
        # blank row: dropped
        ["T1", "2014-01-01T00:40:00+01:00", None, None, None, None, None, None, None],
        # same stamp, other turbine: kept
        ["T2", "2014-01-01T00:30:00+01:00", -1, 530, 8, 0, 5, 180, 180],
    ]
    df = load_tidy(write_raw(tmp_path, rows), verbose=False)
    assert len(df) == 4
    assert df["temp_c"].isna().sum() == 1
    assert df["pitch"].isna().sum() == 1
    assert not df.duplicated(["turbine", "time"]).any()
    assert set(df["turbine"]) == {"T1", "T2"}


# ---------- checks ----------

def tidy_frame(n=12):
    t = pd.date_range("2014-01-01", periods=n, freq="10min", tz="UTC")
    return pd.DataFrame({"turbine": "T1", "time": t, "power_kw": 500.0, "ws": 8.0,
                         "pitch": -1.0, "temp_c": 5.0, "wdir": 180.0})


def test_check_passes_clean_data():
    assert (check(tidy_frame())["status"] == "pass").all()


def test_check_catches_problems():
    df = tidy_frame()
    df.loc[1, "power_kw"] = 5000                          # out of range
    df.loc[2, "time"] += pd.Timedelta("3min")             # off the 10-minute grid
    df = pd.concat([df, df.iloc[[3]]])                    # duplicate
    out = check(df)
    assert out.loc["duplicate turbine+time", "failures"] == 1
    assert out.loc["power_kw outside -50 to 2100", "failures"] == 1
    assert out.loc["off the 10-min grid", "failures"] == 1


def test_check_ignores_nan():
    df = tidy_frame()
    df.loc[0, "temp_c"] = np.nan
    assert check(df).loc["temp_c outside -40 to 50", "failures"] == 0


# ---------- labeling ----------

def synthetic_turbine(seed=0):
    """Ten days of a healthy turbine following a simple cubic curve, plus a little noise."""
    rng = np.random.default_rng(seed)
    t = pd.date_range("2014-01-01", periods=1440, freq="10min", tz="UTC")
    ws = rng.uniform(5, 12, len(t))
    power = np.clip(2050 * ((ws - 3.5) / (14.5 - 3.5)) ** 3 * 1.6, 0, 2050)
    power = power + rng.normal(0, 15, len(t))
    return pd.DataFrame({"turbine": "T1", "time": t, "power_kw": power, "ws": ws,
                         "pitch": -1.0, "temp_c": 10.0, "wdir": 200.0})


def label_after(df):
    return label_points(df, reference_curve(df)).set_index("time")["label"]


def test_stop_and_transitions():
    df = synthetic_turbine()
    df.loc[500, ["power_kw", "pitch"]] = [-3.0, 90.0]     # stopped
    lab = label_after(df)
    t = df["time"]
    assert lab[t[500]] == "stopped"
    assert lab[t[499]] == "transition" and lab[t[501]] == "transition"


def test_derate_needs_a_run():
    df = synthetic_turbine()
    run = [700, 701, 702]                                  # 3 in a row: derate
    lone = 900                                             # 1 alone: not a derate
    for i in run + [lone]:
        df.loc[i, "ws"] = 11.0
        df.loc[i, ["power_kw", "pitch"]] = [600.0, 7.0]
    lab = label_after(df)
    t = df["time"]
    assert all(lab[t[i]] == "derated" for i in run)
    assert lab[t[lone]] == "underperforming"


def test_sensor_suspect_and_no_wind():
    df = synthetic_turbine()
    df.loc[300, ["ws", "power_kw"]] = [5.5, 1500.0]       # far too much power for the wind
    df.loc[301, ["ws", "power_kw"]] = [2.0, 0.0]          # below cut-in + margin
    lab = label_after(df)
    t = df["time"]
    assert lab[t[300]] == "wind sensor suspect"
    assert lab[t[301]] == "no usable wind"


def test_losses_only_for_loss_labels():
    df = synthetic_turbine()
    df.loc[500, ["power_kw", "pitch"]] = [-3.0, 90.0]
    lab = label_points(df, reference_curve(df))
    assert (lab.loc[lab["label"] == "normal", "lost_kwh"] == 0).all()
    assert lab.loc[lab["label"] == "stopped", "lost_kwh"].sum() > 0
