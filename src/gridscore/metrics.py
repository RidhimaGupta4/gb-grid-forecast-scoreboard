from __future__ import annotations

import numpy as np

from .config import ALPHA90, QCOLS, QUANTILES

TAUS = np.asarray(QUANTILES)


def pinball_matrix(y: np.ndarray, Q: np.ndarray) -> np.ndarray:
    e = y[:, None] - Q
    return np.maximum(TAUS * e, (TAUS - 1.0) * e)


def row_metrics(y: np.ndarray, Q: np.ndarray, is_point: bool) -> dict[str, np.ndarray]:
    """Per-row scores. Interval metrics are NaN for point forecasts (they have no interval)."""
    y = np.asarray(y, float)
    Q = np.asarray(Q, float)
    pb = pinball_matrix(y, Q)
    iq = {c: i for i, c in enumerate(QCOLS)}
    out = {
        "pinball_mean": pb.mean(axis=1),
        "ae_med": np.abs(y - Q[:, iq["q50"]]),
    }
    for c, i in iq.items():
        out[f"hit_{c}"] = (y <= Q[:, i]).astype(float)
    nan = np.full(len(y), np.nan)
    if is_point:
        out.update(cov90=nan, cov50=nan, w90=nan, w50=nan, winkler90=nan)
    else:
        lo90, hi90, lo50, hi50 = Q[:, iq["q05"]], Q[:, iq["q95"]], Q[:, iq["q25"]], Q[:, iq["q75"]]
        out["cov90"] = ((y >= lo90) & (y <= hi90)).astype(float)
        out["cov50"] = ((y >= lo50) & (y <= hi50)).astype(float)
        out["w90"] = hi90 - lo90
        out["w50"] = hi50 - lo50
        out["winkler90"] = (hi90 - lo90) + (2 / ALPHA90) * (np.maximum(lo90 - y, 0) + np.maximum(y - hi90, 0))
    return out
