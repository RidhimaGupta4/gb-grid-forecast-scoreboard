"""Issue a pre-registered forecast batch.

Flow:  t0 = clock  ->  fetch NESO fw48h snapshot  ->  fetch history  ->  hard causality guards
       ->  run models  ->  write parquet + raw snapshot + manifest + ledger entry.
The workflow then commits and pushes; GitHub's server-side push time is later collected as a
receipt and any forecast not provably pushed before its target period began is excluded.
"""
from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from . import ledger as led
from .api import CarbonAPI
from .config import (AVAIL_LAG, CQR_WINDOW_DAYS, HISTORY_FETCH_DAYS, MODELS, N_STEPS, PROTOCOL_VERSION, QCOLS,
                     SOURCE_TAG, STEP, paths)
from .data import validate_intensity
from .models import Context, run_all
from .store import load_scores
from .timeutil import as_utc, first_target, utcnow


class CausalityError(AssertionError):
    pass


def issue_id_for(t: pd.Timestamp) -> str:
    return "issue_" + as_utc(t).strftime("%Y%m%dT%H%M%SZ")


def build_residual_memory(root, issue_time: pd.Timestamp) -> pd.DataFrame:
    """Residuals (actual - NESO point) of earlier forecasts whose actual was FETCHED by issue_time."""
    sc = load_scores(root)
    cols = ["step", "target_from", "resid"]
    if sc.empty:
        return pd.DataFrame(columns=cols)
    m = sc[(sc["model_id"] == "neso_point") & (sc["status"] == "scored") & (sc["prereg_status"] != "violated")]
    m = m[pd.to_datetime(m["actual_fetched_at"], utc=True) <= issue_time]
    out = pd.DataFrame({"step": m["step"].to_numpy(), "target_from": pd.to_datetime(m["target_from"], utc=True).to_numpy(),
                        "resid": (m["actual"] - m["q50"]).to_numpy()})
    out["target_from"] = pd.to_datetime(out["target_from"], utc=True)
    return out


def _git_sha(root: Path) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def run_issue(root, api: CarbonAPI | None = None, clock=utcnow) -> dict:
    root = Path(root)
    P = paths(root)
    api = api or CarbonAPI()
    issue_time = as_utc(clock())  # t0, read BEFORE any network call
    iid = issue_id_for(issue_time)
    b0 = first_target(issue_time)
    targets = pd.DatetimeIndex([b0 + STEP * k for k in range(N_STEPS)])
    cutoff = issue_time - AVAIL_LAG

    # ---- NESO snapshot (the benchmark vintage). Only the `forecast` column is retained.
    neso_df, f_neso = api.fw48h(b0)
    neso_df = neso_df.drop_duplicates("from_", keep="last")
    neso = neso_df.set_index("from_")["forecast"].reindex(targets)
    if neso.isna().all():
        raise RuntimeError("NESO forecast snapshot empty; refusing to issue")

    # ---- history: actuals of periods that ENDED at least AVAIL_LAG ago
    h_raw, f_hist = api.range(cutoff - pd.Timedelta(days=HISTORY_FETCH_DAYS), cutoff)
    h_raw, quality = validate_intensity(h_raw)
    hist = h_raw[(h_raw["to_"] <= cutoff) & h_raw["actual"].notna()][["from_", "actual"]].reset_index(drop=True)

    mem = build_residual_memory(root, issue_time)

    # ---- HARD CAUSALITY GUARDS (abort the run rather than issue a contaminated forecast)
    if len(hist) and (h_raw.loc[h_raw["actual"].notna() & (h_raw["to_"] <= cutoff), "to_"].max() > cutoff):
        raise CausalityError("history contains a period ending after the availability cutoff")
    if not (targets > issue_time + pd.Timedelta(minutes=9)).all():
        raise CausalityError("a target period starts before issue_time + buffer")
    if len(mem) and not (mem["target_from"] + STEP <= issue_time).all():
        raise CausalityError("residual memory contains a target that had not ended at issue time")
    if as_utc(clock()) >= b0:
        raise CausalityError("run too slow: first target period already started")

    ctx = Context(issue_time=issue_time, targets=targets, history=hist, neso=neso, residual_memory=mem)
    results = run_all(ctx)

    rows, skipped = [], {}
    for r in results:
        if r.q is None:
            skipped[r.model_id] = r.reason
            continue
        df = pd.DataFrame(r.q, columns=list(QCOLS))
        df.insert(0, "step", np.arange(1, N_STEPS + 1))
        df.insert(0, "model_id", r.model_id)
        rows.append(df)
    if not rows:
        raise RuntimeError("no model produced a forecast")
    fc = pd.concat(rows, ignore_index=True)
    fc["model_version"] = fc["model_id"].map(MODELS)
    fc["issue_id"] = iid
    fc["issued_at"] = issue_time
    fc["target_from"] = targets[fc["step"].to_numpy() - 1]
    fc["target_to"] = fc["target_from"] + STEP
    fc["horizon_min"] = ((fc["target_from"] - issue_time) / pd.Timedelta(minutes=1)).astype(float)
    fc["neso_at_issue"] = neso.to_numpy()[fc["step"].to_numpy() - 1]
    fc["source"] = SOURCE_TAG
    fc["protocol_version"] = PROTOCOL_VERSION
    if not (fc["target_from"] > fc["issued_at"]).all():
        raise CausalityError("forecast row with target before issue")

    ym = issue_time.strftime("%Y-%m")
    fpath = P["forecasts"] / ym / f"{iid}.parquet"
    rpath = P["raw"] / "neso_fw48h" / ym / f"{iid}.json.gz"
    mpath = P["manifests"] / ym / f"{iid}.json"
    for p in (fpath, rpath, mpath):
        p.parent.mkdir(parents=True, exist_ok=True)
    fc.to_parquet(fpath, index=False)
    with gzip.open(rpath, "wb") as g:
        g.write(f_neso.raw)
    manifest = {
        "issue_id": iid, "issued_at": issue_time.isoformat(), "protocol_version": PROTOCOL_VERSION,
        "code_git_sha": _git_sha(root), "models": MODELS, "skipped_models": skipped,
        "first_target": b0.isoformat(), "history_cutoff": cutoff.isoformat(), "n_history_rows": int(len(hist)),
        "residual_memory_rows": int(len(mem)), "quality": quality,
        "fetches": {"neso_fw48h": f_neso.record(), "history": [f.record() for f in f_hist]},
    }
    mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    entry = led.append(root, P["ledger"], "issue", iid, [fpath, rpath, mpath], issue_time.isoformat())
    return {"issue_id": iid, "rows": int(len(fc)), "skipped": skipped, "ledger_seq": entry["seq"], "files": [str(fpath)]}
