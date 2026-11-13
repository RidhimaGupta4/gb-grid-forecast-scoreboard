"""Frozen protocol constants.

Everything in this file is part of the pre-registration (see PROTOCOL.md).
Changing any value that affects forecasts or scoring REQUIRES bumping
PROTOCOL_VERSION and the affected model versions; old results are never
rewritten or re-scored under new settings.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

PROTOCOL_VERSION = "1.0.0"

# ---- data source -----------------------------------------------------------
API_BASE = "https://api.carbonintensity.org.uk"
SOURCE_TAG = "carbonintensity.org.uk"  # provenance tag stored on every row
USER_AGENT = "gridscore/1.0 (research scoreboard; https://github.com/RidhimaGupta4/gb-grid-forecast-scoreboard)"

# ---- time grid -------------------------------------------------------------
STEP = pd.Timedelta(minutes=30)
N_STEPS = 96  # 48 h of half-hour settlement periods
ISSUE_BUFFER = pd.Timedelta(minutes=10)  # first target starts >= 10 min after issue
AVAIL_LAG = pd.Timedelta(hours=1)  # an actual is "known" only 1 h after its period ends
SCORE_LAG = pd.Timedelta(hours=24)  # first scoring attempt: >= 24 h after period end
MISSING_GRACE = pd.Timedelta(days=7)  # then marked missing_actual permanently
TZ_LOCAL = "Europe/London"

# ---- probabilistic output --------------------------------------------------
QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
QCOLS = tuple(f"q{int(round(t * 100)):02d}" for t in QUANTILES)  # q05 ... q95
ALPHA90 = 0.10  # central 90 % interval = [q05, q95]
ALPHA50 = 0.50  # central 50 % interval = [q25, q75]

# ---- horizon structure (pre-registered) ------------------------------------
# step k = k-th half-hour period starting >= ISSUE_BUFFER after issue (1..96)
BUCKETS = {"h00_06": (1, 12), "h06_24": (13, 48), "h24_48": (49, 96)}
BUCKET_LABEL = {"h00_06": "0-6 h", "h06_24": "6-24 h", "h24_48": "24-48 h", "all": "0-48 h"}
STEP_GROUPS = ((1, 4), (5, 12), (13, 24), (25, 48), (49, 96))  # neso_cqr residual pools


def bucket_of(step: int) -> str:
    for name, (lo, hi) in BUCKETS.items():
        if lo <= step <= hi:
            return name
    raise ValueError(step)


# ---- models (id -> version). Any change to a model => new id/version. -------
MODELS = {
    "neso_point": "1",         # NESO's own published forecast, snapshotted at issue time
    "neso_cqr": "1",           # NESO point + rolling split-conformal-style residual quantiles
    "anomaly_persist": "1",    # climatological mean + decaying anomaly persistence + empirical residuals
    "climatology": "1",        # empirical quantiles by (day-type, local half-hour), trailing 12 weeks
}
CHALLENGERS = ("neso_cqr", "anomaly_persist", "climatology")
BENCHMARK = "neso_point"

# ---- model hyper-parameters (frozen) ---------------------------------------
CLIM_WINDOW_DAYS = 84
CLIM_MIN_DAYS = 42
CLIM_MIN_PER_KEY = 8
AP_FIT_DAYS = 28
AP_MIN_PAIRS = 500
CQR_WINDOW_DAYS = 60
CQR_MIN_RESID = 200

# ---- data quality ----------------------------------------------------------
MAX_PLAUSIBLE = 1000.0  # gCO2/kWh; GB values are ~0-500 in practice
HISTORY_FETCH_DAYS = CLIM_WINDOW_DAYS + 2

# ---- statistics (pre-registered) -------------------------------------------
MIN_DAYS_FOR_CLAIM = 28   # below this the dashboard says "insufficient evidence"
MIN_ROWS_PER_DAY = 12     # a (model, bucket, day) cell needs this many paired rows
BOOT_BLOCK_DAYS = 7
BOOT_B = 2000
BOOT_SEED = 20261113
SIGNIFICANCE = 0.05

# ---- paths -----------------------------------------------------------------
def paths(root: Path | str) -> dict[str, Path]:
    root = Path(root)
    d = root / "data"
    return {
        "root": root,
        "data": d,
        "forecasts": d / "forecasts",
        "scores": d / "scores",
        "manifests": d / "manifests",
        "raw": d / "raw",
        "ledger": d / "ledger.jsonl",
        "receipts": d / "receipts.jsonl",
        "summary": root / "site_build" / "summary.json",
        "site": root / "site_build",
    }
