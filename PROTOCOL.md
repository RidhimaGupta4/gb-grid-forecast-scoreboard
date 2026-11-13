# Pre-registration protocol — v1.0.0

> **Status: FROZEN at launch** (tag `protocol-v1.0.0`, created before the first scheduled issue).
> Anything that changes forecasts or scoring requires a new protocol version, new model ids, and a new
> evaluation start date. Old results are never re-scored, overwritten or deleted. Deviations are logged in §13.

## 1. Questions

* **Q1 (central accuracy).** Can a transparent model produce a *point* forecast of GB national carbon intensity that is more accurate than NESO's published forecast, at 0–6 h, 6–24 h or 24–48 h ahead?
* **Q2 (uncertainty).** Can a transparent model produce *probabilistic* forecasts with better pinball loss than NESO's point forecast, and are its intervals calibrated?
* **Q3 (failure modes).** Under which pre-specified conditions (local time of day, day type, NESO forecast level, horizon) do forecasts degrade most?

Q1 and Q2 are distinct on purpose. NESO publishes **no** uncertainty, so any model that outputs calibrated intervals is
structurally advantaged on pinball loss (Q2). Q1 is the like-for-like test of the central forecast.

## 2. Data and definitions

| Term | Definition |
|---|---|
| Source | Carbon Intensity API, national endpoints only (`/intensity/{from}/fw48h`, `/intensity/{from}/{to}`). Units gCO₂/kWh. |
| Period | Half-hour settlement period, identified by its `from` stamp (UTC). |
| Issue time `t0` | System clock (UTC) read *before* any network call of the issue run. Scheduled 00:17, 06:17, 12:17, 18:17 UTC; GitHub may delay runs; the true `t0` is recorded. |
| Target / step `k` | The `k`-th period (1…96) starting at or after the first half-hour boundary that is ≥ 10 min after `t0`. |
| Horizon buckets | `h00_06` = steps 1–12, `h06_24` = 13–48, `h24_48` = 49–96. |
| Availability lag | An actual is treated as known only if its period ended ≥ 1 h before `t0`. |
| Actual | The `actual` field of a period, fetched **≥ 24 h after the period ended** (the *scoring lag*), at scoring time. Never re-fetched or revised afterwards. The value is NESO's own estimate (§12). |
| NESO benchmark | The `forecast` field of the `fw48h` response fetched at `t0`: *the vintage NESO had published when I issued mine.* The API's retrospective `forecast` field for past periods is **never** used. The raw response is stored (gzipped) and hash-chained. |
| Information set | (a) actuals with period-end ≤ `t0` − 1 h; (b) the NESO snapshot at `t0`; (c) residuals of *earlier* forecasts whose actual was fetched at or before `t0` (`neso_cqr` only). Nothing else. |

## 3. Forecasters (frozen specifications)

All output seven quantiles τ ∈ {0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95} for all 96 steps. Quantiles are sorted per row
(non-crossing) and clipped at 0. A model that is not ready does **not** issue; it never issues a degraded or imputed forecast.

* **`neso_point` (benchmark).** NESO's point forecast repeated across all quantiles (a degenerate distribution). Its interval metrics are undefined and reported as n/a.
* **`neso_cqr`.** `q_τ = NESO + Q_τ(residuals)`. Residuals `r = actual − NESO` come from my own earlier `neso_point` rows whose `actual_fetched_at ≤ t0` and whose target started within the last 60 days, pooled by step group {1–4, 5–12, 13–24, 25–48, 49–96}; ≥ 200 residuals per group required. `Q_τ` is the finite-sample-corrected empirical quantile (rank ⌈(n+1)τ⌉ for τ>0.5, ⌊(n+1)τ⌋ for τ<0.5, median at 0.5). Excludes rows marked `violated`.
* **`anomaly_persist`.** `μ(key)` = mean actual by (weekend flag, Europe/London half-hour of day) over the trailing 84 days; anomaly `a = actual − μ`. For each lead gap `g` (in half-hours from the last known actual `L`), fit OLS `a_{t+g} = α_g + β_g a_t` over the trailing 28 days of anomalies (≥ 500 pairs). Forecast `μ(key_target) + α_g + β_g a_L + Q_τ(OLS residuals at gap g)`. Requires ≥ 42 days of history (≥ 80 % coverage).
* **`climatology`.** Finite-sample-corrected empirical quantiles of actuals by (weekend flag, local half-hour) over the trailing 84 days; ≥ 8 observations per key. No persistence, no weather.

Bank holidays are treated as ordinary weekdays (limitation, §11 of the README).

## 4. Scoring rules

* **Pinball loss** averaged over the seven τ (`pinball_mean`). For a degenerate forecast it equals ½·|error|.
* **Point accuracy:** absolute error of the median forecast (`ae_med`).
* **Coverage** of the central 90 % ([q05, q95]) and 50 % ([q25, q75]) intervals; **Winkler/interval score** (α = 0.1); mean interval width; reliability = share of outcomes ≤ q_τ.
* Rows with a missing NESO forecast or missing actual are **excluded, never imputed**. Rows still lacking an actual 7 days after the scoring lag are stored as `missing_actual` and counted on the dashboard.
* **Pre-registration integrity.** A forecast is `verified` if GitHub's server-side `PushEvent` time for the commit that added its file is earlier than its target period's start; `violated` (and excluded everywhere) if not; `unverified` while no receipt has been collected. Unverified rows are *included* but their share is displayed.

## 5. Inference

