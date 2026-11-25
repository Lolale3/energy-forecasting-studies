"""Wind forecast provider study: audit, benchmark, blending, fallbacks and worst days.

Every provider gets the SAME power model; only its wind input changes, so score differences
are differences in the weather, not in the modeling.

Splits (nothing is scored on data the model or the blend weights have seen):
  validation: train before 2025-07-01, predict Jul-Dec 2025  -> blend weights are fit here
  test:       train before 2026-01-01, predict 2026          -> every reported score
"""

import numpy as np
import pandas as pd
from scipy.optimize import minimize

TZ = "America/Chicago"                       # ERCOT runs on Central time
VAL_START, TEST_START = "2025-07-01", "2026-01-01"

HUB = {"GFS": "ws_gfs_seamless", "ICON": "ws_icon_seamless",
       "ECMWF": "ws_ecmwf_ifs025", "GEM": "ws_gem_seamless"}            # 100 m
HEIGHT = {"GFS 100 m": "ws_gfs_seamless", "GFS 10 m": "ws10_gfs_global",
          "JMA 10 m": "ws10_jma_seamless", "UKMO 10 m": "ws10_ukmo_seamless"}


# ---------- audit ----------

def cols_for(t, prefix, lead):
    return sorted(c for c in t if c.startswith(prefix + "_") and c.endswith("_" + lead))


def coverage(t, prefixes):
    """When each provider's archive starts, and how complete it is after that."""
    rows = []
    for prefix in prefixes:
        for lead in ("d1", "d2"):
            cols = cols_for(t, prefix, lead)
            fleet = t[cols].mean(axis=1) if cols else pd.Series(dtype=float)
            first = fleet.first_valid_index() if cols else None
            rows.append({"provider": prefix, "lead": lead, "points": len(cols), "first hour": first,
                         "% missing after first": round(fleet.loc[first:].isna().mean() * 100, 2) if first is not None else None,
                         "mean wind (m/s)": round(fleet.mean(), 2) if first is not None else None})
    return pd.DataFrame(rows)


def spikes(w, frac=0.3):
    """Hours more than `frac` of recent capacity away from the median of their neighbors."""
    med = w.rolling(5, center=True, min_periods=3).median()
    cap = w.rolling(24 * 30, center=True, min_periods=24).quantile(0.99)
    bad = (w - med).abs() > frac * cap
    return pd.DataFrame({"wind_mw": w[bad], "neighbor_median": med[bad]})


def lead_check(t, prefix):
    """Does a feed behave like ONE model at both leads? Mean day1 - day2 gap and their correlation."""
    a = t[cols_for(t, prefix, "d1")].mean(axis=1)
    b = t[cols_for(t, prefix, "d2")].mean(axis=1)
    ok = a.notna() & b.notna()
    return {"mean d1 - d2 (m/s)": round((a[ok] - b[ok]).mean(), 2), "correlation": round(a[ok].corr(b[ok]), 3)}


# ---------- one power model per provider ----------

def common_start(t, groups, leads=("d1", "d2")):
    """Latest first-valid hour across every provider and lead in the comparison."""
    return max(t[cols_for(t, p, l)].dropna().index.min() for p in groups.values() for l in leads)


def features(t, prefix, lead):
    X = t[cols_for(t, prefix, lead)].copy()
    X.columns = [c.split("_")[-2] for c in X.columns]        # point names
    local = t.index.tz_convert(TZ)
    X["hour"], X["month"] = local.hour, local.month
    return X


def fit_predict(X, y, train_end, pred_start, pred_end=None):
    import lightgbm as lgb
    tr = (X.index < train_end) & X.notna().all(axis=1) & y.notna()
    pr = (X.index >= pred_start) & X.notna().all(axis=1)
    if pred_end is not None:
        pr &= X.index < pred_end
    n_wind = X.shape[1] - 2                                    # all columns except hour, month
    model = lgb.LGBMRegressor(n_estimators=400, learning_rate=0.05, num_leaves=31,
                              min_child_samples=50, subsample=0.8, subsample_freq=1,
                              colsample_bytree=0.8, monotone_constraints=[1] * n_wind + [0, 0],
                              verbose=-1)                       # more wind can never mean less power
    model.fit(X[tr], y[tr])
    return pd.Series(model.predict(X[pr]), index=X.index[pr])


