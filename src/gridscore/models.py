"""Forecasters. Every model is a pure function of a Context.

CAUSALITY CONTRACT: a model may only read what is inside Context. The Context is
built by issue.py and contains (a) actuals with period-end <= issue_time - AVAIL_LAG,
(b) NESO's forecast snapshot taken at issue time, (c) residuals of earlier forecasts
whose actual was *fetched* at or before issue_time. tests/test_causality.py perturbs
everything outside this set and asserts that forecasts are bit-identical.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import (AP_FIT_DAYS, AP_MIN_PAIRS, CLIM_MIN_DAYS, CLIM_MIN_PER_KEY, CLIM_WINDOW_DAYS,
                     CQR_MIN_RESID, CQR_WINDOW_DAYS, MODELS, QUANTILES, STEP, STEP_GROUPS)
from .timeutil import local_keys


@dataclass
class Context:
    issue_time: pd.Timestamp
    targets: pd.DatetimeIndex                 # 'from' stamps of the 96 target periods
    history: pd.DataFrame                     # cols: from_, actual (UTC); only periods already known
    neso: pd.Series                           # NESO forecast indexed by target stamp (may contain NaN)
    residual_memory: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["step", "target_from", "resid"]))


@dataclass
class Result:
    model_id: str
    q: np.ndarray | None          # shape (n_targets, 7) or None if not issued
    status: str                   # "ok" | "not_ready"
    reason: str = ""


# ---------------------------------------------------------------- helpers
def conformal_quantile(res: np.ndarray, tau: float) -> float:
    """Finite-sample-corrected empirical quantile (split-conformal style).

    Upper levels use rank ceil((n+1)tau), lower levels floor((n+1)tau), so that under
    exchangeability the interval [Q_lo, Q_hi] has coverage >= nominal. Time series are
    NOT exchangeable: the guarantee is only approximate and is audited live by the scoreboard.
    """
    s = np.sort(np.asarray(res, dtype=float))
    n = len(s)
    if n == 0:
        return float("nan")
    if tau > 0.5:
        k = min(n, int(np.ceil((n + 1) * tau)))
    elif tau < 0.5:
        k = max(1, int(np.floor((n + 1) * tau)))
    else:
        return float(np.median(s))
    return float(s[k - 1])


def _finalise(q: np.ndarray) -> np.ndarray:
    """Non-crossing (sort) and physical lower bound (>= 0 gCO2/kWh)."""
    q = np.sort(q, axis=1)
    return np.clip(q, 0.0, None)


def _grid(history: pd.DataFrame) -> pd.Series:
    s = history.set_index("from_")["actual"].astype(float).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    full = pd.date_range(s.index.min(), s.index.max(), freq=STEP, tz="UTC")
    return s.reindex(full)


# ---------------------------------------------------------------- models
def neso_point(ctx: Context) -> Result:
    v = ctx.neso.reindex(ctx.targets).to_numpy(dtype=float)
    q = np.repeat(v[:, None], len(QUANTILES), axis=1)
    return Result("neso_point", np.clip(q, 0, None), "ok")


def climatology(ctx: Context) -> Result:
    y = _grid(ctx.history)
    y = y[y.index >= y.index.max() - pd.Timedelta(days=CLIM_WINDOW_DAYS)]
    if y.notna().sum() < CLIM_MIN_DAYS * 48 * 0.8:
        return Result("climatology", None, "not_ready", f"history {y.notna().sum()/48:.1f} d < {CLIM_MIN_DAYS} d")
    hh, we = local_keys(y.index)
    key = we * 48 + hh
    tq = ctx.targets
    thh, twe = local_keys(tq)
    tkey = twe * 48 + thh
    vals = y.to_numpy()
    out = np.full((len(tq), len(QUANTILES)), np.nan)
    cache: dict[int, np.ndarray] = {}
    for i, k in enumerate(tkey):
        if k not in cache:
            v = vals[(key == k) & ~np.isnan(vals)]
            cache[k] = np.array([conformal_quantile(v, t) for t in QUANTILES]) if len(v) >= CLIM_MIN_PER_KEY else None
        if cache[k] is None:
            return Result("climatology", None, "not_ready", f"key {k} has < {CLIM_MIN_PER_KEY} obs")
        out[i] = cache[k]
    return Result("climatology", _finalise(out), "ok")


def anomaly_persist(ctx: Context) -> Result:
    """mu(key) + alpha_g + beta_g * (latest anomaly), with empirical residual quantiles per gap g.

    mu = mean of actuals by (weekend, local half-hour) over the trailing 84 d.
    (alpha_g, beta_g) = OLS of a_{t+g} on a_t over the trailing 28 d of anomalies.
    Residual quantiles: conformal-corrected empirical quantiles of the OLS residuals at gap g.
    All inputs end at the last known actual L; nothing after L is touched.
    """
    y = _grid(ctx.history)
    y = y[y.index >= y.index.max() - pd.Timedelta(days=CLIM_WINDOW_DAYS)]
    if y.notna().sum() < CLIM_MIN_DAYS * 48 * 0.8:
        return Result("anomaly_persist", None, "not_ready", "insufficient history")
    L = y.dropna().index.max()
    y = y[:L]
    hh, we = local_keys(y.index)
    key = we * 48 + hh
    mu = pd.Series(y.to_numpy()).groupby(key).mean()
    a = y.to_numpy() - mu.reindex(key).to_numpy()
    a_last = a[-1]
    thh, twe = local_keys(ctx.targets)
    mu_t = mu.reindex(twe * 48 + thh).to_numpy()
    if np.isnan(mu_t).any():
        return Result("anomaly_persist", None, "not_ready", "climatology key missing")
    gaps = ((ctx.targets - L) / STEP).astype(int).to_numpy()
    fit = a[-AP_FIT_DAYS * 48:]
    out = np.full((len(ctx.targets), len(QUANTILES)), np.nan)
    cache: dict[int, tuple[float, float, np.ndarray]] = {}
    for i, g in enumerate(gaps):
        if g not in cache:
            x, z = fit[:-g], fit[g:]
            m = np.isfinite(x) & np.isfinite(z)
            if m.sum() < AP_MIN_PAIRS:
                return Result("anomaly_persist", None, "not_ready", f"gap {g}: {m.sum()} pairs < {AP_MIN_PAIRS}")
            x, z = x[m], z[m]
            xm, zm = x.mean(), z.mean()
            beta = float(((x - xm) * (z - zm)).sum() / max(((x - xm) ** 2).sum(), 1e-12))
            alpha = float(zm - beta * xm)
            res = z - (alpha + beta * x)
            cache[g] = (alpha, beta, np.array([conformal_quantile(res, t) for t in QUANTILES]))
        alpha, beta, rq = cache[g]
        out[i] = mu_t[i] + alpha + beta * a_last + rq
    return Result("anomaly_persist", _finalise(out), "ok")


def _step_group(step: int) -> int:
    for j, (lo, hi) in enumerate(STEP_GROUPS):
        if lo <= step <= hi:
            return j
    raise ValueError(step)


def neso_cqr(ctx: Context) -> Result:
    """NESO's point forecast + rolling empirical residual quantiles (own scored vintages only)."""
    mem = ctx.residual_memory
    if len(mem):
        mem = mem[mem["target_from"] >= ctx.issue_time - pd.Timedelta(days=CQR_WINDOW_DAYS)]
    steps = np.arange(1, len(ctx.targets) + 1)
    base = ctx.neso.reindex(ctx.targets).to_numpy(dtype=float)
    out = np.full((len(ctx.targets), len(QUANTILES)), np.nan)
    pools: dict[int, np.ndarray] = {}
    for j, (lo, hi) in enumerate(STEP_GROUPS):
        r = mem.loc[(mem["step"] >= lo) & (mem["step"] <= hi), "resid"].to_numpy(dtype=float)
        r = r[np.isfinite(r)]
        if len(r) < CQR_MIN_RESID:
            return Result("neso_cqr", None, "not_ready", f"step group {lo}-{hi}: {len(r)} residuals < {CQR_MIN_RESID}")
        pools[j] = np.array([conformal_quantile(r, t) for t in QUANTILES])
    for i, k in enumerate(steps):
        out[i] = base[i] + pools[_step_group(int(k))]
    return Result("neso_cqr", _finalise(out), "ok")


REGISTRY = {"neso_point": neso_point, "neso_cqr": neso_cqr, "anomaly_persist": anomaly_persist, "climatology": climatology}
assert set(REGISTRY) == set(MODELS)


def run_all(ctx: Context) -> list[Result]:
    return [REGISTRY[m](ctx) for m in MODELS]