* **Unit of analysis:** target day (UTC). Paired loss differential `d = loss(challenger) − loss(NESO)` on identical (issue, step) pairs, averaged per day; days with < 12 paired rows in a bucket are dropped.
* **Estimator & CI:** mean of daily means; 95 % **moving-block bootstrap** (block = 7 days, B = 2000, fixed seed `20261113` + stable per-test hash).
* **Test:** Diebold–Mariano-style *t*-test on the daily series with Newey–West/Bartlett variance (lag ⌊n^{1/3}⌋), Student-*t*(n−1) reference.
* **Families (Holm-adjusted):** Family A = pinball, Family B = point accuracy (MAE of median); each family has 9 tests (3 challengers × 3 horizon buckets). The "0–48 h" row and every stratified cut are descriptive and not adjusted.
* **A verdict ("better"/"worse") requires all of:** ≥ 28 scored days in the cell · the bootstrap CI excludes 0 · Holm-adjusted *p* < 0.05. Otherwise "no detectable difference" or "insufficient evidence". No verdicts are issued below 28 days, whatever the point estimates look like.
* **Leaderboard** = common support: only (issue, step) pairs scored for *all* eligible models, so no model is flattered by an easier subset.
* **Calibration** intervals are block-bootstrap intervals over days (rows within a day are strongly dependent; Wilson intervals on raw row counts would be far too narrow and are not used).

## 6. Pre-registered strata (Q3)

Local time of the target in four blocks (00–06, 06–12, 12–18, 18–24, Europe/London) · weekday vs weekend · tercile of NESO's forecast *at issue time* · horizon bucket. Strata use only information known at issue time. **Stratifying by the realised outcome is not done**, because conditioning on `y` mechanically destroys apparent coverage. The ten largest NESO misses are shown descriptively and make no inferential claim.

## 7. Pre-registered expectations (my priors, written before any data)

1. NESO has lower MAE than both transparent challengers at 6–24 h and 24–48 h (high confidence).
2. The gap is smallest at 0–6 h; I put ~25 % on `anomaly_persist` *not* being detectably worse there.
3. `neso_cqr` beats `neso_point` on pinball at every horizon (near-certain by construction) but ties it on MAE within ≈ 0.5 gCO₂/kWh, because the residual median is small.
4. Intervals from `anomaly_persist` and `climatology` under-cover (< 90 %) during regime changes; `neso_cqr` is within ±3 pp of nominal on average but under-covers in the tails during storm/wind-ramp periods.
5. NESO's largest misses concentrate at long leads and in rapid-ramp periods; checked descriptively only.

These are recorded so that being wrong is visible. Being wrong is an acceptable outcome.

## 8. Reporting schedule and stopping rule

Evaluation runs indefinitely. First claims allowed at ≥ 28 scored days. A formal write-up is planned at ≥ 365 days so that every season appears at least once; results before that are explicitly provisional (carbon intensity is strongly seasonal and wind-regime dependent). The dashboard re-runs the tests every day and is **not** corrected for repeated looks (no alpha-spending); interim verdicts are therefore provisional and only the formal analysis counts as the pre-registered result.

## 9. Missing runs and failures

A skipped or failed run leaves a gap; it is **never back-filled**. Gaps are visible as missing batches. A run that would start its first target before the push could happen aborts (`CausalityError`), and a run whose push failed is not pre-registered and is rejected at scoring.

## 10. Researcher-degrees-of-freedom policy

* Hyper-parameters are frozen in `src/gridscore/config.py`; there is no tuning code path.
* Any change to a model, window, quantile set, bucket, or test ⇒ new `PROTOCOL_VERSION`, new model id/version, new start date; previous results stay as published.
* Exploratory analyses may be added but must be labelled exploratory and cannot create verdicts.

## 11. Leakage threat model

| # | Threat | Defence | Residual risk |
|---|---|---|---|
| 1 | Future actuals reach a model | Cutoff filter at `t0 − 1 h`; hard `CausalityError` guards; test that perturbs all post-cutoff data and asserts bit-identical forecasts (mutation-tested) | None known in code; API semantics verified by `gridscore smoke` |
| 2 | Using the API's retrospective `forecast` as the benchmark (unknown vintage, possibly hindsight-corrected) | Benchmark = own snapshot at `t0`, raw response stored and hashed | None |
| 3 | Revised actuals leaking into calibration residuals | Residuals allowed only if `actual_fetched_at ≤ t0` | Revisions after scoring are untracked (limitation) |
| 4 | Tuning on scored data | Frozen config, change policy §10 | Author could still fork and change; visible in Git history |
| 5 | Cherry-picked horizons/strata/models | Pre-registered families and strata; common-support leaderboard; exploratory labelling | Reader must respect the labels |
| 6 | Back-dating forecasts or rewriting history | SHA-256 hash-chain ledger; server-side push receipts; protected default branch (no force-push) | Repo owner could replace the whole chain before anyone clones it; external anchoring (§README roadmap) mitigates |
| 7 | Late issue (target already begun) | 10-min buffer, abort guard, receipt check per row | GitHub scheduling delays |
| 8 | Survivorship via missing actuals | Counted and displayed; never imputed | If missingness correlates with extremes, estimates are biased |
| 9 | Multiplicity | Two Holm families; descriptive rows unadjusted and labelled | Holm assumes nothing about dependence but is conservative |
| 10 | Overlapping/dependent forecasts inflate n | Day-level unit, HAC + block bootstrap | Small-sample HAC is noisy; hence the 28-day floor and bootstrap CI |

## 12. Known protocol-level caveats

* "Actual" is itself a model-based estimate of intensity from generation data, not a direct measurement.
* Scoring a probabilistic model against a point forecast is intrinsically asymmetric (§1); Family B exists to correct for it.
* `neso_cqr` is a calibration of NESO, not an independent model: it cannot be more accurate than NESO in the median by more than the residual median shift.

## 13. Deviation log

| Date | Change | Reason | Affects |
|---|---|---|---|
| — | none yet | — | — |
