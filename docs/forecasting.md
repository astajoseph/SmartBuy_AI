# Statistics, forecasting and seasonality

## Data used

* **Recorded points** — prices the source or our collector actually recorded (`v_price_points`).
* **Daily series** — one value per IST day; days without a record carry the previous recorded price
  forward for **at most 30 days**. Longer gaps are unknown (NaN) and are excluded from statistics,
  backtests and event windows. This is a step-function assumption that fits price trackers, which record
  when a price is checked or changes; it is always labelled.

## History statistics (`services/price_service.py`)

Current price, offer price, lowest recorded price, recorded-point stats, daily stats (all-time, last 365
known days), percentile of today's price among last year's known days (needs ≥ 60 known days), changes
over 7/30/90/365 days (unknown if no record within 30 days of the reference date), and data quality
(recorded days, span, median/max gap, unknown-day share, sufficiency flags).

## Forecasts (`forecasting/`)

Baselines: `naive_last`, `median_30d`, `median_90d`, `ewma_hl14d`, `damped_trend_60d`.

* **Rolling-origin backtest**: origins every 7 days after ≥ 60 days of history; each forecast sees only
  `history[:origin]` (test-enforced); scored against the value `h` days later; origins whose start or
  target day is unknown are skipped.
* **Selection**: lowest MAE, but a method must beat `naive_last` by ≥ 2 % MAE, otherwise `naive_last` is used.
* **Interval**: empirical 10th–90th percentile of actual/predicted ratios; coverage is checked on the
  second half of origins using quantiles from the first half. Coverage < 65 % adds a limitation.
* **Insufficient data**: fewer than 20 recorded days or 120 known days → "Insufficient historical data for
  a reliable forecast."
* Horizons 7 / 14 / 30 days (plus the user's deadline, clamped to 7–30, for recommendations).

No complex model (exponential smoothing, gradient boosting, LSTM…) is used: none has been shown to beat
these baselines on this data. Any future model must pass the same backtest.

## Target-price probability

`P(min price over the next N days ≤ target)` is estimated from how often the same *relative* drop
(`target / current`) happened historically over N-day windows (only fully known windows, last 2 years).
Overlapping windows are not independent, so the effective sample size is `windows / N`; fewer than 8
effective samples → no probability. Results are rounded to 5 % and shown as bands (very unlikely …
very likely).

**Validation**: a Brier-score backtest (5 % drop event) compares the method with a constant base-rate
guess. If it does not beat the base rate (currently the case for product 1), the probability is marked
`skill_demonstrated: false` and recommendation confidence is lowered.

## Outcome tracking

`evaluate_due_forecasts` scores a forecast once a price has been recorded after `base_timestamp + horizon`:
actual = last recorded price at or before that moment (≤ 30 days old), error, interval hit, target reached.
`scripts/evaluate_forecasts.py` prints MAE and coverage by horizon and method. These are backtests and
forecast scores, **not** measured user savings.

## Seasonality (`forecasting/seasonality.py`)

* Event registry: `config/sale_events.yaml` + sale bands observed on source charts. Duplicate records of
  the same sale are merged; events without an end date are not analysed; future events are not history.
* Window: 14 days before · event · 14 days after (configurable). Reported per event: median before,
  minimum and median during, median after, % change, saving at the minimum, discount days, recovery,
  percentile of the event low in the prior 90 days, and how many prices were actually recorded in the window.
* Windows with fewer than 2 recorded prices are shown but excluded from summaries.
* A pattern needs ≥ 3 comparable events. "Unusually large" compares the event change with same-length
  non-event windows (≥ 20 windows, also requiring recorded prices).
* Calendar patterns use recorded prices divided by a 61-day rolling median. Day-of-week and month-end:
  Kruskal–Wallis / Mann–Whitney, p < 0.05. Month: needs ≥ 2 years of history **and** ≥ 6 calendar months
  observed in ≥ 2 different years.
* Upcoming events are labelled by verification status; only `confirmed_official` or `reported_by_media`
  events with a supporting history (≥ 3 past events, ≥ 60 % with drops) can make the policy say WAIT.

## Recommendation policy (`recommendations/policies.py`)

Order of rules: no price → INSUFFICIENT_DATA; at/below target (or budget) → BUY_NOW; deadline ≤ 3 days
→ BUY_NOW within budget, else MONITOR; insufficient history → MONITOR (with target) / INSUFFICIENT_DATA;
with target: probability ≥ 60 % → WAIT, 20–60 % or supported event → BUY_IF_TARGET_REACHED, < 20 % →
BUY_NOW if today's price is in the cheapest 30 % of the year, else MONITOR; without target: cheapest 20 %
→ BUY_NOW, supported upcoming sale and expensive today → WAIT, forecast interval wholly below today →
WAIT, else MONITOR. With vendor analysis, BUY_NOW becomes COMPARE_VENDORS when vendor prices differ by
> 2 %. Expected savings are either forecast-based or probability-weighted and always carry an explanation.
The policy never assigns a monetary value to the user's time or urgency.
