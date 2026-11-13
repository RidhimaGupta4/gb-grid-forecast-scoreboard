"""Thin, polite client for the Carbon Intensity API (national endpoints only).

Every call returns the parsed frame AND a provenance record (URL, status, UTC fetch
time, SHA-256 of the raw body) that is stored in the issue manifest.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

import pandas as pd
import requests

from .config import API_BASE, USER_AGENT
from .timeutil import api_stamp, as_utc, utcnow

CHUNK_DAYS = 13  # API allows <= 14 days per range request; stay below it


class ApiError(RuntimeError):
    pass


@dataclass
class Fetch:
    url: str
    status: int
    fetched_at: str
    sha256: str
    n_bytes: int
    raw: bytes = field(repr=False, default=b"")

    def record(self) -> dict:
        return {k: getattr(self, k) for k in ("url", "status", "fetched_at", "sha256", "n_bytes")}


def parse_intensity(payload: dict) -> pd.DataFrame:
    rows = []
    for item in payload.get("data", []):
        inten = item.get("intensity") or {}
        rows.append((item["from"], item["to"], inten.get("forecast"), inten.get("actual"), inten.get("index")))
    df = pd.DataFrame(rows, columns=["from_", "to_", "forecast", "actual", "index"])
    if df.empty:
        return df.astype({"forecast": "float64", "actual": "float64"})
    fmt = "%Y-%m-%dT%H:%MZ"
    df["from_"] = pd.to_datetime(df["from_"], format=fmt, utc=True)
    df["to_"] = pd.to_datetime(df["to_"], format=fmt, utc=True)
    df["forecast"] = pd.to_numeric(df["forecast"], errors="coerce")
    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    return df


class CarbonAPI:
    def __init__(self, session: requests.Session | None = None, retries: int = 5, pause: float = 0.6, timeout: int = 40):
        self.s = session or requests.Session()
        self.s.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
        self.retries, self.pause, self.timeout = retries, pause, timeout

    def _get(self, path: str) -> tuple[dict, Fetch]:
        url = API_BASE + path
        last = None
        for attempt in range(self.retries):
            try:
                r = self.s.get(url, timeout=self.timeout)
                if r.status_code == 200:
                    f = Fetch(url, 200, utcnow().isoformat(), hashlib.sha256(r.content).hexdigest(), len(r.content), r.content)
                    time.sleep(self.pause)  # be polite
                    return r.json(), f
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {r.status_code}"
                else:
                    raise ApiError(f"{url} -> HTTP {r.status_code}")
            except requests.RequestException as e:  # network / timeout
                last = repr(e)
            time.sleep(min(60, 2 ** attempt * 2))
        raise ApiError(f"{url} failed after {self.retries} attempts: {last}")

    def fw48h(self, start: pd.Timestamp) -> tuple[pd.DataFrame, Fetch]:
        payload, f = self._get(f"/intensity/{api_stamp(start)}/fw48h")
        return parse_intensity(payload), f

    def range(self, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, list[Fetch]]:
        start, end = as_utc(start), as_utc(end)
        frames, fetches, cur = [], [], start
        while cur < end:
            nxt = min(cur + pd.Timedelta(days=CHUNK_DAYS), end)
            payload, f = self._get(f"/intensity/{api_stamp(cur)}/{api_stamp(nxt)}")
            frames.append(parse_intensity(payload))
            fetches.append(f)
            cur = nxt
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return df, fetches
