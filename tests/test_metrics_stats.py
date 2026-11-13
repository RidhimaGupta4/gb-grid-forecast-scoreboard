import numpy as np

from gridscore.config import QCOLS
from gridscore.metrics import row_metrics
from gridscore.models import conformal_quantile
from gridscore.stats import block_bootstrap_ratio, hac_t_test, holm


def test_point_forecast_pinball_is_half_mae():
    y = np.array([100.0, 150.0, 90.0])
    pt = np.array([110.0, 140.0, 90.0])
    Q = np.repeat(pt[:, None], 7, axis=1)
    m = row_metrics(y, Q, is_point=True)
    np.testing.assert_allclose(m["pinball_mean"], 0.5 * np.abs(y - pt))
    assert np.isnan(m["cov90"]).all()  # a point forecast has no interval


def test_interval_metrics_by_hand():
    Q = np.array([[10, 20, 30, 40, 50, 60, 70.0]])
    y = np.array([80.0])  # above q95=70
    m = row_metrics(y, Q, is_point=False)
    assert m["cov90"][0] == 0 and m["cov50"][0] == 0
    assert m["w90"][0] == 60 and m["w50"][0] == 20
    assert m["winkler90"][0] == 60 + (2 / 0.1) * 10  # width + 20 * overshoot
    assert m["hit_q95"][0] == 0 and m["hit_q05"][0] == 0
    pb = [max(t * (80 - q), (t - 1) * (80 - q)) for t, q in zip((0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95), Q[0])]
    np.testing.assert_allclose(m["pinball_mean"][0], np.mean(pb))
    assert set(f"hit_{c}" for c in QCOLS) <= set(m)


def test_conformal_interval_coverage_iid():
    rng = np.random.default_rng(1)
    hits = []
    for _ in range(4000):
        cal = rng.normal(size=100)
        lo, hi = conformal_quantile(cal, 0.05), conformal_quantile(cal, 0.95)
        hits.append(lo <= rng.normal() <= hi)
    cov = np.mean(hits)
    assert 0.895 <= cov <= 0.95, cov  # finite-sample-valid (>= 0.90) and not wildly conservative


def test_hac_test_size_under_null():
    rng = np.random.default_rng(2)
    rej = np.mean([hac_t_test(rng.normal(size=60))["p"] < 0.05 for _ in range(1500)])
    assert 0.02 <= rej <= 0.09, rej


def test_bootstrap_ci_covers_truth_and_is_deterministic():
    rng = np.random.default_rng(3)
    cover = 0
    for i in range(200):
        x = rng.normal(1.0, 1.0, 60)
        est, lo, hi = block_bootstrap_ratio(x, np.ones(60), 7, 300, seed=i)
        cover += lo <= 1.0 <= hi
    assert cover / 200 > 0.88
    a = block_bootstrap_ratio(x, np.ones(60), 7, 300, seed=5)
    assert a == block_bootstrap_ratio(x, np.ones(60), 7, 300, seed=5)


def test_holm_matches_reference():
    adj = holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert np.isclose(adj["a"], 0.03) and np.isclose(adj["c"], 0.06) and np.isclose(adj["b"], 0.06)
