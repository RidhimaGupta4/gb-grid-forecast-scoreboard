"""Score matured forecasts against realised actuals. Idempotent and append-only."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import ledger as led
from .api import CarbonAPI
from .config import MISSING_GRACE, QCOLS, SCORE_LAG, STEP, paths
from .data import validate_intensity
from .metrics import row_metrics
from .receipts import read_receipts
from .store import load_forecasts, load_scores
from .timeutil import as_utc, utcnow


def prereg_status(issue_id: str, target_from: pd.Series, receipts: dict) -> pd.Series:
    r = receipts.get(issue_id)
    if r is None:
        return pd.Series("unverified", index=target_from.index)
    pushed = as_utc(r["push_created_at"])
    return pd.Series(np.where(pushed < target_from, "verified", "violated"), index=target_from.index)


def run_score(root, api: CarbonAPI | None = None, clock=utcnow) -> dict:
    root = Path(root)
    P = paths(root)
    api = api or CarbonAPI()
    now = as_utc(clock())
    fc = load_forecasts(root)
    if fc.empty:
        return {"scored": 0, "reason": "no forecasts"}
    sc = load_scores(root)
    done = set(zip(sc["issue_id"], sc["model_id"], sc["step"])) if len(sc) else set()
    keys = list(zip(fc["issue_id"], fc["model_id"], fc["step"]))
    mask = np.array([k not in done for k in keys]) & (fc["target_to"] <= now - SCORE_LAG).to_numpy()
    todo = fc[mask].copy()
    if todo.empty:
        return {"scored": 0, "reason": "nothing matured"}

    act, fetches = api.range(todo["target_from"].min() - STEP, todo["target_to"].max() + pd.Timedelta(hours=1))
    act, quality = validate_intensity(act)
    actual = act.drop_duplicates("from_", keep="last").set_index("from_")["actual"]
    fetched_at = max(pd.Timestamp(f.fetched_at) for f in fetches)

    todo["actual"] = todo["target_from"].map(actual).astype(float)
    miss = todo["actual"].isna()
    too_old = todo["target_to"] <= now - SCORE_LAG - MISSING_GRACE
    todo = todo[~miss | too_old].copy()
    if todo.empty:
        return {"scored": 0, "reason": "actuals not yet available", "quality": quality}
    todo["status"] = np.where(todo["actual"].isna(), "missing_actual", "scored")

    receipts = read_receipts(P["receipts"])
    todo["prereg_status"] = "unverified"
    todo["receipt_pushed_at"] = pd.NaT
    for iid, g in todo.groupby("issue_id"):
        todo.loc[g.index, "prereg_status"] = prereg_status(iid, g["target_from"], receipts).to_numpy()
        if iid in receipts:
            todo.loc[g.index, "receipt_pushed_at"] = pd.Timestamp(receipts[iid]["push_created_at"])

    metric_cols: dict[str, np.ndarray] = {}
    for mid, g in todo.groupby("model_id"):
        ok = g["status"] == "scored"
        m = row_metrics(g.loc[ok, "actual"].to_numpy(), g.loc[ok, list(QCOLS)].to_numpy(), is_point=(mid == "neso_point"))
        for k, v in m.items():
            col = metric_cols.setdefault(k, pd.Series(np.nan, index=todo.index))
            col.loc[g.index[ok.to_numpy()]] = v
    for k, v in metric_cols.items():
        todo[k] = v
    todo["actual_fetched_at"] = fetched_at
    todo["scored_at"] = now
    todo["receipt_pushed_at"] = pd.to_datetime(todo["receipt_pushed_at"], utc=True)

    ym = now.strftime("%Y-%m")
    out = P["scores"] / ym / f"score_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    todo.to_parquet(out, index=False)
    led.append(root, P["ledger"], "score", out.stem, [out], now.isoformat())
    return {"scored": int((todo["status"] == "scored").sum()), "missing": int((todo["status"] == "missing_actual").sum()),
            "violated": int((todo["prereg_status"] == "violated").sum()), "quality": quality, "file": str(out)}