def has_data(t, prefix):
    cols = cols_for(t, prefix, "d1")
    return bool(cols) and t[cols].notna().any().any()


def predict_all(t, groups):
    """Validation + test predictions for every provider and lead. Providers with no data are skipped."""
    skipped = [k for k, v in groups.items() if not has_data(t, v)]
    groups = {k: v for k, v in groups.items() if has_data(t, v)}
    start = common_start(t, groups)
    t = t[t.index >= start]
    y = t["wind_mw"]
    preds = {}
    for name, prefix in groups.items():
        for lead in ("d1", "d2"):
            X = features(t, prefix, lead)
            val = fit_predict(X, y, VAL_START, VAL_START, TEST_START)
            test = fit_predict(X, y, TEST_START, TEST_START)
            preds[f"{name}|{lead}"] = pd.concat([val, test])
    return pd.DataFrame(preds), start, skipped


def reference_capacity(y, start):
    """Fixed reference for nMAE: the 99th percentile of output in the training years only."""
    return y[(y.index >= start) & (y.index < TEST_START)].quantile(0.99)


def baselines(t, y, start):
    local = t.index.tz_convert(TZ)
    key = pd.Series(list(zip(local.month, local.hour)), index=t.index)
    train = (t.index >= start) & (t.index < TEST_START)
    clim = y[train].groupby(key[train]).mean()
    out = pd.DataFrame(index=t.index)
    out["climatology"] = key.map(clim)
    out["persistence_48h"] = y.shift(48)                     # same hour 2 days ago: known at day-ahead time
    return out


def score(pred, y, cap, ref=None):
    e = pred - y
    row = {"MAE_MW": e.abs().mean(), "nMAE_%": e.abs().mean() / cap * 100,
           "RMSE_MW": np.sqrt((e ** 2).mean()), "bias_MW": e.mean()}
    if ref is not None:
        for name, r in ref.items():
            row[f"skill_vs_{name}_%"] = (1 - (e ** 2).mean() / ((r - y) ** 2).mean()) * 100
    return row


def block_bootstrap_vs_best(errs, n=2000, seed=0):
    """MAE difference of each provider vs the best, resampling whole WEEKS (errors are autocorrelated)."""
    rng = np.random.default_rng(seed)
    wk = errs.abs().groupby(errs.index.tz_convert(None).to_period("W")).agg(["sum", "count"])
    sums, counts = wk.xs("sum", axis=1, level=1), wk.xs("count", axis=1, level=1)
    best = (sums.sum() / counts.sum()).idxmin()
    idx = rng.integers(0, len(wk), size=(n, len(wk)))
    rows = {}
    for p in errs:
        mae_p = sums[p].to_numpy()[idx].sum(1) / counts[p].to_numpy()[idx].sum(1)
        mae_b = sums[best].to_numpy()[idx].sum(1) / counts[best].to_numpy()[idx].sum(1)
        d = mae_p - mae_b
        rows[p] = {"MAE_minus_best_MW": d.mean(), "CI_low": np.percentile(d, 2.5),
                   "CI_high": np.percentile(d, 97.5), "clearly_worse": np.percentile(d, 2.5) > 0}
    return best, pd.DataFrame(rows).T


def by_slice(err_abs, key, cap):
    """nMAE (%) of each column of absolute errors, grouped by `key`."""
    return err_abs.groupby(key, observed=True).mean() / cap * 100


def season_of(index):
    m = index.tz_convert(TZ).month
    return pd.Series(np.select([m.isin([12, 1, 2]), m.isin([3, 4, 5]), m.isin([6, 7, 8])],
                               ["Winter", "Spring", "Summer"], "Fall"), index=index)


def part_of_day(index):
    h = index.tz_convert(TZ).hour
    return pd.Series(pd.cut(h, [-1, 5, 11, 17, 23],
                            labels=["night 0-6", "morning 6-12", "afternoon 12-18", "evening 18-24"]), index=index)


