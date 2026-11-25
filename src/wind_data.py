"""Download ERCOT wind generation and archived day-ahead wind forecasts from several weather models.

Run from the repo root:
    EIA_API_KEY=your_key python -m src.wind_data            # hub-height (100 m) providers
    EIA_API_KEY=your_key python -m src.wind_data --surface  # also GEM (80/120 m) and 10 m providers

Writes raw pulls to data/wind/raw/ (never edited, reused on reruns) and one table to
data/wind/wind_hourly.parquet. Index: hour_end_utc, UTC, hour-ending.

Target:    ERCOT hourly wind generation (EIA-930, respondent ERCO, fuel type WND).
Providers: wind forecasts made about 1 and 2 days ahead (Open-Meteo Previous Runs API) at 9 points
           in Texas's main wind regions.
"""
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

START, END = "2024-01-01", "2026-11-09"
OUT = Path("data/wind")
RAW = OUT / "raw"

# Approximate locations in ERCOT's wind regions. A fleet total needs coverage, not precision.
POINTS = {
    "sweetwater": (32.47, -100.41),  # West Texas
    "bigspring":  (32.25, -101.47),  # West Texas
    "snyder":     (32.72, -100.92),  # West Texas
    "mccamey":    (31.13, -102.22),  # Far West Texas
    "knox":       (33.42,  -99.81),  # Rolling Plains
    "amarillo":   (35.22, -101.83),  # Panhandle
    "pampa":      (35.54, -100.96),  # Panhandle
    "taft":       (27.98,  -97.40),  # South coast
    "kenedy":     (26.85,  -97.65),  # South coast
}
MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "gem_seamless",
          "jma_seamless", "meteofrance_seamless", "ukmo_seamless"]
LEADS = {"d1": "wind_speed_100m_previous_day1", "d2": "wind_speed_100m_previous_day2"}
API = "https://previous-runs-api.open-meteo.com/v1/forecast"


def fetch_eia_fuel(respondent="ERCO"):
    """EIA-930 generation by fuel type. 'period' is UTC, hour-ending."""
    key = os.environ["EIA_API_KEY"]
    url = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
    rows, offset = [], 0
    while True:
        params = {"api_key": key, "frequency": "hourly", "data[0]": "value",
                  "facets[respondent][]": respondent,
                  "start": f"{START}T00", "end": f"{END}T23",
                  "sort[0][column]": "period", "sort[0][direction]": "asc",
                  "offset": offset, "length": 5000}
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        page = r.json()["response"]
        rows += page["data"]
        offset += 5000
        if offset >= int(page["total"]):
            break
        time.sleep(0.3)
    df = pd.DataFrame(rows)
    df.to_csv(RAW / f"eia930_fuel_{respondent}.csv", index=False)
    return df


def get_json(params, tries=5):
    """GET with retries and growing waits; the archive API sometimes times out."""
    for attempt in range(tries):
        try:
            r = requests.get(API, params=params, timeout=180)
            if r.status_code == 200:
                return r.json()
            reason = r.text[:200]
        except (requests.RequestException, ValueError) as e:
            reason = f"{type(e).__name__}: {str(e)[:150]}"
        wait = [10, 30, 60, 120, 300][attempt]
        print(f"    attempt {attempt + 1} failed ({reason}); waiting {wait}s")
        time.sleep(wait)
    return None


