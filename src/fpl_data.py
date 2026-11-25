"""Download FPL's hourly load, FPL's own day-ahead forecast, and Florida temperatures.

Run from the repo root:
    EIA_API_KEY=your_key python -m src.fpl_data
(In Colab, store the key in Colab Secrets and set os.environ["EIA_API_KEY"] from it. Never paste it into code.)

Writes raw pulls to data/fpl/raw/ (never edited) and one hourly table to data/fpl/fpl_hourly.parquet.
Every timestamp is UTC and HOUR-ENDING, in a column named hour_end_utc.
"""
import io
import os
import time
from pathlib import Path

import pandas as pd
import requests

START, END = "2024-01-01", "2026-11-08"          # inclusive dates, UTC
OUT = Path("data/fpl")
RAW = OUT / "raw"

# FPL-territory airports (ASOS id, lat, lon). Tampa and Orlando are served by other utilities.
STATIONS = {
    "MIA": (25.79, -80.29),   # Miami
    "FLL": (26.07, -80.15),   # Fort Lauderdale
    "PBI": (26.68, -80.10),   # West Palm Beach
    "RSW": (26.54, -81.76),   # Fort Myers
    "SRQ": (27.40, -82.55),   # Sarasota
    "DAB": (29.18, -81.06),   # Daytona Beach
    "PNS": (30.47, -87.19),   # Pensacola
}


def fetch_eia(respondent="FPL", types=("D", "DF")):
    """EIA-930 hourly demand (D) and day-ahead demand forecast (DF). 'period' is UTC, hour-ending."""
    key = os.environ["EIA_API_KEY"]
    url = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
    rows, offset = [], 0
    while True:
        params = {
            "api_key": key, "frequency": "hourly", "data[0]": "value",
            "facets[respondent][]": respondent, "facets[type][]": list(types),
            "start": f"{START}T00", "end": f"{END}T23",
            "sort[0][column]": "period", "sort[0][direction]": "asc",
            "offset": offset, "length": 5000,
        }
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        page = r.json()["response"]
        rows += page["data"]
        offset += 5000
        if offset >= int(page["total"]):
            break
        time.sleep(0.3)
    df = pd.DataFrame(rows)
    df.to_csv(RAW / f"eia930_{respondent}.csv", index=False)
    return df


def fetch_asos(station):
    """Observed temperature (deg F) from Iowa State's ASOS archive, in UTC."""
    s, e = pd.Timestamp(START), pd.Timestamp(END) + pd.Timedelta(days=1)
    url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
           f"station={station}&data=tmpf&tz=Etc/UTC&format=onlycomma&latlon=no&missing=M"
           f"&year1={s.year}&month1={s.month}&day1={s.day}&year2={e.year}&month2={e.month}&day2={e.day}"
           "&report_type=3")                                  # 3 = routine hourly reports
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    (RAW / f"asos_{station}.csv").write_text(r.text)
    return pd.read_csv(io.StringIO(r.text), na_values="M")


def fetch_temp_forecast(station, lat, lon, model="gfs_seamless"):
    """Archived temperature forecasts made about one day ahead (Open-Meteo Previous Runs API)."""
    url = "https://previous-runs-api.open-meteo.com/v1/forecast"
    params = {"latitude": lat, "longitude": lon, "models": model,
              "hourly": "temperature_2m_previous_day1", "temperature_unit": "fahrenheit",
              "timezone": "GMT", "start_date": START, "end_date": END}
    r = requests.get(url, params=params, timeout=120)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["hourly"])
    df.to_csv(RAW / f"openmeteo_{model}_{station}.csv", index=False)
    return df


def tidy_eia(df):
    df = df.assign(hour_end_utc=pd.to_datetime(df["period"], utc=True),
                   value=pd.to_numeric(df["value"], errors="coerce"))
    wide = df.pivot_table(index="hour_end_utc", columns="type", values="value", aggfunc="first")
    return wide.rename(columns={"D": "load_mw", "DF": "fpl_df_mw"})


def tidy_asos(df):
    df = df.assign(t=pd.to_datetime(df["valid"], utc=True), tmpf=pd.to_numeric(df["tmpf"], errors="coerce"))
    # Routine reports come at about :53. Assign each to the hour it ends, e.g. 13:53 -> 14:00.
    df["hour_end_utc"] = df["t"].dt.ceil("h")
    return df.groupby("hour_end_utc")["tmpf"].mean()


def tidy_forecast(df):
    s = df.set_index(pd.to_datetime(df["time"], utc=True))["temperature_2m_previous_day1"]
    s.index.name = "hour_end_utc"
    return s


def build():
    RAW.mkdir(parents=True, exist_ok=True)
    table = tidy_eia(fetch_eia())
    obs, fc = {}, {}
    for st, (lat, lon) in STATIONS.items():
        obs[st] = tidy_asos(fetch_asos(st))
        fc[st] = tidy_forecast(fetch_temp_forecast(st, lat, lon))
        time.sleep(1)
    obs, fc = pd.DataFrame(obs), pd.DataFrame(fc)
    table["temp_obs_f"] = obs.mean(axis=1)
    table["temp_fc_f"] = fc.mean(axis=1)
    table = table.join(obs.add_prefix("obs_")).join(fc.add_prefix("fc_"))
    full = pd.date_range(table.index.min(), table.index.max(), freq="h", tz="UTC")
    table.reindex(full).rename_axis("hour_end_utc").to_parquet(OUT / "fpl_hourly.parquet")
    print("wrote", OUT / "fpl_hourly.parquet")


if __name__ == "__main__":
    build()
