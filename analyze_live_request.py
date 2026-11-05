#!/usr/bin/env python3
"""Re-evaluate the canonical Python USDJPY engine with externally fetched live 1min bars.

No Twelve Data API call. No changes to permanent OHLC history. This outputs a
separate, timestamped and request-bound analysis of the actual fresh 1min input.
The initiating caller must validate the returned request_id and current age.
"""
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import analyze_usdjpy as engine

JST = engine.JST
ROOT = Path("data")
REQUEST = Path("control/live_analysis_request.json")
OUTPUT = ROOT / "analysis_live_latest.json"
MAX_LIVE_BARS = 2000


def main():
    payload = json.loads(REQUEST.read_text(encoding="utf-8"))
    request_id = str(payload.get("request_id", ""))
    if not (8 <= len(request_id) <= 128 and all(c.isalnum() or c in "-_." for c in request_id)):
        raise ValueError("Invalid request_id")
    if payload.get("source") != "USDJPY Market Data MCP" or payload.get("symbol") != "USD/JPY":
        raise ValueError("Unexpected input source/symbol")
    provider_fetch = datetime.fromisoformat(payload["fetched_at"].replace("Z", "+00:00"))
    if provider_fetch.tzinfo is None:
        raise ValueError("fetched_at must have timezone")
    now = datetime.now(JST)
    fetch_age = (now - provider_fetch.astimezone(JST)).total_seconds()
    if not -30 <= fetch_age <= 600:
        raise ValueError(f"Request too old or in the future: {fetch_age:.0f} seconds")
    raw = payload.get("values")
    if not isinstance(raw, list) or not 10 <= len(raw) <= MAX_LIVE_BARS:
        raise ValueError("Invalid live OHLC count")
    raw_stamps = [x.get("datetime") for x in raw if isinstance(x, dict)]
    if len(raw_stamps) != len(raw) or len(set(raw_stamps)) != len(raw):
        raise ValueError("Missing/duplicate live timestamps")
    live = engine.normalized(raw, now, "1min", max_invalid_fraction=0)
    if len(live) != len(raw):
        raise ValueError("Unconfirmed or invalid live bars")
    last_live = live[-1]
    if not 0 <= (now - engine.stamp(last_live["t"]) - timedelta(minutes=1)).total_seconds() <= 600:
        raise ValueError("Newest confirmed live bar too old/new")
    minute, hourly, daily, source, minute_idx, hour_idx = engine.load_market(ROOT, now)
    merged = {x["t"]: x for x in minute}
    for b in live:
        merged[b["t"]] = b
    minute = [merged[k] for k in sorted(merged)]
    report = engine.evaluate(minute, hourly, daily, now, sources={"fetched_at": payload["fetched_at"]})
    actual_last = report["data_quality"]["last_confirmed_1min_start_jst"]
    if actual_last != last_live["t"]:
        raise ValueError("Re-evaluation did not reach the live newest confirmed candle")
    completed_age_seconds = (now - engine.stamp(last_live["t"]) - timedelta(minutes=1)).total_seconds()
    result = {
        "request_id": request_id,
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_live_fetched_at": payload["fetched_at"],
        "last_confirmed_1min_start_jst": last_live["t"],
        "age_at_compute_seconds": round(completed_age_seconds, 2),
        "strict_freshness_ok_at_compute": completed_age_seconds < 120,
        "recheck_freshness_when_read": True,
        "minute_input_bars": len(live),
        "total_merged_minute_bars": len(minute),
        "python_engine": "analyze_usdjpy.py",
        "recomputed_from_live_1min": True,
        "analysis": report,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Recomputed Python analysis:", request_id, "last=", last_live["t"],
          "phase=", report["market_structure"]["phase"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
