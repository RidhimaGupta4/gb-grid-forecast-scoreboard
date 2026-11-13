# Data card & data ethics

## 1. What data is used

| Item | Detail |
|---|---|
| Source | **Carbon Intensity API** (`api.carbonintensity.org.uk`), produced by NESO (National Energy System Operator) |
| Endpoints | National only: `/intensity/{from}/fw48h` (NESO's 48 h forecast), `/intensity/{from}/{to}` (history, ≤ 14 days per call, fetched in 13-day chunks) |
| Fields used | `from`, `to`, `intensity.forecast`, `intensity.actual` (gCO₂/kWh). `intensity.index` is parsed but never used. |
| Resolution | 30-minute settlement periods, UTC timestamps |
| Real vs synthetic | **100 % real.** The scoreboard contains no simulated, imputed or interpolated values. Test fixtures live only in `tests/` and are fenced off (see §5). |
| Personal data | **None.** The data describes the national electricity system. No individuals, households or devices. |

## 2. What is stored in this repository

* `data/raw/neso_fw48h/…json.gz` — the **raw** NESO forecast response at each issue time (the benchmark vintage).
* `data/forecasts/…parquet` — my forecasts (quantiles per model × step), plus the NESO value at issue time.
* `data/scores/…parquet` — matured forecasts joined with the realised actual and per-row metrics.
* `data/manifests/…json` — request URLs, HTTP status, UTC fetch time and SHA-256 of every API response, code git SHA, protocol/model versions, data-quality counters.
* `data/ledger.jsonl`, `data/receipts.jsonl` — hash chain and server-side push receipts.

Raw history responses are *not* stored (only their SHA-256 and the 84-day window used is re-fetchable); the repository
therefore redistributes only a small amount of derived data and NESO's forecast snapshots.

## 3. Licence and attribution

The data provider states on <https://carbonintensity.org.uk/> (checked 13 Nov 2026) that the API is licensed under **Creative Commons Attribution 4.0 International (CC BY 4.0)**: <https://creativecommons.org/licenses/by/4.0/>. Terms of Use: <http://terms.carbonintensity.org.uk>. The Carbon Intensity API is developed by NESO (National Energy System Operator) in partnership with Environmental Defense Fund Europe, the University of Oxford Department of Computer Science and WWF.

How this project meets CC BY 4.0:

1. **Attribution:** *"Contains data from the Carbon Intensity API, produced by NESO (National Energy System Operator), in partnership with Environmental Defense Fund Europe, the University of Oxford Department of Computer Science and WWF. Licensed under CC BY 4.0."* This appears on the dashboard and in the README.
2. **Licence link:** given above.
3. **Changes indicated:** NESO forecast snapshots in `data/raw/` are stored exactly as received. Everything else in `data/` (forecasts, scores, metrics, summaries) is derived by this project and is **not** NESO's data or NESO's forecast.

This project is independent: **not affiliated with, sponsored by, or endorsed by NESO or its partners.** The code is MIT-licensed; the data keeps its own licence.

## 4. Data ethics & responsible use

* **No human subjects, no personal data, no web scraping of private systems.** Only a public, documented, free API is used.
* **Fair treatment of the benchmark.** NESO's forecast is a good-faith operational product, not an adversary. The scoreboard is designed so that NESO is judged on what it publishes (a point forecast) *and* on like-for-like point accuracy, so it is not penalised for lacking intervals. Pre-registered priors expect NESO to win on most accuracy cells.
* **No overclaiming.** Verdicts are suppressed below 28 days; seasonal coverage needs ≥ 1 year; exploratory cuts are labelled; negative and null results are published with the same prominence.
* **Not for operations.** Nothing here is advice for grid balancing, trading, or demand shifting. The forecasts are a research artefact; a wrong forecast must never be acted on for anything safety- or market-critical.
* **Respectful API use.** Roughly 8 requests per issue run (1 forecast snapshot + 7 history chunks) × 4 runs per day, plus a small daily scoring pass (~35 requests/day in total), 0.6 s pause between calls, exponential back-off on 429/5xx, descriptive `User-Agent`, no parallelism. If the provider asks for less, reduce the cadence in `config.py` via a new protocol version.
* **Environmental footprint.** The motivating domain is decarbonisation; the footprint of the pipeline is a few CI minutes per day on shared runners. Nothing is trained on GPUs.
* **Transparency of failure.** Skipped runs, missing actuals, violated pre-registrations and unverified receipts are counted and shown, not hidden.
* **Misuse risk.** Because a transparent benchmark can be gamed by selective reading, the dashboard shows the pre-registered families first, discloses multiplicity handling, and links to the protocol.
* **Reproducibility & retention.** Everything needed to re-compute every number is in Git (raw NESO snapshots, code SHA, protocol version). The data is retained for the life of the repo; deletions would break the hash chain and are therefore detectable.

## 5. Preventing synthetic data from ever entering the scoreboard

1. `tests/fakeapi.py` is the only synthetic generator; its URLs are `https://fixture.invalid/…` and it is not importable from the package.
2. `tests/test_provenance.py` fails CI if any manifest under `data/` points anywhere other than `https://api.carbonintensity.org.uk/` or any forecast row lacks the real-source tag.
3. Integration tests write only to temporary directories, never to `data/`.
4. The dashboard renders an explicit "warming up" state instead of placeholder charts when no real outcomes exist.

## 6. Data-quality handling

Deduplicated on `from` (last wins) · sorted · values outside [0, 1000] gCO₂/kWh set to missing and counted · gaps counted · null actuals counted · nothing imputed. Quality counters are written to every manifest.

## 7. Known data issues

* `actual` is NESO's modelled estimate (from generation by fuel type and emission factors), not a metered measurement, and can be revised; this project scores the value fetched ≥ 24 h after the period ended.
* The definition/methodology of the index and factors can change over time; any such change is a structural break the scoreboard cannot see unless the provider announces it.
* The API is a free service without an SLA; outages produce gaps, which are never back-filled.
