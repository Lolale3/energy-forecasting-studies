"""Wind turbine SCADA: loading, checks, labeling and loss attribution.

Written for the La Haute Borne open dataset (4 x Senvion MM82), but the rules
use turbine specs and each turbine's own learned behavior, so the same code
can run on another farm by changing SPEC.
"""

import os
import urllib.request
import zipfile

import numpy as np
import pandas as pd

# Published MM82 specs (wind-turbine-models.com). The only farm-specific input.
SPEC = {"rated_kw": 2050, "cut_in": 3.5, "rated_ws": 14.5, "cut_out": 25.0}

NAMES = {"Wind_turbine_name": "turbine", "P_avg": "power_kw", "Ws_avg": "ws",
         "Ba_avg": "pitch", "Ot_avg": "temp_c", "Wa_avg": "wdir"}

# Physical limits used by check(); values outside them are errors, not weather.
RANGES = {"power_kw": (-50, 2100), "ws": (0, 40), "pitch": (-5, 95),
          "temp_c": (-40, 50), "wdir": (0, 360)}

# Labels that count as lost energy. "normal" scatters both ways, so it never counts.
LOSS = ["stopped", "transition", "derated", "underperforming"]

ZIP_URL = "https://raw.githubusercontent.com/NREL/OpenOA/main/examples/data/la_haute_borne.zip"
SCADA_FILE = "la-haute-borne-data-2014-2015.csv"


def download(data_dir):
    """Download the La Haute Borne example data (about 36 MB) once and unzip it."""
    os.makedirs(data_dir, exist_ok=True)
    zpath = os.path.join(data_dir, "la_haute_borne.zip")
    if not os.path.exists(zpath):
        urllib.request.urlretrieve(ZIP_URL, zpath)
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if not name.startswith("__MACOSX") and not os.path.exists(os.path.join(data_dir, name)):
                z.extract(name, data_dir)


def parse_time(stamps, clock="wall"):
    """Turn the file's Date_time strings into UTC.

    clock="offset": trust the +01:00/+02:00 written in the file.
    clock="wall":   ignore the offset and read the clock time as UTC. Testing against
                    ERA5 and MERRA-2 shows the logger ran on UTC (see the notebook).
    """
    if clock == "offset":
        return pd.to_datetime(stamps, utc=True)
    if clock == "wall":
        return pd.to_datetime(stamps.str[:19]).dt.tz_localize("UTC")
    raise ValueError("clock must be 'wall' or 'offset'")


def load_tidy(path, clock="wall", verbose=True):
    """One row per turbine per 10 minutes, readable names, impossible values removed.

    - whole-row blanks (logger down) are dropped
    - temperatures below -40 C (the -273.2 sentinel) and pitch outside -5..95 deg become NaN;
      the row stays, because a broken sensor doesn't make the power reading wrong
    - stamps that appear twice for one turbine are dropped, both copies: two different
      readings share one label and there is no way to tell which is which
    """
    raw = pd.read_csv(path)
    df = raw[["Date_time"] + list(NAMES)].rename(columns=NAMES).copy()
    df["time"] = parse_time(df["Date_time"], clock)
    df = df.drop(columns="Date_time")

    blank = df[["power_kw", "ws", "pitch", "temp_c", "wdir"]].isna().all(axis=1)
    bad_temp = df["temp_c"] < -40
    bad_pitch = ~df["pitch"].between(-5, 95) & df["pitch"].notna()
    df.loc[bad_temp, "temp_c"] = np.nan
    df.loc[bad_pitch, "pitch"] = np.nan
    dup = df.duplicated(["turbine", "time"], keep=False)

    df = df[~blank & ~dup].sort_values(["turbine", "time"]).reset_index(drop=True)
    if verbose:
        print(f"[{clock}] blank: {blank.sum():,} | bad temp: {bad_temp.sum():,} | "
              f"bad pitch: {bad_pitch.sum():,} | duplicates: {dup.sum():,} | kept: {len(df):,}")
    return df


