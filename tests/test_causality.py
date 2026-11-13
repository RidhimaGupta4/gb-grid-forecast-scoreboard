"""The core leakage tests: forecasts must be a function of past information only."""
import pandas as pd
import pytest

from fakeapi import FakeAPI, World
from gridscore.config import AVAIL_LAG, QCOLS
from gridscore.issue import CausalityError, run_issue
from gridscore.store import load_forecasts

ISSUE = pd.Timestamp("2026-04-20 12:17", tz="UTC")


class Clock:
    def __init__(self, t):
        self.t, self.n = t, 0

    def __call__(self):
        self.n += 1
        return self.t if self.n == 1 else self.t + pd.Timedelta(minutes=1)


def test_future_actuals_cannot_change_any_forecast(tmp_path):
    A = World(seed=11)
    cutoff = ISSUE - AVAIL_LAG
    B = A.perturbed_after(cutoff)          # identical past, wildly different future
    a_root, b_root = tmp_path / "a", tmp_path / "b"
    run_issue(a_root, FakeAPI(A, now=ISSUE), Clock(ISSUE))
    run_issue(b_root, FakeAPI(B, base=A, now=ISSUE), Clock(ISSUE))
    fa, fb = load_forecasts(a_root), load_forecasts(b_root)
    assert len(fa) and len(fa) == len(fb)
    key = ["model_id", "step"]
    fa, fb = fa.sort_values(key).reset_index(drop=True), fb.sort_values(key).reset_index(drop=True)
    pd.testing.assert_frame_equal(fa[key + list(QCOLS)], fb[key + list(QCOLS)], check_exact=True)


def test_all_targets_strictly_after_issue_with_buffer(tmp_path):
    run_issue(tmp_path, FakeAPI(World(seed=1), now=ISSUE), Clock(ISSUE))
    f = load_forecasts(tmp_path)
    assert (f["target_from"] >= f["issued_at"] + pd.Timedelta(minutes=10)).all()
    assert f["step"].min() == 1 and f["step"].max() == 96


def test_cold_start_issues_only_neso_and_records_why(tmp_path):
    w = World(seed=2, start="2026-04-01", days=60)  # only ~19 days of history before ISSUE
    r = run_issue(tmp_path, FakeAPI(w, now=ISSUE), Clock(ISSUE))
    f = load_forecasts(tmp_path)
    assert set(f["model_id"]) == {"neso_point"}
    assert {"climatology", "anomaly_persist", "neso_cqr"} <= set(r["skipped"])


def test_slow_run_aborts_instead_of_issuing(tmp_path):
    class Slow:
        def __init__(self):
            self.n = 0

        def __call__(self):
            self.n += 1
            return ISSUE if self.n == 1 else ISSUE + pd.Timedelta(minutes=45)

    with pytest.raises(CausalityError):
        run_issue(tmp_path, FakeAPI(World(seed=3), now=ISSUE), Slow())
