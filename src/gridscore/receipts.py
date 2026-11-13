"""Server-side push receipts.

A commit's author/committer date is client-controlled and therefore weak evidence. GitHub's
Events API stamps each PushEvent with a *server-side* `created_at`. For every issue batch we
look up the commit that added its forecast file and the PushEvent that carried that commit,
and store the receipt. Scoring then marks a forecast `violated` (and excludes it) if the push
was not provably earlier than the start of its target period.

Limits: the Events API only keeps ~90 days / 300 events, so receipts must be collected
regularly (the daily score workflow does this). A missing receipt => `unverified`, NOT `verified`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd
import requests

from .config import paths
from .store import load_forecasts


def _commit_for_file(root: Path, rel: str) -> str | None:
    try:
        out = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "-n", "1", "--", rel],
                             cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        return out or None
    except Exception:
        return None


def read_receipts(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["issue_id"]] = r
    return out


def collect(root, repo: str, token: str | None = None, session: requests.Session | None = None, max_pages: int = 3) -> dict:
    root = Path(root)
    P = paths(root)
    have = read_receipts(P["receipts"])
    fc = load_forecasts(root)
    if fc.empty:
        return {"new": 0, "pending": 0}
    issues = sorted(fc["issue_id"].unique())
    pending = [i for i in issues if i not in have]
    if not pending:
        return {"new": 0, "pending": 0}
    s = session or requests.Session()
    hdr = {"Accept": "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    pushes: dict[str, str] = {}  # commit sha -> server push time
    for page in range(1, max_pages + 1):
        r = s.get(f"https://api.github.com/repos/{repo}/events", params={"per_page": 100, "page": page}, headers=hdr, timeout=30)
        if r.status_code != 200:
            break
        ev = r.json()
        if not ev:
            break
        for e in ev:
            if e.get("type") != "PushEvent":
                continue
            pl = e.get("payload", {})
            for sha in [pl.get("head")] + [c.get("sha") for c in pl.get("commits", [])]:
                if sha:
                    pushes.setdefault(sha, e["created_at"])
    new = []
    for iid in pending:
        f = fc.loc[fc["issue_id"] == iid].iloc[0]
        ym = pd.Timestamp(f["issued_at"]).strftime("%Y-%m")
        sha = _commit_for_file(root, f"data/forecasts/{ym}/{iid}.parquet")
        if sha and sha in pushes:
            new.append({"issue_id": iid, "commit_sha": sha, "push_created_at": pushes[sha], "source": "github_events_api"})
    if new:
        with open(P["receipts"], "a") as fh:
            for r in new:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
    return {"new": len(new), "pending": len(pending) - len(new)}
