# USD/JPY OHLC storage and analysis guide

This repository separates **archival coverage** from **the minimum data needed for a normal analysis**.

## Acquisition

- `.github/workflows/update.yml`: hourly cron at minute 11 (UTC and JST share minute values); on-demand refresh via pushes of `control/refresh_request.json`; optional `workflow_dispatch`.
- `fetch_usdjpy.py`: Twelve Data collector. Minute series every actual run, hourly series when at least 1 hour old, exchange-calendar daily series once every 24 hours. Data calls and GitHub file reads are distinct.
- A new `request_id` in `control/refresh_request.json` triggers `push`. Verify the same `refresh_request_id`, `event=push`, `fetch_outcome=success` in `data/workflow_status.json`, and confirm `fetched_at` and newest `datetime` in `data/usdjpy_1min.json` advanced. A submitted request alone does not confirm freshness.

## Files and coverage

| Data | Recent view | Archive index | Archive partitions | Retention |
| --- | --- | --- | --- | --- |
| 1 minute | `data/usdjpy_1min.json` (up to 1500 bars) | `data/history_1min_index.json` | `data/history_1min/YYYY-MM-DD.json` | 10 calendar days |
| 1 hour | `data/usdjpy_1h.json` (up to 200 bars) | `data/history_1h_index.json` | `data/history_1h/YYYY-MM.json` | 370 calendar days |
| Daily | `data/usdjpy_1day.json` (up to 200 bars) | `data/history_1day_index.json` | `data/history_1day/YYYY.json` | 3653 calendar days |

The year/month/day retention is a target ceiling, **not a promise that every hour or day is available**. Read `actual_oldest`, `actual_newest`, `total_bars` and the partitions before claiming coverage. Archive bootstrap state, API errors, and retries appear in `data/archive_state.json`.

Hourly bars are stored in **Asia/Tokyo**. Daily bars retain **Twelve Data exchange-day labels**; do not assume they are New York 17:00 close bars. Avoid combining the two calendars without reconciliation.

## Analysis reading order

1. **Every question about near-term USD/JPY**: fetch newest minute JSON, examine `fetched_at` and last completed candle. Trigger on-demand refresh when more than 10 minutes stale; never substitute third-party pivots for missing candles.
2. **Classify context**: read 200 recent hourly candles, supplement previous monthly hourly partitions if structural S/Rs or range breakout require it. Construct 4-hour bars from hourly if needed.
3. **Ranging market**: use recent 1-min and corresponding daily minute archive as needed to assemble 5/15-min candles; identify repeated reversals, middle, limits, breadth, and breakout/retest.
4. **Trending market**: focus 1h/4h structure, major swing highs/lows, overlapping S/R, momentum; widen hourly lookback toward 1 year if major levels are breached.
5. **Historical policy, election, or crisis events**: first fetch `data/history_1day_index.json`, then only the relevant `data/history_1day/YYYY.json` files and event timestamps. Daily history is **not read routinely** for every short-horizon question.
6. **Market news / indicators**: verify exact JST/ET timestamps and relate confirmed price reactions; state separate 3h / 24h / 72h projections only when requested and supported by OHLC.
7. **Decision**: identify trade setup, available upside to first resistance, invalidation, and no-trade conditions. Do not force trades in trends or ranges.

No single timeframe establishes support/resistance purely by high/low extremes. Confirm independent reactions, retests, and higher timeframe relevance.

## Maintenance and audit

- `data/workflow_status.json` contains per-run `event`, `fetch_outcome`, and request id. `data/workflow_runs_audit.json` audits GitHub Actions execution history.
- GitHub scheduled jobs may be delayed or skipped. A successful update never guarantees future punctuality.
- Archives are merged/deduplicated by timestamp, and old partitions are trimmed as they exceed the retention horizon.
- The daily archive is initially fetched once and is supplemented by a small number of recent confirmed daily candles at most once per 24 hours. Old years are not re-fetched.
- `data/usdjpy_1day.json` is a compact, backward-compatible 200-bar view, not the historical source of truth.