# ---------- blending, value and fallbacks ----------

def boot_diff(err_a, err_b, n=2000, seed=0):
    """MAE(a) - MAE(b) with a 95% interval, resampling whole weeks."""
    wk = pd.DataFrame({"a": err_a.abs(), "b": err_b.abs()}).dropna()
    g = wk.groupby(wk.index.tz_convert(None).to_period("W"))
    sa, sb, c = g["a"].sum().to_numpy(), g["b"].sum().to_numpy(), g["a"].count().to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(c), size=(n, len(c)))
    d = (sa[idx].sum(1) - sb[idx].sum(1)) / c[idx].sum(1)
    return (sa.sum() - sb.sum()) / c.sum(), np.percentile(d, 2.5), np.percentile(d, 97.5)


def fit_weights(P, y):
    """Non-negative weights that sum to 1, minimizing squared error."""
    X, t = P.to_numpy(), y.to_numpy()
    k = X.shape[1]
    scale = t.var()                                        # keeps the optimizer's numbers near 1
    res = minimize(lambda w: ((X @ w - t) ** 2).mean() / scale, np.full(k, 1 / k), method="SLSQP",
                   bounds=[(0, 1)] * k, constraints={"type": "eq", "fun": lambda w: w.sum() - 1})
    return pd.Series(res.x, index=P.columns)


def recent_bias_fix(pred, y, lag_h=48, window_days=14):
    """Subtract the forecast's average error over the previous two weeks, using only errors already
    known at day-ahead time (actuals at least 48 hours old)."""
    err = (pred - y).shift(lag_h)
    bias = err.rolling(24 * window_days, min_periods=24 * 3).mean()
    return pred - bias.fillna(0)


def mae(p, y):
    return (p - y).abs().mean()


def split_lead(P, y, lead):
    """One lead's predictions, with validation and test hours where everything exists."""
    P = P[[c for c in P if c.endswith("|" + lead)]].rename(columns=lambda c: c.split("|")[0])
    both = P.dropna().index.intersection(y.dropna().index)
    val = both[(both >= VAL_START) & (both < TEST_START)]
    test = both[both >= TEST_START]
    return P, both, val, test


def blend_results(P, y, lead, cap):
    P, both, val, test = split_lead(P, y, lead)
    yv, yt, Pv, Pt = y[val], y[test], P.loc[val], P.loc[test]
    w_inv = 1 / Pv.sub(yv, axis=0).pow(2).mean()
    w_inv /= w_inv.sum()
    w_opt = fit_weights(Pv, yv)
    weights = pd.DataFrame({"equal": 1 / P.shape[1], "inverse_MSE": w_inv, "constrained": w_opt})
    best = min(P.columns, key=lambda n: mae(Pt[n], yt))
    cands = {f"best single ({best})": Pt[best], "equal blend": Pt.mean(axis=1),
             "inverse-MSE blend": Pt @ w_inv, "constrained blend": Pt @ w_opt}
    full = P.loc[both]
    cands["equal blend + bias fix"] = recent_bias_fix(full.mean(axis=1), y).loc[test]
    cands[f"best single ({best}) + bias fix"] = recent_bias_fix(full[best], y).loc[test]
    rows = {}
    for k, p in cands.items():
        d, lo, hi = boot_diff(p - yt, Pt[best] - yt)
        rows[k] = {"MAE_MW": mae(p, yt), "nMAE_%": mae(p, yt) / cap * 100, "bias_MW": (p - yt).mean(),
                   "vs_best_MW": d, "CI_low": lo, "CI_high": hi, "clearly_better": hi < 0}
    return weights, pd.DataFrame(rows).T, Pt.sub(yt, axis=0).corr()


def drop_one(P, y, lead):
    """MAE change when each provider is removed from the equal blend (positive = it was helping)."""
    P, both, val, test = split_lead(P, y, lead)
    Pt, yt = P.loc[test], y[test]
    full = Pt.mean(axis=1)
    rows = {}
    for n in P.columns:
        d, lo, hi = boot_diff(Pt.drop(columns=n).mean(axis=1) - yt, full - yt)
        rows[n] = {"MAE_change_MW": d, "CI_low": lo, "CI_high": hi, "clearly_adds_value": lo > 0}
    return pd.DataFrame(rows).T


