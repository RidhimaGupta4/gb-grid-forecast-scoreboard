"""TEST FIXTURE ONLY — SYNTHETIC DATA.

This module exists solely so unit/integration tests can exercise the code paths offline.
It is never imported by the `gridscore` package, its URLs are `https://fixture.invalid/...`,
and tests/test_provenance.py fails CI if any fixture-provenance record appears under data/.
The scoreboard itself uses ONLY real data fetched from api.carbonintensity.org.uk.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from gridscore.api import Fetch
from gridscore.config import STEP


class World:
    def __init__(self, seed: int = 0, start: str = "2026-01-01", days: int = 160):
        idx = pd.date_range(start, periods=days * 48, freq="30min", tz="UTC")
        rng = np.random.default_rng(seed)
        t = np.arange(len(idx))
        ar = np.zeros(len(t))
        e = rng.normal(0, 12, len(t))
        for i in range(1, len(t)):
            ar[i] = 0.97 * ar[i - 1] + e[i]
        v = 180 + 60 * np.sin(2 * np.pi * (t % 48) / 48 - 1.0) + 15 * np.sin(2 * np.pi * t / (48 * 7)) + ar
        self.actual = pd.Series(np.clip(v, 5, 600), idx)

    def perturbed_after(self, cutoff: pd.Timestamp, bump: float = 500.0) -> "World":
        w = World.__new__(World)
        a = self.actual.copy()
        a[a.index >= cutoff] += bump
        w.actual = a
        return w


class FakeAPI:
    """Mimics gridscore.api.CarbonAPI. `base` supplies the NESO forecast so that two worlds that
    differ only in the future can be compared with identical NESO snapshots."""

    def __init__(self, world: World, base: World | None = None, now: pd.Timestamp | None = None, leak_bait_days: int = 3):
        self.world, self.base, self.now, self.leak_bait_days = world, base or world, now, leak_bait_days

    def _fetch(self, url: str, payload) -> Fetch:
        raw = json.dumps(payload, default=str).encode()
        return Fetch(url, 200, (self.now or pd.Timestamp.now(tz="UTC")).isoformat(), hashlib.sha256(raw).hexdigest(), len(raw), raw)

    def fw48h(self, start):
        idx = pd.date_range(start, periods=96, freq="30min", tz="UTC")
        rows = []
        for k, t in enumerate(idx):
            rng = np.random.default_rng(int(t.timestamp()) % (2**31) + int(start.timestamp()) % 9973)
            fc = float(self.base.actual.get(t, np.nan) + rng.normal(0, 8 + 2.5 * np.sqrt(k + 1)))
            # `actual` deliberately populated (leak bait): the engine must ignore it.
            rows.append((t, t + STEP, fc, float(self.world.actual.get(t, np.nan)), "moderate"))
        df = pd.DataFrame(rows, columns=["from_", "to_", "forecast", "actual", "index"])
        return df, self._fetch("https://fixture.invalid/fw48h", {"n": len(df)})

    def range(self, start, end):
        end_bait = end + pd.Timedelta(days=self.leak_bait_days)  # misbehaving API returns extra future rows
        a = self.world.actual
        s = a[(a.index >= start) & (a.index < end_bait)]
        df = pd.DataFrame({"from_": s.index, "to_": s.index + STEP, "forecast": np.nan, "actual": s.to_numpy(), "index": "moderate"})
        return df, [self._fetch("https://fixture.invalid/range", {"n": len(df)})]
