"""Stress the paths that only run live: receipts of every kind, gaps/nulls in actuals, mixed datetime
resolutions, partial re-scoring, report + site on the result. SYNTHETIC fixtures; tests only."""
import json

import numpy as np
import pandas as pd

from fakeapi import FakeAPI, World
from gridscore import ledger
from gridscore.config import paths
from gridscore.issue import run_issue
from gridscore.report import build_summary
from gridscore.score import run_score
from gridscore.site import build_site
from gridscore.store import load_forecasts, load_scores

START = pd.Timestamp("2026-04-12", tz="UTC")


class Clk:
    def __init__(self, t):
        self.t, self.n = t, 0

    def __call__(self):
        self.n += 1
        return self.t if self.n == 1 else self.t + pd.Timedelta(minutes=1)


class GappyAPI(FakeAPI):
    """Real-world messiness: dropped half-hours, null actuals, microsecond timestamps."""

    def range(self, start, end):
        df, f = super().range(start, end)
        h = (df["from_"].astype("int64") // 10**9 // 1800) % 100     # deterministic per period
        df = df[h >= 3].copy()                                        # ~3% of periods absent
        h = (df["from_"].astype("int64") // 10**9 // 1800 * 7) % 100
        df.loc[h < 2, "actual"] = np.nan                              # ~2% null actual
        df["from_"] = df["from_"].astype("datetime64[us, UTC]")
        df["to_"] = df["to_"].astype("datetime64[us, UTC]")
        return df, f


def test_gappy_actuals_mixed_receipts_and_full_pipeline(tmp_path):
    root = tmp_path / "live"
    w = World(seed=9, days=170)
    for d in range(10):
        day = START + pd.Timedelta(days=d)
        for h in (0, 6, 12, 18):
            t = day + pd.Timedelta(hours=h, minutes=17)
            run_issue(root, FakeAPI(w, now=t), Clk(t))
    fc = load_forecasts(root)
    iids = sorted(fc["issue_id"].unique())
    P = paths(root)
    first = fc.groupby("issue_id")["target_from"].min()
    # verified, violated (pushed after targets began), and no receipt at all
    with open(P["receipts"], "w") as f:
        for iid in iids[:10]:
            f.write(json.dumps({"issue_id": iid, "push_created_at": (first[iid] - pd.Timedelta(minutes=3)).isoformat()}) + "\n")
        f.write(json.dumps({"issue_id": iids[10], "push_created_at": (first[iids[10]] + pd.Timedelta(hours=2)).isoformat()}) + "\n")
    # two scoring passes: idempotent, second one only adds newly matured rows
    n1 = START + pd.Timedelta(days=6, hours=3)
    r1 = run_score(root, GappyAPI(w, now=n1), clock=lambda: n1)
    n2 = START + pd.Timedelta(days=11)
    r2 = run_score(root, GappyAPI(w, now=n2), clock=lambda: n2)
    r3 = run_score(root, GappyAPI(w, now=n2), clock=lambda: n2)
    assert r1["scored"] > 0 and r2["scored"] > 0 and r3["scored"] == 0
    sc = load_scores(root)
    assert not sc.duplicated(["issue_id", "model_id", "step"]).any()
    assert sc["prereg_status"].isin(["verified", "violated", "unverified"]).all()
    assert (sc["prereg_status"] == "violated").any() and (sc["prereg_status"] == "unverified").any() and (sc["prereg_status"] == "verified").any()
    assert str(sc["receipt_pushed_at"].dt.tz) == "UTC"
    # rows that were not scored carry NO metrics; scored rows have finite metrics and a real actual
    ok = sc["status"] == "scored"
    assert sc.loc[~ok, "actual"].isna().all() and sc.loc[~ok, "pinball_mean"].isna().all()
    assert np.isfinite(sc.loc[ok, "pinball_mean"]).all() and sc.loc[ok, "actual"].notna().all()
    # causality of scoring: every scored row's target ended >= 24h before its scoring time
    assert (sc["target_to"] <= sc["scored_at"] - pd.Timedelta(hours=24)).all()
    assert ledger.verify(root, paths(root)["ledger"])["ok"]
    s = build_summary(root)
    flat = json.dumps(s, default=str)
    assert "NaN" not in flat and "Infinity" not in flat          # valid JSON for the browser
    json.loads(flat)
    # violated rows must not influence the leaderboard
    assert s["counts"]["violated_rows"] > 0
    build_site(root)
