"""Inference helpers. Unit of analysis = target day (UTC) to respect strong within-day dependence."""
from __future__ import annotations

import zlib

import numpy as np
from scipy import stats as sps


def seed_for(base: int, label: str) -> int:
    return (base + zlib.crc32(label.encode())) % (2**32)


def hac_t_test(d: np.ndarray, lag: int | None = None) -> dict:
    """Diebold-Mariano-style test of E[d]=0 with Newey-West (Bartlett) variance, t(n-1) reference.

    d = daily mean loss differential (challenger - benchmark). Small-n caveat: with ~30-60 days
    the HAC variance is itself noisy, so the block-bootstrap CI is reported alongside.
    """
    d = np.asarray(d, float)
    d = d[np.isfinite(d)]
    n = len(d)
    if n < 3:
        return {"n": n, "mean": float(d.mean()) if n else float("nan"), "t": float("nan"), "p": float("nan")}
    if lag is None:
        lag = int(np.floor(n ** (1 / 3)))
    u = d - d.mean()
    var = (u @ u) / n
    for l in range(1, lag + 1):
        var += 2 * (1 - l / (lag + 1)) * (u[l:] @ u[:-l]) / n
    var = max(var, 1e-12)
    t = d.mean() / np.sqrt(var / n)
    return {"n": n, "mean": float(d.mean()), "t": float(t), "p": float(2 * sps.t.sf(abs(t), df=n - 1)), "lag": lag}


def block_bootstrap_ratio(num: np.ndarray, den: np.ndarray, block: int, B: int, seed: int, level: float = 0.95):
    """Moving-block bootstrap of sum(num)/sum(den) over an ordered daily series. Returns (est, lo, hi)."""
    num = np.asarray(num, float)
    den = np.asarray(den, float)
    n = len(num)
    if n == 0 or den.sum() == 0:
        return float("nan"), float("nan"), float("nan")
    est = float(num.sum() / den.sum())
    if n < 3:
        return est, float("nan"), float("nan")
    block = max(1, min(block, n))
    nb = int(np.ceil(n / block))
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n - block + 1, size=(B, nb))
    idx = (starts[:, :, None] + np.arange(block)).reshape(B, -1)[:, :n]
    r = num[idx].sum(1) / den[idx].sum(1)
    a = (1 - level) / 2
    lo, hi = np.nanpercentile(r, [100 * a, 100 * (1 - a)])
    return est, float(lo), float(hi)


def holm(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(((k, p) for k, p in pvals.items() if np.isfinite(p)), key=lambda kv: kv[1])
    m, running, adj = len(items), 0.0, {}
    for i, (k, p) in enumerate(items):
        running = max(running, (m - i) * p)
        adj[k] = min(1.0, running)
    for k, p in pvals.items():
        adj.setdefault(k, float("nan"))
    return adj
