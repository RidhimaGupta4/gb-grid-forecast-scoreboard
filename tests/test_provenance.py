"""Guard: only real-API provenance may ever exist under the repository's data/ folder."""
import json
from pathlib import Path

import pandas as pd

from gridscore.config import API_BASE, SOURCE_TAG

DATA = Path(__file__).resolve().parents[1] / "data"


def test_every_manifest_points_at_the_real_api():
    for m in DATA.rglob("*.json"):
        if m.parent.name == "raw":
            continue
        man = json.loads(m.read_text())
        urls = [man["fetches"]["neso_fw48h"]["url"]] + [f["url"] for f in man["fetches"]["history"]]
        assert all(u.startswith(API_BASE + "/") for u in urls), f"{m}: non-API provenance {urls}"
        assert "fixture" not in json.dumps(man)


def test_every_forecast_row_is_tagged_with_the_real_source():
    for f in (DATA / "forecasts").rglob("*.parquet"):
        assert set(pd.read_parquet(f, columns=["source"])["source"]) == {SOURCE_TAG}, f
