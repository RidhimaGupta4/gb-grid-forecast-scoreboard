from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gridscore", description="Live pre-registered GB grid forecast scoreboard")
    ap.add_argument("--root", default=".", help="repository root (default: cwd)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("issue", help="issue a forecast batch (needs network)")
    sub.add_parser("score", help="score matured forecasts (needs network)")
    r = sub.add_parser("receipts", help="collect server-side push receipts from the GitHub Events API")
    r.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    sub.add_parser("smoke", help="read-only live API sanity check; writes nothing (run once before launch)")
    sub.add_parser("verify", help="verify the hash-chain ledger")
    sub.add_parser("report", help="aggregate stores into site_build/summary.json")
    sub.add_parser("site", help="build the static dashboard into site_build/")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()

    if a.cmd == "issue":
        from .issue import run_issue
        print(json.dumps(run_issue(root), indent=2, default=str))
    elif a.cmd == "score":
        from .score import run_score
        print(json.dumps(run_score(root), indent=2, default=str))
    elif a.cmd == "receipts":
        from .receipts import collect
        print(json.dumps(collect(root, a.repo, os.environ.get("GITHUB_TOKEN"))))
    elif a.cmd == "smoke":
        return _smoke()
    elif a.cmd == "verify":
        from . import ledger
        from .config import paths
        res = ledger.verify(root, paths(root)["ledger"])
        print(json.dumps(res, indent=2))
        return 0 if res["ok"] else 1
    elif a.cmd == "report":
        from .report import write_summary
        print(write_summary(root))
    elif a.cmd == "site":
        from .site import build_site
        print(build_site(root))
    return 0


def _smoke() -> int:
    """Checks the assumptions this project makes about the live API. Writes nothing."""
    import pandas as pd
    from .api import CarbonAPI
    from .config import N_STEPS, STEP
    from .data import validate_intensity
    from .timeutil import first_target, utcnow
    api, now = CarbonAPI(), utcnow()
    b0 = first_target(now)
    problems = []
    nf, f1 = api.fw48h(b0)
    print(f"fw48h from {b0}: {len(nf)} rows, forecast non-null={int(nf['forecast'].notna().sum())}, actual non-null={int(nf['actual'].notna().sum())}")
    if len(nf) < N_STEPS - 4:
        problems.append(f"fw48h returned {len(nf)} rows, expected ~{N_STEPS}")
    if len(nf) and nf["from_"].min() < b0:
        problems.append("fw48h returned periods before the requested start")
    if len(nf) and not ((nf["to_"] - nf["from_"]) == STEP).all():
        problems.append("fw48h periods are not 30 minutes long")
    h, fs = api.range(now - pd.Timedelta(days=3), now - pd.Timedelta(hours=2))
    h, q = validate_intensity(h)
    print(f"history 3 d: {len(h)} rows, quality={q}")
    if len(h) < 3 * 48 * 0.9:
        problems.append("history shorter than expected")
    if len(h) and not (h["from_"].dt.minute.isin([0, 30])).all():
        problems.append("history periods not aligned to :00/:30")
    print("fetch provenance:", f1.record(), fs[0].record() if fs else None)
    print("SMOKE FAILED:\n - " + "\n - ".join(problems) if problems else "SMOKE OK: API behaves as the protocol assumes")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
