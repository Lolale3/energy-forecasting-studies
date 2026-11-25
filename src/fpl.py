"""FPL load study: cleaning, forecast alignment, scoring, and a simple honest load model."""

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar
from sklearn.linear_model import Ridge

STATIONS = ["MIA", "FLL", "PBI", "RSW", "SRQ", "DAB", "PNS"]
TEMP_BANDS = [0, 45, 55, 65, 75, 85, 110]
BAND_LABELS = ["<45", "45-55", "55-65", "65-75", "75-85", "85+"]


# ---------- cleaning ----------

def flag_load(load, floor=5000, jump=0.25):
    """Impossible values, and hours more than `jump` away from the median of their neighbors."""
    med = load.rolling(5, center=True, min_periods=3).median()
    return (load < floor) | ((load - med).abs() / med > jump)


def flag_temps(obs, lo=15, hi=105, jump=15):
    """Out-of-range readings, or readings far from the same station's nearby hours (deg F)."""
    own = obs.rolling(5, center=True, min_periods=3).median()
    return (obs < lo) | (obs > hi) | (obs - own).abs().gt(jump)


def clean(df, stations=STATIONS, verbose=True):
    """Blank out flagged load and temperatures, then average the same stations for observed and forecast."""
    df = df.copy()
    bad_load = flag_load(df["load_mw"])
    if verbose:
        print("load glitches:")
        print(df.loc[bad_load, ["load_mw"]].to_string())
    df.loc[bad_load, "load_mw"] = np.nan

    obs_cols = [f"obs_{s}" for s in stations]
    bad_t = flag_temps(df[obs_cols])
    if verbose:
        print("bad temperature readings per station:", bad_t.sum()[bad_t.sum() > 0].to_dict())
    df[obs_cols] = df[obs_cols].mask(bad_t)

    keep = [s for s in stations if df[f"obs_{s}"].notna().any()]
    if verbose and len(keep) < len(stations):
        print("stations with no observations, dropped from both averages:", sorted(set(stations) - set(keep)))
    df["temp_obs_f"] = df[[f"obs_{s}" for s in keep]].mean(axis=1)
    df["temp_fc_f"] = df[[f"fc_{s}" for s in keep]].mean(axis=1)
    return df


# ---------- scoring ----------

def ape(pred, actual):
    return (pred - actual).abs() / actual * 100


def score(err, actual):
    """MAPE (%) and mean bias (MW) of an error series."""
    a = ape(actual + err, actual)
    return pd.Series({"hours": int(a.count()), "MAPE_%": a.mean(), "bias_MW": err[a.notna()].mean()})


def score_by(err, actual, key):
    frame = pd.DataFrame({"err": err, "actual": actual, "key": key})
    return frame.groupby("key", observed=True)[["err", "actual"]].apply(
        lambda g: score(g["err"], g["actual"]))


def temp_band(temp):
    return pd.cut(temp, TEMP_BANDS, labels=BAND_LABELS)


def shifted_half_hour(f, direction="later"):
    """Average neighboring hours: moves an hourly series ~30 minutes later (or earlier)."""
    if direction == "later":
        return (f + f.shift(1)) / 2
    if direction == "earlier":
        return (f + f.shift(-1)) / 2
    raise ValueError("direction must be 'later' or 'earlier'")


def alignment_tests(f, actual):
    """The alignment experiment, with controls."""
    tests = {
        "as reported": f,
        "moved 1 h later": f.shift(1),
        "moved 1 h earlier": f.shift(-1),
        "moved 30 min later": shifted_half_hour(f, "later"),
        "moved 30 min earlier": shifted_half_hour(f, "earlier"),
        "smoothed, no shift": (f.shift(-1) + f + f.shift(1)) / 3,
    }
    return pd.Series({k: ape(s, actual).mean() for k, s in tests.items()}, name="MAPE_%")


# ---------- an honest load model ----------

def features(local, temp_col):
    """One row per hour. Temperature hinges let the response bend for heat and cold.
    Lags are 48 h and 168 h, so they're known when a day-ahead forecast is made."""
    T = local[temp_col]
    X = pd.DataFrame(index=local.index)
    X["cool"] = (T - 65).clip(lower=0)
    X["cool2"] = (T - 80).clip(lower=0)
    X["heat"] = (60 - T).clip(lower=0)
    X["heat2"] = (45 - T).clip(lower=0)
    X["lag48"] = local["load_mw"].shift(48)
    X["lag168"] = local["load_mw"].shift(168)
    X["trend"] = (local.index - local.index[0]).days / 365
    days = local.index.normalize().tz_localize(None)
    hol = USFederalHolidayCalendar().holidays(days.min(), days.max())
    X["holiday"] = days.isin(hol).astype(int)
    dow = pd.get_dummies(local.index.dayofweek, prefix="dow", drop_first=True, dtype=int)
    return X.join(dow.set_axis(local.index))


def fit_predict(local, train_col, test_col, split="2026-01-01"):
    """One Ridge model per local hour of day. Train with train_col temps, predict with test_col temps."""
    preds = pd.Series(np.nan, index=local.index)
    hours = local.index.hour
    train_mask, test_mask = local.index < split, local.index >= split
    Xtr_all, Xte_all = features(local, train_col), features(local, test_col)
    for h in range(24):
        tr = train_mask & (hours == h)
        te = test_mask & (hours == h)
        Xtr, ytr = Xtr_all[tr], local.loc[tr, "load_mw"]
        ok = Xtr.notna().all(axis=1) & ytr.notna()
        model = Ridge(alpha=1.0).fit(Xtr[ok], ytr[ok])
        Xte = Xte_all[te]
        okt = Xte.notna().all(axis=1)
        preds[Xte[okt].index] = model.predict(Xte[okt])
    return preds


def benchmark(forecasts, actual, baseline):
    """MAPE, bias and skill (by squared error, vs `baseline`) on the hours where every forecast exists."""
    hours = actual.dropna().index
    for f in forecasts.values():
        hours = hours.intersection(f.dropna().index)
    y = actual.loc[hours]
    base_mse = ((forecasts[baseline].loc[hours] - y) ** 2).mean()
    rows = {}
    for name, f in forecasts.items():
        e = f.loc[hours] - y
        rows[name] = {"MAPE_%": ape(f.loc[hours], y).mean(), "bias_MW": e.mean(),
                      f"skill vs {baseline} (%)": (1 - (e ** 2).mean() / base_mse) * 100}
    return pd.DataFrame(rows).T, len(hours)