def fetch_vars(model, name, lat, lon, variables, tag):
    """Several variables x both leads, one model and one point, pulled a year at a time.
    Reuses the raw file if it exists, so reruns don't spend API calls again."""
    path = RAW / f"om_{model}_{name}_{tag}.csv"
    if path.exists():
        h = pd.read_csv(path)
    else:
        hourly = ",".join(f"{v}_previous_day{d}" for v in variables for d in (1, 2))
        chunks = []
        for y in range(pd.Timestamp(START).year, pd.Timestamp(END).year + 1):
            s = max(pd.Timestamp(START), pd.Timestamp(f"{y}-01-01")).strftime("%Y-%m-%d")
            e = min(pd.Timestamp(END), pd.Timestamp(f"{y}-12-31")).strftime("%Y-%m-%d")
            js = get_json({"latitude": lat, "longitude": lon, "models": model, "hourly": hourly,
                           "wind_speed_unit": "ms", "timezone": "GMT", "start_date": s, "end_date": e})
            if js is None:
                print(f"  skip {model} @ {name} {y}: gave up after retries")
                return None
            chunks.append(pd.DataFrame(js["hourly"]))
            time.sleep(1)
        h = pd.concat(chunks, ignore_index=True)
        h.to_csv(path, index=False)
    return to_hour_ending(h)


def to_hour_ending(h):
    """Open-Meteo values are instantaneous, at the top of each hour. EIA reports the average over the
    hour ENDING at t. So average the instants at t-1 and t, and label the result hour-ending t."""
    t = pd.to_datetime(h["time"], utc=True)
    out = {}
    for c in h.columns.drop("time"):
        s = pd.Series(h[c].to_numpy(dtype=float), index=t)
        out[c] = (s + s.shift(1)) / 2
    return pd.DataFrame(out)


def tidy_eia_wind(df):
    df = df.assign(hour_end_utc=pd.to_datetime(df["period"], utc=True),
                   value=pd.to_numeric(df["value"], errors="coerce"))
    wind = df[df["fueltype"] == "WND"]
    return wind.groupby("hour_end_utc")["value"].first().rename("wind_mw")


def to_100m(v80, v120):
    """Power-law interpolation with the hourly shear between 80 m and 120 m.
    Falls back to straight-line interpolation when a speed is ~0 (shear is undefined)."""
    ok = (v80 > 0.1) & (v120 > 0.1)
    power = v80 * (v120 / v80.where(ok)) ** (np.log(100 / 80) / np.log(120 / 80))
    linear = v80 + (v120 - v80) * 0.5
    return power.where(ok, linear)


def build(surface=False):
    RAW.mkdir(parents=True, exist_ok=True)
    table = tidy_eia_wind(fetch_eia_fuel()).to_frame()
    cols = {}
    for model in MODELS:
        for name, (lat, lon) in POINTS.items():
            h = fetch_vars(model, name, lat, lon, ["wind_speed_100m"], "100m")
            if h is None:
                continue
            for lead, var in LEADS.items():
                if var in h:
                    cols[f"ws_{model}_{name}_{lead}"] = h[var]
    if surface:
        for name, (lat, lon) in POINTS.items():
            # GEM has no 100 m level in the archive: interpolate from 80 m and 120 m
            g = fetch_vars("gem_seamless", name, lat, lon, ["wind_speed_80m", "wind_speed_120m"], "80m120m")
            if g is not None:
                for d in (1, 2):
                    cols[f"ws_gem_seamless_{name}_d{d}"] = to_100m(g[f"wind_speed_80m_previous_day{d}"],
                                                                   g[f"wind_speed_120m_previous_day{d}"])
            # GFS seamless blends a different model at short leads near the surface, so the
            # clean 10 m vs 100 m comparison uses gfs_global (pure GFS).
            for model in ["gfs_global", "gfs_seamless", "jma_seamless", "ukmo_seamless"]:
                s = fetch_vars(model, name, lat, lon, ["wind_speed_10m"], "10m")
                if s is not None:
                    for d in (1, 2):
                        cols[f"ws10_{model}_{name}_d{d}"] = s[f"wind_speed_10m_previous_day{d}"]
    table = table.join(pd.DataFrame(cols), how="outer")
    full = pd.date_range(table.index.min(), table.index.max(), freq="h", tz="UTC")
    table.reindex(full).rename_axis("hour_end_utc").to_parquet(OUT / "wind_hourly.parquet")
    print("wrote", OUT / "wind_hourly.parquet")


if __name__ == "__main__":
    build(surface="--surface" in sys.argv)
