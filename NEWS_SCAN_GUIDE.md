# USD/JPY sudden-move NEWS-FIRST investigation (on demand)

This repository has a functioning global-news search program, not a post-hoc USDJPY-recap search.

## Usage

1. Fetch latest data/usdjpy_1min.json and identify move onset in JST using confirmed minute candles.
2. Fetch control/news_scan_request.json and its blob SHA. Call GitHub update_file with unique request_id and move_at_jst (e.g. 2026-11-03T22:30:00+09:00), pre_minutes (30-60), post_minutes (0-15, context only), and optional hints.
3. Push triggers .github/workflows/news_investigation.yml. It runs regression tests and investigate_world_news.py. Search terms are world news (geopolitics, energy, policy, economy); never "USDJPY fell why".
4. Fetch data/news_trigger_latest.json only after workflow completion, verify matching request_id and scan time window. Inspect news_sources for errors.
5. SOURCE_TIMESTAMP_BEFORE_MOVE_EVENT_TIME_UNVERIFIED is a lead requiring verification; SAME_MINUTE_ORDER_UNKNOWN is ambiguous; POST_MOVE_CANNOT_BE_TRIGGER_FROM_THIS_TIMESTAMP cannot be evidence of the initial trigger. Never conflate article publication, GDELT indexing and actual event time.
6. Check primary reporting or official announcement, oil, US yields, DXY and equity futures independently; no cross-asset stream is currently connected and the JSON indicates NOT_FETCHED. Use FX commentary only after direct candidate search.
7. Investigation results are archived at data/news_investigations/YYYY-MM-DD/ and the latest is at data/news_trigger_latest.json.

GitHub Actions is asynchronous; request submission alone does not confirm completion.
This is event-driven: no permanent monitoring needed.
Google News RSS can lag or omit breaking news. GDELT can rate-limit HTTP 429.
Program ranking is only for investigation priority, NOT causal probability.
The program never automatically confirms that any particular article caused a price move.

## Direct command

python investigate_world_news.py --move-at 2026-11-03T22:30:00+09:00 --pre-minutes 60 --post-minutes 15 --hints Qatar,Iran,Israel --request-id manual-001

## Regression tests

python -m unittest discover -s tests -p 'test_world_news.py' -v