def fallback_chain(P, y, lead, cap, clim_start):
    """What gets delivered as feeds fail, and what it costs."""
    P, both, val, test = split_lead(P, y, lead)
    Pt, yt = P.loc[test], y[test]
    names = list(P.columns)
    order = sorted(names, key=lambda n: mae(Pt[n], yt))
    chain = {"all providers": Pt.mean(axis=1)}
    for n in names:
        chain[f"{n} down: blend of the rest"] = Pt.drop(columns=n).mean(axis=1)
    chain[f"only {order[0]} left"] = Pt[order[0]]
    chain[f"only {order[-1]} left"] = Pt[order[-1]]
    local = test.tz_convert(TZ)
    key = pd.Series(list(zip(local.month, local.hour)), index=test)
    hist = y[(y.index >= clim_start) & (y.index < TEST_START)]
    hl = hist.index.tz_convert(TZ)
    clim = hist.groupby(pd.Series(list(zip(hl.month, hl.hour)), index=hist.index)).mean()
    chain["every feed down: climatology"] = key.map(clim)
    return pd.DataFrame({"MAE_MW": {k: mae(p, yt) for k, p in chain.items()},
                         "nMAE_%": {k: mae(p, yt) / cap * 100 for k, p in chain.items()}})


# ---------- worst days ----------

PROVIDERS_D1 = ["GFS|d1", "ICON|d1", "ECMWF|d1"]
PROVIDERS_D2 = ["GFS|d2", "ICON|d2", "ECMWF|d2"]


def forecast_table(P, w):
    """Actuals, each provider, the equal blends and the forecast hub-height wind, in Central time."""
    d = P.join(w["wind_mw"])
    d["blend_d1"] = d[PROVIDERS_D1].mean(axis=1)
    d["blend_d2"] = d[PROVIDERS_D2].mean(axis=1)
    d["err"] = d["blend_d1"] - d["wind_mw"]                # positive = we forecast too much wind
    hub = [c for c in w.columns if c.endswith("_d1")
           and c.startswith(("ws_gfs_seamless_", "ws_icon_seamless_", "ws_ecmwf_ifs025_"))]
    d["ws_fc"] = w[hub].mean(axis=1).reindex(d.index)      # 3 models x 9 sites
    d["ws_pct"] = d["ws_fc"].rank(pct=True)
    return d.tz_convert(TZ)


def daily_errors(local, min_hours=20):
    daily = local.groupby(local.index.date).agg(
        hours=("err", "count"), mae=("err", lambda e: e.abs().mean()), bias=("err", "mean"),
        actual=("wind_mw", "mean"), forecast=("blend_d1", "mean"))
    return daily, daily[daily["hours"] >= min_hours]


def diagnose(local, day, max_shift=6):
    """Who missed, by how much, at day 2 too, and would a time shift have fixed it?"""
    g = local.loc[str(day)]
    y = g["wind_mw"]
    row = {"mae": (g["blend_d1"] - y).abs().mean(), "bias": (g["blend_d1"] - y).mean()}
    for c in PROVIDERS_D1:
        row[f"bias {c.split('|')[0]}"] = (g[c] - y).mean()
    row["all same sign"] = len({np.sign(row[f"bias {c.split('|')[0]}"]) for c in PROVIDERS_D1}) == 1
    row["mae d2"] = (g["blend_d2"] - y).abs().mean()
    ext = local["blend_d1"]                                 # full series, so shifts can reach across midnight
    best = min(range(-max_shift, max_shift + 1), key=lambda k: (ext.shift(k).loc[str(day)] - y).abs().mean())
    row["best shift (h)"] = best                            # +3 = fits best moved 3 h later (it was early)
    row["mae at best shift"] = (ext.shift(best).loc[str(day)] - y).abs().mean()
    row["forecast wind (m/s)"] = g["ws_fc"].mean()
    row["wind percentile"] = g["ws_pct"].mean()
    return pd.Series(row)
