"""Append-only SHA-256 hash chain over every forecast / score artefact.

Each entry commits to the file hashes and to the previous entry, so silently editing,
deleting or re-ordering history is detectable by anyone with a clone:  `gridscore verify`.
(It cannot stop the repo owner force-pushing a *rewritten* chain; see README, "Trust model".)
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

GENESIS = "0" * 64


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _entry_hash(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


def read(ledger: Path) -> list[dict]:
    if not ledger.exists():
        return []
    return [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]


def append(root: Path, ledger: Path, kind: str, ref: str, files: list[Path], utc: str) -> dict:
    entries = read(ledger)
    prev = entries[-1]["entry_hash"] if entries else GENESIS
    body = {
        "seq": len(entries) + 1,
        "kind": kind,
        "ref": ref,
        "utc": utc,
        "files": {str(Path(f).relative_to(root)): sha256_file(Path(f)) for f in files},
        "prev_hash": prev,
    }
    entry = {**body, "entry_hash": _entry_hash(prev, body)}
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def verify(root: Path, ledger: Path) -> dict:
    entries, prev, problems = read(ledger), GENESIS, []
    for i, e in enumerate(entries, 1):
        body = {k: e[k] for k in ("seq", "kind", "ref", "utc", "files", "prev_hash")}
        if e["seq"] != i:
            problems.append(f"seq {e['seq']} out of order at position {i}")
        if e["prev_hash"] != prev:
            problems.append(f"entry {i}: prev_hash mismatch")
        if _entry_hash(e["prev_hash"], body) != e["entry_hash"]:
            problems.append(f"entry {i}: entry_hash mismatch")
        for rel, h in e["files"].items():
            p = Path(root) / rel
            if not p.exists():
                problems.append(f"entry {i}: missing file {rel}")
            elif sha256_file(p) != h:
                problems.append(f"entry {i}: file hash mismatch {rel}")
        prev = e["entry_hash"]
    return {"ok": not problems, "n_entries": len(entries), "head": prev, "problems": problems}