def check(df, min_complete=98.0):
    """Data checks; each row is a count of failures (0 = pass).

    Uniqueness, validity, consistency of the 10-minute grid, and completeness.
    Completeness can't reach 100% after cleaning removes rows, so it passes above a threshold.
    """
    results = {"duplicate turbine+time": int(df.duplicated(["turbine", "time"]).sum())}
    for col, (lo, hi) in RANGES.items():
        x = df[col].dropna()
        results[f"{col} outside {lo} to {hi}"] = int((~x.between(lo, hi)).sum())
    t = df["time"]
    results["off the 10-min grid"] = int(((t.dt.minute % 10 != 0) | (t.dt.second != 0)).sum())
    full = pd.date_range(t.min(), t.max(), freq="10min")
    for name, g in df.groupby("turbine"):
        results[f"{name} missing slots"] = len(full) - g["time"].nunique()

    out = pd.Series(results, name="failures").to_frame()
    out["status"] = out["failures"].map(lambda n: "pass" if n == 0 else "FAIL")
    gaps = out.index.str.endswith("missing slots")
    out.loc[gaps, "% complete"] = (100 * (1 - out.loc[gaps, "failures"] / len(full))).round(2)
    out.loc[gaps, "status"] = out.loc[gaps, "% complete"].map(
        lambda p: "pass" if p >= min_complete else "FAIL")
    return out


