from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ISSUE_BUFFER, STEP, TZ_LOCAL

API_FMT = "%Y-%m-%dT%H:%MZ"


def utcnow() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def as_utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def next_boundary(t: pd.Timestamp) -> pd.Timestamp:
    """First half-hour boundary strictly after t."""
    return as_utc(t).floor("30min") + STEP


def first_target(issue_time: pd.Timestamp) -> pd.Timestamp:
    """First target period starts at least ISSUE_BUFFER after the issue time."""
    return next_boundary(as_utc(issue_time) + ISSUE_BUFFER)


def api_stamp(t) -> str:
    return as_utc(t).strftime(API_FMT)


def local_keys(idx: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    """(half-hour-of-local-day 0..47, weekend flag) in Europe/London.

    Using local clock time makes the daily shape invariant to BST/GMT changes.
    Bank holidays are NOT modelled (documented limitation).
    """
    loc = idx.tz_convert(TZ_LOCAL)
    hh = (loc.hour * 2 + (loc.minute // 30)).to_numpy()
    weekend = (loc.dayofweek >= 5).astype(int)
    return hh, np.asarray(weekend)
