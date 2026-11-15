"""End-to-end run on a SYNTHETIC fixture world (tests only; never touches the repo's data/)."""
import json

import numpy as np
import pandas as pd
import pytest

from fakeapi import FakeAPI, World
from gridscore import ledger
from gridscore.config import BENCHMARK, paths
from gridscore.issue import run_issue
from gridscore.report import build_summary
from gridscore.score import run_score
from gridscore.store import load_forecasts, load_scores

START = pd.Timestamp("2026-04-12", tz="UTC")   # world starts 2026-01-01 -> ~101 d of history
N_DAYS = 44


class Clk:
    def __init__(self, t):
        self.t, self.n = t, 0

    def __call__(self):
        self.n += 1
        return self.t if self.n == 1 else self.t + pd.Timedelta(minutes=1)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e")
    w = World(seed=5, days=170)
    for d in range(N_DAYS):
        day = START + pd.Timedelta(days=d)
        for h in (0, 6, 12, 18):
            t = day + pd.Timedelta(hours=h, minutes=17)
            run_issue(root, FakeAPI(w, now=t), Clk(t))
        t = day + pd.Timedelta(hours=23, minutes=50)
        run_score(root, FakeAPI(w, now=t), clock=lambda t=t: t)
    return root


def test_ledger_chain_verifies_and_detects_tampering(run, tmp_path):
    P = paths(run)
    assert ledger.verify(run, P["ledger"])["ok"]
    f = sorted(P["forecasts"].rglob("*.parquet"))[3]
    orig = f.read_bytes()
    df = pd.read_parquet(f)
    df.loc[df.index[0], "q50"] += 1.0
    df.to_parquet(f, index=False)
    res = ledger.verify(run, P["ledger"])
    f.write_bytes(orig)
    assert not res["ok"] and any("hash mismatch" in p for p in res["problems"])
    assert ledger.verify(run, P["ledger"])["ok"]


def test_scores_only_for_matured_targets_and_no_duplicates(run):
    sc, fc = load_scores(run), load_forecasts(run)
    assert len(sc) and not sc.duplicated(["issue_id", "model_id", "step"]).any()
    assert (sc["target_to"] <= sc["scored_at"] - pd.Timedelta(hours=24)).all()
    assert (sc["actual_fetched_at"] >= sc["target_to"] + pd.Timedelta(hours=24)).all()
    assert len(sc) < len(fc)  # the most recent day cannot be scored yet


def test_residual_memory_is_causal(run):
    """Every neso_cqr forecast may only use residuals whose actual was fetched before its issue time."""
    fc, sc = load_forecasts(run), load_scores(run)
    assert "neso_cqr" in set(fc["model_id"]), "neso_cqr should have warmed up in this run"
    first = fc[fc["model_id"] == "neso_cqr"]["issued_at"].min()
    earliest_usable = sc[sc["model_id"] == BENCHMARK]["actual_fetched_at"].min()
    assert first > earliest_usable


def test_neso_point_is_degenerate_and_models_never_cross(run):
    fc = load_forecasts(run)
    q = fc[["q05", "q10", "q25", "q50", "q75", "q90", "q95"]].to_numpy()
    assert (np.diff(q, axis=1) >= -1e-9).all() and (q >= 0).all()
    n = fc[fc["model_id"] == BENCHMARK][["q05", "q95"]]
    assert (n["q05"] == n["q95"]).all()


def test_summary_is_honest_about_evidence(run):
    s = build_summary(run)
    assert s["verification"]["ledger"]["ok"] is True
    assert s["counts"]["days_scored"] >= 20
    assert s["counts"]["unverified_rows"] == s["counts"]["scored_rows"]  # no receipts were collected here
    for rec in s["paired"]:
        if rec["pinball"]["n_days"] < s["meta"]["min_days_for_claim"]:
            assert rec["verdict"] == "insufficient"
    fam = [r for r in s["paired"] if r["in_family"]]
    assert all(r["p_holm"] is None or r["p_holm"] >= r["pinball"]["p"] - 1e-12 for r in fam if r["pinball"]["p"] is not None)
    json.dumps(s)  # fully serialisable, no NaN tokens
    assert "NaN" not in json.dumps(s)


def test_synthetic_calibration_is_sane(run):
    """On the fixture world the interval models should be roughly calibrated (method sanity, not a result)."""
    s = build_summary(run)
    for r in s["leaderboard"]:
        if r["bucket"] == "all" and r["comparable"] and r["model"] in ("climatology", "anomaly_persist") and r["cov90"]["est"] is not None:
            assert 0.80 <= r["cov90"]["est"] <= 0.97, r


def test_push_receipt_violation_is_excluded(run, tmp_path):
    import shutil
    root = tmp_path / "copy"
    shutil.copytree(run, root)
    fc = load_forecasts(root)
    iid = sorted(fc["issue_id"].unique())[0]
    # a receipt claiming the push happened AFTER the targets began -> must be excluded when (re)scored
    sc_before = load_scores(root)
    from gridscore.score import prereg_status
    t = sc_before[sc_before["issue_id"] == iid]["target_from"]
    late = {iid: {"push_created_at": (t.max() + pd.Timedelta(hours=1)).isoformat()}}
    st = prereg_status(iid, t, late)
    assert (st == "violated").any() and (st == "verified").sum() == (t > pd.Timestamp(late[iid]["push_created_at"])).sum()


def test_site_builds_with_no_data_and_with_data(run, tmp_path):
    from gridscore.site import build_site
    empty = tmp_path / "empty"
    (empty / "data").mkdir(parents=True)
    p = build_site(empty)
    assert "Warming up" in p.read_text() or '"state":"warming_up"' in p.read_text()
    p2 = build_site(run)
    html = p2.read_text()
    assert "__SUMMARY_JSON__" not in html and '"claims_enabled"' in html or '"insufficient_evidence"' in html
    assert (p2.parent / "plotly.min.js").stat().st_size > 1_000_000


def test_scoring_with_receipts_present_regression(tmp_path):
    """Regression: a tz-aware receipt timestamp used to crash scoring (tz-naive NaT column) on the first live run."""
    root = tmp_path / "rcpt"
    w = World(seed=5, days=170)
    for d in range(12):
        day = START + pd.Timedelta(days=d)
        for h in (0, 6, 12, 18):
            t = day + pd.Timedelta(hours=h, minutes=17)
            run_issue(root, FakeAPI(w, now=t), Clk(t))
    fc = load_forecasts(root)
    iids = sorted(fc["issue_id"].unique())
    P = paths(root)
    with open(P["receipts"], "w") as f:
        for iid in iids[:-1]:
            t0 = fc[fc["issue_id"] == iid]["target_from"].min()
            f.write(json.dumps({"issue_id": iid, "push_created_at": (t0 - pd.Timedelta(minutes=5)).isoformat().replace("+00:00", "Z")}) + "\n")
    now = START + pd.Timedelta(days=12, hours=23)
    res = run_score(root, FakeAPI(w, now=now), clock=lambda: now)
    assert res["scored"] > 0 and res["violated"] == 0
    sc = load_scores(root)
    assert str(sc["receipt_pushed_at"].dt.tz) == "UTC"
    assert (sc["prereg_status"] == "verified").any()