def reference_curve(df, spec=SPEC, width=0.5, min_points=20):
    """Each turbine's normal power per wind bin (median) and normal spread (scaled MAD).

    Medians ignore the bad points as long as they are under half a bin.
    The spread has a floor of 2% of rated, so the flat top of the curve doesn't
    flag tiny wobbles. Bins with fewer than min_points rows get no reference.
    """
    d = df[df["ws"].between(spec["cut_in"], spec["cut_out"])].copy()
    d["bin"] = (d["ws"] // width) * width + width / 2
    mad = lambda x: (x - x.median()).abs().median()
    curve = (d.groupby(["turbine", "bin"])["power_kw"]
               .agg(median="median", mad=mad, n="size").reset_index())
    curve["spread"] = np.maximum(1.4826 * curve["mad"], 0.02 * spec["rated_kw"])
    return curve[curve["n"] >= min_points].reset_index(drop=True)


def label_points(df, curve, spec=SPEC, k=3, stop_frac=0.01, ws_margin=1.0,
                 stop_pitch=20, pitch_margin=3, min_derate=3, width=0.5):
    """Give every 10-minute row one label. The first rule that matches wins.

    1 unknown              a key reading is missing, or no reference for that wind
    2 no usable wind       below cut-in + margin, or above cut-out
    3 stopped              power <= stop_frac of rated and pitch far from running pitch
    4 transition           next to a stopped row (a start or stop inside the interval)
    5 derated              below the curve, pitched while below rated, for min_derate rows in a row
    6 underperforming      below the curve by k spreads, pitch at running position
    7 wind sensor suspect  above the curve by k spreads (power too high for the wind reading)
    8 normal               everything else
    """
    d = df.sort_values(["turbine", "time"]).copy()
    d["bin"] = (d["ws"] // width) * width + width / 2
    d = d.merge(curve[["turbine", "bin", "median", "spread"]], on=["turbine", "bin"], how="left")

    # Above the last bin with enough data, expect that turbine's top-bin value
    top = curve.sort_values("bin").groupby("turbine").last()
    above = d["median"].isna() & (d["ws"] > d["turbine"].map(top["bin"]))
    d.loc[above, "median"] = d.loc[above, "turbine"].map(top["median"])
    d.loc[above, "spread"] = d.loc[above, "turbine"].map(top["spread"])

    # Running pitch, learned per turbine: median pitch at 20-80% of rated power
    rated = spec["rated_kw"]
    mid = d["power_kw"].between(0.2 * rated, 0.8 * rated)
    run_pitch = d[mid].groupby("turbine")["pitch"].median()
    d["pitch_up"] = d["pitch"] - d["turbine"].map(run_pitch)
    resid = d["power_kw"] - d["median"]

    def same(s):
        """Neighbor row s steps away is the same turbine, exactly 10 minutes apart."""
        return (d["turbine"].shift(s) == d["turbine"]) & \
               ((d["time"] - d["time"].shift(s)).abs() == pd.Timedelta("10min"))

    missing = d[["power_kw", "ws", "pitch"]].isna().any(axis=1)
    no_wind = (d["ws"] < spec["cut_in"] + ws_margin) | (d["ws"] > spec["cut_out"])
    no_ref = d["median"].isna()
    stopped = (d["power_kw"] <= stop_frac * rated) & (d["pitch_up"] > stop_pitch)
    near_stop = (stopped.shift(1, fill_value=False) & same(1)) | \
                (stopped.shift(-1, fill_value=False) & same(-1))
    below = resid < -k * d["spread"]
    above_curve = resid > k * d["spread"]
    pitched = (d["pitch_up"] > pitch_margin) & (d["power_kw"] < 0.95 * rated)

    # Derate: pitched and below the curve, lasting at least min_derate rows in a row
    cand = (below & pitched & ~stopped).astype(int)
    run_id = ((cand != cand.shift()) | ~same(1)).cumsum()
    run_len = cand.groupby(run_id).transform("size")
    derated = (cand == 1) & (run_len >= min_derate)

    d["label"] = np.select(
        [missing, no_wind, no_ref, stopped, near_stop, derated, below, above_curve],
        ["unknown", "no usable wind", "unknown", "stopped", "transition", "derated",
         "underperforming", "wind sensor suspect"],
        default="normal")
    d["expected_kw"] = d["median"]
    lost = (d["expected_kw"] - d["power_kw"]).clip(lower=0) / 6     # kW over 10 min -> kWh
    d["lost_kwh"] = np.where(d["label"].isin(LOSS), lost, 0.0)
    return d.drop(columns=["median", "pitch_up"])


def loss_table(lab):
    """Lost energy by cause and turbine (MWh), with each turbine's share of potential energy."""
    lost = (lab[lab["label"].isin(LOSS)]
            .pivot_table(index="label", columns="turbine", values="lost_kwh", aggfunc="sum")
            .reindex(LOSS).fillna(0) / 1000)
    lost.loc["total lost"] = lost.sum()
    produced = lab.groupby("turbine")["power_kw"].sum() / 6 / 1000
    lost.loc["produced"] = produced
    lost.loc["% of potential lost"] = 100 * lost.loc["total lost"] / (produced + lost.loc["total lost"])
    lost["farm"] = lost.sum(axis=1)
    lost.loc["% of potential lost", "farm"] = (
        100 * lost.loc["total lost", "farm"] / (lost.loc["produced", "farm"] + lost.loc["total lost", "farm"]))
    return lost


# ---------- outside references: ERA5 and MERRA-2 (both in the zip, both in UTC) ----------

def load_era5(path):
    e = pd.read_csv(path, index_col=0)
    e["time"] = pd.to_datetime(e["datetime"], utc=True)
    return e.set_index("time")


def load_merra2(path):
    m = pd.read_csv(path, index_col=0)
    m["time"] = pd.to_datetime(m["datetime"], utc=True)   # hourly averages stamped at :30
    return m.set_index("time")


def best_lag(x, ref, max_min=180):
    """Lag in minutes where SCADA best matches the reference.

    +60 means SCADA at time t matches the reference at t + 60 min.
    """
    corr = {k * 10: x.corr(ref.shift(-k).reindex(x.index))
            for k in range(-max_min // 10, max_min // 10 + 1)}
    best = max(corr, key=corr.get)
    return best, round(corr[best], 3)


CLOCK_CHANGES = ["2014-03-30", "2014-10-26", "2015-03-29", "2015-10-25"]


def clock_test(df, ref_ws):
    """Best lag 3 weeks before vs 3 weeks after each daylight-saving change.

    The reference has no daylight saving, and the weather is similar on both sides,
    so a correct SCADA clock should give the same lag before and after.
    """
    ref = ref_ws.resample("10min").mean().interpolate()
    farm = df.pivot(index="time", columns="turbine", values="ws").mean(axis=1)
    rows = []
    for day in CLOCK_CHANGES:
        d = pd.Timestamp(day, tz="UTC")
        before = farm[(farm.index >= d - pd.Timedelta("21D")) & (farm.index < d)]
        after = farm[(farm.index >= d + pd.Timedelta("1D")) & (farm.index < d + pd.Timedelta("22D"))]
        b, a = best_lag(before, ref)[0], best_lag(after, ref)[0]
        rows.append({"clock change": day, "lag before (min)": b, "lag after (min)": a, "jump": a - b})
    return pd.DataFrame(rows)


def add_density(lab, era5):
    """Air density rho = p / (R T): SCADA temperature, ERA5 surface pressure."""
    pres = era5["surf_pres"].resample("10min").mean().interpolate()
    out = lab.copy()
    out["rho"] = pres.reindex(out["time"]).values / (287.05 * (out["temp_c"] + 273.15))
    return out
