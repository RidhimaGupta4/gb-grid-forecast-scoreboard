"""Validation of API frames. Nothing is ever imputed or simulated."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import MAX_PLAUSIBLE, STEP


def validate_intensity(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Deduplicate, sort, null-out implausible values, and report quality counters."""
    q = {"n_raw": int(len(df)), "n_duplicates": 0, "n_implausible": 0, "n_null_actual": 0, "n_gaps": 0}
    if df.empty:
        return df, q
    df = df.sort_values("from_")
    dup = df.duplicated("from_", keep="last")
    q["n_duplicates"] = int(dup.sum())
    df = df[~dup].copy()
    for col in ("actual", "forecast"):
        bad = (df[col] < 0) | (df[col] > MAX_PLAUSIBLE)
        q["n_implausible"] += int(bad.sum())
        df.loc[bad, col] = np.nan
    q["n_null_actual"] = int(df["actual"].isna().sum())
    gaps = df["from_"].diff().dropna()
    q["n_gaps"] = int((gaps > STEP).sum())
    return df.reset_index(drop=True), q
