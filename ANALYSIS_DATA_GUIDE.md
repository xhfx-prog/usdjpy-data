# USD/JPY OHLC storage and analysis guide

This repository separates **archival coverage** from **the minimum data needed for a normal analysis**.

## Acquisition

- `.github/workflows/update.yml`: **hourly archival collection at minute 11 (UTC/JST)** plus on-demand refresh using a push to `control/refresh_request.json`; optional `workflow_dispatch`. Both modes use `fetch_usdjpy.py` to merge/deduplicate new candles into historical files. Reading a GitHub file (`GET`/`fetch_file`) does **not** trigger Actions.
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

## Mandatory on-demand freshness gate (all interactive USD/JPY analyses)

1. Read `data/usdjpy_1min.json` to establish the last completed minute candle and `fetched_at`. A normal read does not request new Twelve Data observations.
2. If the requested time window extends beyond the current dataset (or if the newest candle is more than 10 minutes older than the analysis time), fetch `control/refresh_request.json`, then update it with its current blob SHA and a unique `request_id`. This push starts the data acquisition workflow. **Do not stop after the first fetch or report stale bars as current.**
3. Re-read `data/workflow_status.json` until `latest.event=push`, `latest.refresh_request_id` matches the submitted id, and `fetch_outcome=success` and `job_status_before_publish=success`. Then re-read `data/usdjpy_1min.json` and confirm that `fetched_at` and newest candle advanced and the candle range covers the question. If workflow execution is delayed/failed, explicitly state its state; do not claim refresh success based on the request commit alone.
4. Before answering, recheck the newest bar against the question time. When data remain stale, state the precise missing range rather than constructing a current price narrative from stale information.
5. Do not issue repeated refresh requests for one user question while an existing request is in flight.

**Why both modes exist:** Scheduled hourly runs preserve minute/hour/day archives even when there are no ChatGPT questions. Question-time push runs fill the gap since the most recent scheduled run. Both write to the same deduplicated history. The 09:00 ChatGPT analysis must trigger/verify its own refresh when data are stale; the scheduled cadence alone cannot guarantee fresh data.

## Analysis reading order

1. **Every question about near-term USD/JPY**: fetch newest minute JSON, examine `fetched_at` and last completed candle. Trigger on-demand refresh when more than 10 minutes stale **or when the question requests later candles**; never substitute third-party pivots for missing candles.
2. **Classify context**: read 200 recent hourly candles, supplement previous monthly hourly partitions if structural S/Rs or range breakout require it. Construct 4-hour bars from hourly if needed.
3. **Ranging market**: use recent 1-min and corresponding daily minute archive as needed to assemble 5/15-min candles; identify repeated reversals, middle, limits, breadth, and breakout/retest.
4. **Trending market**: focus 1h/4h structure, major swing highs/lows, overlapping S/R, momentum; widen hourly lookback toward 1 year if major levels are breached.
5. **Historical policy, election, or crisis events**: first fetch `data/history_1day_index.json`, then only the relevant `data/history_1day/YYYY.json` files and event timestamps. Daily history is **not read routinely** for every short-horizon question.
6. **Market news / indicators**: verify exact JST/ET timestamps and relate confirmed price reactions; state separate 3h / 24h / 72h projections only when requested and supported by OHLC.
7. **Decision**: identify trade setup, available upside to first resistance, invalidation, and no-trade conditions. Do not force trades in trends or ranges.

No single timeframe establishes support/resistance purely by high/low extremes. Confirm independent reactions, retests, and higher timeframe relevance.

## Maintenance and audit

- `data/workflow_status.json` contains per-run `event`, `fetch_outcome`, and request id. `data/workflow_runs_audit.json` audits GitHub Actions execution history.
- Scheduled GitHub jobs may be delayed/skipped, and on-demand Actions jobs may be delayed or fail. A successful update never guarantees freshness for subsequent questions.
- Archives are merged/deduplicated by timestamp, and old partitions are trimmed as they exceed the retention horizon.
- The daily archive is initially fetched once and is supplemented by a small number of recent confirmed daily candles at most once per 24 hours. Old years are not re-fetched.
- `data/usdjpy_1day.json` is a compact, backward-compatible 200-bar view, not the historical source of truth.
