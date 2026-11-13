"""Build the self-contained static dashboard (no server, no CDN, no tracking).

plotly.min.js is copied from the installed `plotly` package so the page works offline and the
deployed bundle is pinned to the version used in CI.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import paths
from .report import write_summary

TEMPLATE_DIR = Path(__file__).resolve().parent / "site_template"


def build_site(root) -> Path:
    P = paths(root)
    out = P["site"]
    out.mkdir(parents=True, exist_ok=True)
    summary_path = write_summary(root)
    import plotly  # noqa: WPS433  (only needed at build time)
    plotly_js = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
    shutil.copy(plotly_js, out / "plotly.min.js")
    html = (TEMPLATE_DIR / "index.html").read_text()
    summ = json.loads(Path(summary_path).read_text())
    html = html.replace("/*__SUMMARY_JSON__*/null", json.dumps(summ, separators=(",", ":")))
    (out / "index.html").write_text(html)
    (out / ".nojekyll").write_text("")
    return out / "index.html"
