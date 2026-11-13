"""Readers for the append-only parquet stores."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import paths


def read_parquets(folder: Path) -> pd.DataFrame:
    files = sorted(Path(folder).rglob("*.parquet"))
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def load_forecasts(root) -> pd.DataFrame:
    return read_parquets(paths(root)["forecasts"])


def load_scores(root) -> pd.DataFrame:
    return read_parquets(paths(root)["scores"])
