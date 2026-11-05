"""Run the existing deterministic Python analysis engine on current MCP OHLC."""
from datetime import datetime, timedelta
import analyze_usdjpy as engine

def analyze_payload(payload, now=None):
    if payload.get("symbol") != "USD/JPY":
        raise ValueError("WRONG_SYMBOL")
    if payload.get("source") != "USDJPY_MCP_FRESH_BUNDLE":
        raise ValueError("INVALID_SOURCE")
    now = (now or datetime.now(engine.JST)).astimezone(engine.JST)
    minutes = payload.get("1min", [])
    hours = payload.get("1h", [])
    days = payload.get("1day", [])
    if not (isinstance(minutes,list) and isinstance(hours,list) and isinstance(days,list)):
        raise ValueError("INVALID_SERIES")
    if not (150 <= len(minutes) <= 6500 and 100 <= len(hours) <= 2000 and 60 <= len(days) <= 2000):
        raise ValueError("INSUFFICIENT_HISTORY")
    minute = engine.normalized(minutes, now, "1min", max_invalid_fraction=0.015)
    hourly = engine.normalized(hours, now, "1h", max_invalid_fraction=0.015)
    daily = [b for b in engine.normalized(days, now, "1day", max_invalid_fraction=0.015)
             if datetime.fromisoformat(b["t"]).weekday()<5]
    if not minute:
        raise ValueError("NO_CONFIRMED_1MIN")
    latest = minute[-1]["t"]
    if payload.get("latest_confirmed_1min_bar") != latest:
        raise ValueError("LATEST_CANDLE_MISMATCH")
    age_seconds = (now-engine.stamp(latest)-timedelta(minutes=1)).total_seconds()
    if age_seconds < -1 or age_seconds > 120:
        raise ValueError("STALE_1MIN_AT_PYTHON_COMPUTE")
    report = engine.evaluate(minute, hourly, daily, now,
                             sources={"fetched_at":payload.get("fetched_at")})
    if report["data_quality"]["last_confirmed_1min_start_jst"] != latest:
        raise ValueError("PYTHON_RESULT_LAST_CANDLE_MISMATCH")
    return {
        "symbol": "USD/JPY",
        "python_engine": "analyze_usdjpy.py",
        "execution": "CLOUDFLARE_PYTHON_WORKER_INLINE",
        "full_python_engine_recomputed": True,
        "analysis_input_latest_confirmed_1min_start_jst": latest,
        "age_seconds_at_compute": round(age_seconds,3),
        "input_bar_counts": {"1min":len(minute),"1h":len(hourly),"1day":len(daily)},
        "analysis":report
    }
