"""Aggregate the append-only stores into the JSON that drives the dashboard.

Conventions
-----------
* delta = challenger - benchmark (NESO point forecast) on mean pinball; NEGATIVE = challenger better.
* Unit of inference = target day (UTC). Daily series -> HAC (Newey-West) t-test and moving-block bootstrap.
* Two pre-registered families for multiplicity control (Holm), each 3 challengers x 3 horizon buckets = 9 tests:
  (A) probabilistic score = mean pinball, (B) point accuracy = absolute error of the median forecast.
  NESO publishes a point forecast only, so (A) alone flatters any model that outputs intervals; (B) is the
  fair test of "can anyone beat NESO's central forecast".
  The "all horizons" row and every stratified cut are descriptive/exploratory and NOT multiplicity-adjusted.
* Nothing below the MIN_DAYS_FOR_CLAIM threshold is labelled better/worse.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import ledger as led
from .config import (BENCHMARK, BOOT_B, BOOT_BLOCK_DAYS, BOOT_SEED, BUCKET_LABEL, BUCKETS, CHALLENGERS, MIN_DAYS_FOR_CLAIM,
                     MIN_ROWS_PER_DAY, MODELS, PROTOCOL_VERSION, QCOLS, QUANTILES, SIGNIFICANCE, TZ_LOCAL, bucket_of, paths)
from .receipts import read_receipts
from .stats import block_bootstrap_ratio, hac_t_test, holm, seed_for
from .store import load_forecasts, load_scores
from .timeutil import utcnow

MODEL_INFO = {
    "neso_point": {"label": "NESO forecast", "role": "benchmark",
                   "desc": "NESO's own published point forecast, snapshotted at issue time. Treated as a degenerate distribution (all quantiles equal)."},
    "neso_cqr": {"label": "NESO + conformal bands", "role": "challenger",
                 "desc": "NESO's point forecast plus rolling, finite-sample-corrected empirical residual quantiles from my own earlier scored snapshots."},
    "anomaly_persist": {"label": "Anomaly persistence", "role": "challenger",
                        "desc": "Local-time climatology + decaying persistence of the latest known anomaly; empirical residual quantiles by lead gap."},
    "climatology": {"label": "Climatology", "role": "challenger",
                    "desc": "Empirical quantiles by (weekday/weekend, local half-hour) over the trailing 12 weeks. No weather, no persistence."},
}
LONDON_BLOCKS = [(0, 6, "night 00-06"), (6, 12, "morning 06-12"), (12, 18, "afternoon 12-18"), (18, 24, "evening 18-24")]


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if (math.isnan(o) or math.isinf(o)) else round(float(o), 4)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    return o


def _prep(S: pd.DataFrame) -> pd.DataFrame:
    S = S.copy()
    S["bucket"] = S["step"].map(bucket_of)
    S["target_date"] = S["target_from"].dt.tz_convert("UTC").dt.date
    loc = S["target_from"].dt.tz_convert(TZ_LOCAL)
    S["local_hour"] = loc.dt.hour
    S["weekend"] = loc.dt.dayofweek >= 5
    return S


def _daily(df: pd.DataFrame, col: str, min_rows: int = MIN_ROWS_PER_DAY) -> pd.DataFrame:
    g = df.groupby("target_date")[col].agg(["sum", "count"])
    g = g[g["count"] >= min_rows].sort_index()
    return g


def _paired(S: pd.DataFrame, model: str) -> pd.DataFrame:
    a = S[S["model_id"] == BENCHMARK][["issue_id", "step", "target_date", "bucket", "pinball_mean", "ae_med"]]
    b = S[S["model_id"] == model][["issue_id", "step", "pinball_mean", "ae_med"]]
    m = a.merge(b, on=["issue_id", "step"], suffixes=("_n", "_m"))
    m["d_pb"] = m["pinball_mean_m"] - m["pinball_mean_n"]
    m["d_ae"] = m["ae_med_m"] - m["ae_med_n"]
    return m


def _delta_stats(m: pd.DataFrame, col: str, label: str, B: int = BOOT_B) -> dict:
    d = _daily(m, col)
    n_days = len(d)
    if n_days == 0:
        return {"n_days": 0, "n_rows": 0, "delta": None, "lo": None, "hi": None, "t": None, "p": None}
    daily_mean = (d["sum"] / d["count"]).to_numpy()
    est, lo, hi = block_bootstrap_ratio(daily_mean, np.ones_like(daily_mean), BOOT_BLOCK_DAYS, B, seed_for(BOOT_SEED, label))
    t = hac_t_test(daily_mean)
    return {"n_days": int(n_days), "n_rows": int(d["count"].sum()), "delta": est, "lo": lo, "hi": hi, "t": t.get("t"), "p": t.get("p")}


def _ratio_ci(S: pd.DataFrame, col: str, label: str, B: int = 1000) -> dict:
    d = _daily(S, col)
    if d.empty:
        return {"est": None, "lo": None, "hi": None, "n_days": 0}
    est, lo, hi = block_bootstrap_ratio(d["sum"].to_numpy(), d["count"].to_numpy(), BOOT_BLOCK_DAYS, B, seed_for(BOOT_SEED, label))
    return {"est": est, "lo": lo, "hi": hi, "n_days": int(len(d))}


def _verdict(stat: dict, p_holm: float | None, in_family: bool) -> str:
    if stat["n_days"] < MIN_DAYS_FOR_CLAIM:
        return "insufficient"
    if stat["lo"] is None or stat["hi"] is None or stat["delta"] is None:
        return "insufficient"
    excl = stat["lo"] > 0 or stat["hi"] < 0
    sig = (p_holm is not None and p_holm < SIGNIFICANCE) if in_family else (stat["p"] is not None and stat["p"] < SIGNIFICANCE)
    if excl and sig:
        return "better" if stat["delta"] < 0 else "worse"
    return "no_difference"


def build_summary(root, ledger_check: bool = True) -> dict:
    root = Path(root)
    P = paths(root)
    fc, sc = load_forecasts(root), load_scores(root)
    receipts = read_receipts(P["receipts"])
    now = utcnow()
    out: dict = {
        "meta": {"generated_at": now.isoformat(), "protocol_version": PROTOCOL_VERSION, "quantiles": list(QUANTILES),
                 "min_days_for_claim": MIN_DAYS_FOR_CLAIM, "boot_block_days": BOOT_BLOCK_DAYS, "boot_B": BOOT_B,
                 "models": {m: {**MODEL_INFO[m], "version": v} for m, v in MODELS.items()},
                 "bucket_label": BUCKET_LABEL, "benchmark": BENCHMARK, "challengers": list(CHALLENGERS)},
    }
    out["verification"] = {"ledger": led.verify(root, P["ledger"]) if ledger_check else {"ok": None},
                           "receipts_collected": len(receipts)}
    out["verification"]["ledger"].pop("problems", None) if out["verification"]["ledger"].get("ok") else None

    out["counts"] = {"issues": int(fc["issue_id"].nunique()) if len(fc) else 0, "forecast_rows": int(len(fc)),
                     "first_issue": fc["issued_at"].min().isoformat() if len(fc) else None,
                     "last_issue": fc["issued_at"].max().isoformat() if len(fc) else None,
                     "scored_rows": 0, "missing_actual_rows": 0, "violated_rows": 0, "unverified_rows": 0, "verified_rows": 0,
                     "days_scored": 0}
    if len(fc):
        out["counts"]["models_issuing"] = sorted(fc["model_id"].unique().tolist())
    out["fan_live"] = _fan(fc, sc, scored_only=False) if len(fc) else None

    if sc.empty:
        out.update(state="warming_up", leaderboard=[], paired=[], reliability=[], timeseries=[], strata=[], worst=[], fan_scored=None)
        return _clean(out)

    out["counts"]["missing_actual_rows"] = int((sc["status"] == "missing_actual").sum())
    out["counts"]["violated_rows"] = int((sc["prereg_status"] == "violated").sum())
    out["counts"]["unverified_rows"] = int(((sc["prereg_status"] == "unverified") & (sc["status"] == "scored")).sum())
    out["counts"]["verified_rows"] = int(((sc["prereg_status"] == "verified") & (sc["status"] == "scored")).sum())
    S = sc[(sc["status"] == "scored") & (sc["prereg_status"] != "violated")]
    if S.empty:
        out.update(state="warming_up", leaderboard=[], paired=[], reliability=[], timeseries=[], strata=[], worst=[], fan_scored=None)
        return _clean(out)
    S = _prep(S)
    out["counts"]["scored_rows"] = int(len(S))
    out["counts"]["days_scored"] = int(S["target_date"].nunique())

    # --- eligibility and common support
    days_per_model = {m: len(_daily(S[S["model_id"] == m], "pinball_mean")) for m in S["model_id"].unique()}
    bench_ok = days_per_model.get(BENCHMARK, 0) >= MIN_DAYS_FOR_CLAIM
    active = list(days_per_model)
    lb_models = [m for m in active if days_per_model[m] >= MIN_DAYS_FOR_CLAIM] if bench_ok else active
    if BENCHMARK not in lb_models and BENCHMARK in active:
        lb_models.append(BENCHMARK)
    key = S[["issue_id", "step", "model_id"]]
    sets = [set(zip(key.loc[key["model_id"] == m, "issue_id"], key.loc[key["model_id"] == m, "step"])) for m in lb_models]
    common = set.intersection(*sets) if sets else set()
    C = S[S["model_id"].isin(lb_models) & pd.Series(list(zip(S["issue_id"], S["step"])), index=S.index).isin(common)]
    out["common_support"] = {"models": lb_models, "n_keys": len(common), "warming_up": [m for m in active if m not in lb_models],
                             "days_per_model": days_per_model}
    out["state"] = "claims_enabled" if bench_ok else "insufficient_evidence"

    # --- leaderboard (common support) + warming-up models on their own rows (flagged)
    def lb_row(df: pd.DataFrame, m: str, b: str, comparable: bool) -> dict:
        d = df[(df["model_id"] == m) & ((df["bucket"] == b) if b != "all" else True)]
        r = {"model": m, "bucket": b, "n_rows": int(len(d)), "comparable": comparable,
             "mean_pinball": float(d["pinball_mean"].mean()) if len(d) else None, "mae_med": float(d["ae_med"].mean()) if len(d) else None}
        dd = d[(d["bucket"] == b)] if b != "all" else d
        r["pinball_ci"] = _ratio_ci(dd, "pinball_mean", f"pb|{m}|{b}", 600)
        if m != BENCHMARK:
            r["cov90"] = _ratio_ci(dd, "cov90", f"c90|{m}|{b}", 600)
            r["cov50"] = _ratio_ci(dd, "cov50", f"c50|{m}|{b}", 600)
            r["mean_w90"] = float(d["w90"].mean()) if len(d) else None
            r["winkler90"] = float(d["winkler90"].mean()) if len(d) else None
        return r

    lb = []
    for b in [*BUCKETS, "all"]:
        for m in lb_models:
            lb.append(lb_row(C, m, b, True))
        for m in out["common_support"]["warming_up"]:
            lb.append(lb_row(S, m, b, False))
    out["leaderboard"] = lb

    # --- paired comparisons vs benchmark (pairwise support, pre-registered family = 3 x 3)
    paired, pvals, pvals_ae = [], {}, {}
    for m in CHALLENGERS:
        if m not in active or BENCHMARK not in active:
            continue
        pm = _paired(S, m)
        for b in [*BUCKETS, "all"]:
            sub = pm if b == "all" else pm[pm["bucket"] == b]
            st = _delta_stats(sub, "d_pb", f"dpb|{m}|{b}")
            st_ae = _delta_stats(sub, "d_ae", f"dae|{m}|{b}", B=600)
            rec = {"model": m, "bucket": b, "in_family": b != "all", "pinball": st, "mae": st_ae}
            paired.append(rec)
            if b != "all" and st["p"] is not None:
                pvals[f"{m}|{b}"] = st["p"]
            if b != "all" and st_ae["p"] is not None:
                pvals_ae[f"{m}|{b}"] = st_ae["p"]
    adj, adj_ae = holm(pvals), holm(pvals_ae)   # two separate pre-registered families (9 tests each)
    for rec in paired:
        k = f"{rec['model']}|{rec['bucket']}"
        rec["p_holm"] = adj.get(k) if rec["in_family"] else None
        rec["p_holm_mae"] = adj_ae.get(k) if rec["in_family"] else None
        rec["verdict"] = _verdict(rec["pinball"], rec["p_holm"], rec["in_family"])
        rec["verdict_mae"] = _verdict(rec["mae"], rec["p_holm_mae"], rec["in_family"])
    out["paired"] = paired

    # --- reliability (common support)
    rel = []
    for m in lb_models:
        for b in ["all", *BUCKETS]:
            d = C[(C["model_id"] == m) & ((C["bucket"] == b) if b != "all" else True)]
            pts = []
            for tau, c in zip(QUANTILES, QCOLS):
                r = _ratio_ci(d, f"hit_{c}", f"rel|{m}|{b}|{c}", 400)
                pts.append({"tau": tau, **r})
            rel.append({"model": m, "bucket": b, "points": pts})
    out["reliability"] = rel

    # --- daily time series: paired delta vs benchmark, and benchmark level
    ts = []
    for m in CHALLENGERS:
        if m not in active or BENCHMARK not in active:
            continue
        pm = _paired(S, m)
        d = _daily(pm, "d_pb")
        if d.empty:
            continue
        v = (d["sum"] / d["count"])
        c90 = _daily(S[S["model_id"] == m], "cov90")
        ts.append({"model": m, "dates": [str(x) for x in v.index], "delta_pinball": v.round(4).tolist(),
                   "rolling7": v.rolling(7, min_periods=3).mean().round(4).tolist(),
                   "cov90_dates": [str(x) for x in c90.index], "cov90": (c90["sum"] / c90["count"]).round(4).tolist()})
    b = _daily(S[S["model_id"] == BENCHMARK], "pinball_mean")
    out["timeseries"] = ts
    out["benchmark_series"] = {"dates": [str(x) for x in b.index], "pinball": (b["sum"] / b["count"]).round(4).tolist()}

    # --- stratified diagnostics (exploratory; descriptive only)
    strata = []
    sub = C[C["model_id"].isin(lb_models)]
    nes = sub[sub["model_id"] == BENCHMARK]
    if len(nes):
        terc = np.nanquantile(nes["neso_at_issue"], [1 / 3, 2 / 3])
        sub = sub.assign(neso_level=np.where(sub["neso_at_issue"] <= terc[0], "low", np.where(sub["neso_at_issue"] <= terc[1], "mid", "high")))
        sub = sub.assign(block=pd.cut(sub["local_hour"], [-1, 5, 11, 17, 23], labels=[x[2] for x in LONDON_BLOCKS]).astype(str),
                         daytype=np.where(sub["weekend"], "weekend", "weekday"))
        out["neso_level_cutpoints"] = [float(x) for x in terc]
        for dim, label in (("block", "Local time of target"), ("daytype", "Day type"), ("neso_level", "NESO forecast level (tercile)")):
            for val, g in sub.groupby(dim):
                row = {"dim": dim, "dim_label": label, "value": str(val), "n_rows": int(len(g) // max(1, g["model_id"].nunique())), "models": {}}
                for m in lb_models:
                    gm = g[g["model_id"] == m]
                    row["models"][m] = {"mean_pinball": float(gm["pinball_mean"].mean()) if len(gm) else None,
                                        "cov90": float(gm["cov90"].mean()) if (len(gm) and m != BENCHMARK) else None}
                strata.append(row)
    out["strata"] = strata

    # --- worst misses (descriptive)
    wn = S[S["model_id"] == BENCHMARK].nlargest(12, "ae_med")
    cq = S[S["model_id"] == "neso_cqr"].set_index(["issue_id", "step"])
    worst = []
    for _, r in wn.iterrows():
        k = (r["issue_id"], r["step"])
        inside = None
        if k in cq.index:
            q = cq.loc[k]
            inside = bool(q["q05"] <= r["actual"] <= q["q95"])
        worst.append({"target_from": r["target_from"], "step": int(r["step"]), "horizon_h": round(float(r["horizon_min"]) / 60, 1),
                      "neso": float(r["q50"]), "actual": float(r["actual"]), "error": float(r["actual"] - r["q50"]), "inside_cqr90": inside})
    out["worst"] = worst
    out["fan_scored"] = _fan(fc, sc, scored_only=True)
    return _clean(out)


def _fan(fc: pd.DataFrame, sc: pd.DataFrame, scored_only: bool) -> dict | None:
    if fc.empty:
        return None
    if scored_only:
        if sc.empty:
            return None
        ok = sc[(sc["status"] == "scored") & (sc["model_id"] == BENCHMARK)].groupby("issue_id").size()
        full = ok[ok >= 90].index
        if len(full) == 0:
            return None
        iid = fc[fc["issue_id"].isin(full)].sort_values("issued_at")["issue_id"].iloc[-1]
    else:
        iid = fc.sort_values("issued_at")["issue_id"].iloc[-1]
    f = fc[fc["issue_id"] == iid]
    res = {"issue_id": iid, "issued_at": f["issued_at"].iloc[0], "models": {}}
    for m, g in f.groupby("model_id"):
        g = g.sort_values("step")
        res["models"][m] = {"t": [t.isoformat() for t in g["target_from"]], **{c: g[c].round(2).tolist() for c in ("q05", "q25", "q50", "q75", "q95")}}
    if scored_only:
        s = sc[(sc["issue_id"] == iid) & (sc["model_id"] == BENCHMARK)].sort_values("step")
        res["actual"] = {"t": [t.isoformat() for t in s["target_from"]], "y": s["actual"].round(2).tolist()}
    return res


def write_summary(root) -> Path:
    P = paths(root)
    P["site"].mkdir(parents=True, exist_ok=True)
    summ = build_summary(root)
    P["summary"].write_text(json.dumps(summ, separators=(",", ":")))
    return P["summary"]
