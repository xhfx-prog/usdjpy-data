"""USD/JPY collection: 10-day minute, 370-day hourly, 10-year daily archive.

Runtime hourly or on-demand. Daily and hourly archives are updated incrementally.
Data coverage manifests report what actually exists, not the configured target.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
JST = ZoneInfo("Asia/Tokyo")
NY = ZoneInfo("America/New_York")
NOW = datetime.now(UTC)
DATA = Path("data")
MINS = DATA / "history_1min"
HOURS = DATA / "history_1h"
DAYS = DATA / "history_1day"
for folder in (DATA, MINS, HOURS, DAYS):
    folder.mkdir(exist_ok=True, parents=True)

MIN_RETENTION_DAYS = 10
HOUR_RETENTION_DAYS = 370
DAY_RETENTION_DAYS = 3653
STATE_PATH = DATA / "archive_state.json"


def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def values(path):
    return read(path).get("data", {}).get("values", [])


def store(path, obj):
    encoded = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == encoded:
        return
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(encoded, encoding="utf-8")


def is_closed(ts):
    ny = ts.astimezone(NY)
    return ny.weekday() == 5 or (ny.weekday() == 4 and ny.hour >= 17) or (ny.weekday() == 6 and ny.hour < 17)


def parse_jst(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)


def normalize(rows, interval):
    unique = {}
    for row in rows:
        try:
            o, h, l, c = [float(row[k]) for k in ("open", "high", "low", "close")]
            if not (0 < l <= o <= h and l <= c <= h):
                continue
            stamp = row["datetime"]
            if interval == "1day":
                # Daily labels use provider exchange time; discard the current day
                # because it is not a confirmed close.
                if date.fromisoformat(stamp[:10]) >= NOW.astimezone(JST).date():
                    continue
            else:
                dt = parse_jst(stamp)
                if is_closed(dt.astimezone(UTC)):
                    continue
                if dt + timedelta(minutes=1 if interval == "1min" else 60) > NOW.astimezone(JST):
                    continue
            unique[stamp] = {k: row[k] for k in ("datetime", "open", "high", "low", "close")}
        except (ValueError, KeyError, TypeError):
            continue
    return [unique[k] for k in sorted(unique, reverse=True)]


def payload(interval, rows, **extra):
    return {
        "source": "Twelve Data", "symbol": "USD/JPY", "interval": interval,
        "timezone": "Exchange timezone" if interval == "1day" else "Asia/Tokyo",
        "fetched_at": NOW.isoformat(), **extra,
        "data": {"status": "ok", "values": rows}
    }


def fetch(interval, outputsize=None, end_date=None, start_date=None):
    params = {
        "symbol": "USD/JPY", "interval": interval,
        "timezone": "Asia/Tokyo", "apikey": os.environ["TWELVE_API_KEY"]
    }
    if outputsize is not None:
        params["outputsize"] = outputsize
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    url = "https://api.twelvedata.com/time_series?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "USDJPY-Data-Collector"})
    with urllib.request.urlopen(req, timeout=40) as response:
        result = json.load(response)
    if result.get("status") != "ok" or not result.get("values"):
        raise RuntimeError(f"{interval} feed failed: {result.get('message', 'missing OHLC')}")
    rows = normalize(result["values"], interval)
    if not rows:
        raise RuntimeError(f"{interval} feed: no confirmed candles")
    return rows


def due(last_timestamp, hours):
    try:
        return NOW - datetime.fromisoformat(last_timestamp.replace("Z", "+00:00")) >= timedelta(hours=hours)
    except (AttributeError, ValueError, TypeError):
        return True


def archive(interval, root, rows, name_fn, cutoff):
    grouped = {}
    for bar in rows:
        if bar["datetime"][:10] < cutoff:
            continue
        grouped.setdefault(name_fn(bar["datetime"]), []).append(bar)
    for name, batch in grouped.items():
        path = root / (name + ".json")
        prior = normalize(values(path), interval)
        merged = normalize(prior + batch, interval)
        merged = [b for b in merged if b["datetime"][:10] >= cutoff]
        if merged != prior:
            store(path, payload(interval, merged, partition=name))
    for path in root.glob("*.json"):
        old = normalize(values(path), interval)
        kept = [b for b in old if b["datetime"][:10] >= cutoff]
        if not kept:
            path.unlink()
        elif kept != old:
            store(path, payload(interval, kept, partition=path.stem))


def manifest(root, interval, split):
    result = []
    for path in sorted(root.glob("*.json"), reverse=True):
        rows = values(path)
        if rows:
            result.append({
                split: path.stem, "path": str(path), "count": len(rows),
                "first": rows[-1]["datetime"], "last": rows[0]["datetime"]
            })
    store(DATA / ("history_" + interval + "_index.json"), {
        "updated_at": NOW.isoformat(), "interval": interval,
        "retention_calendar_days": {"1min": 10, "1h": 370, "1day": 3653}[interval],
        "sessions" if interval == "1min" else "partitions": result,
        "actual_oldest": result[-1]["first"] if result else None,
        "actual_newest": result[0]["last"] if result else None,
        "total_bars": sum(item["count"] for item in result)
    })


if is_closed(NOW):
    print("FX weekend: no API requests")
    raise SystemExit(0)

state = read(STATE_PATH)
errors = []
today = NOW.astimezone(JST).date()
minute_cutoff = (today - timedelta(days=MIN_RETENTION_DAYS)).isoformat()
hour_cutoff = (today - timedelta(days=HOUR_RETENTION_DAYS)).isoformat()
day_cutoff = (today - timedelta(days=DAY_RETENTION_DAYS)).isoformat()

# Every run fetches recent 1-minute candles and fills missed intervals.
previous = normalize(values(DATA / "usdjpy_1min.json"), "1min")
gap = (NOW.astimezone(JST) - parse_jst(previous[0]["datetime"])).total_seconds()/60 if previous else 100000
recent = fetch("1min", 1500 if gap > 150 else 180)
combined = normalize(previous + recent, "1min")
if (NOW.astimezone(JST) - parse_jst(combined[0]["datetime"])) > timedelta(minutes=35):
    raise RuntimeError("Minute feed too stale; no data persisted")
archive("1min", MINS, combined, lambda s: s[:10], minute_cutoff)
store(DATA / "usdjpy_1min.json", payload("1min", combined[:1500]))
manifest(MINS, "1min", "date_jst")
print("1min refreshed:", combined[0]["datetime"])

# 1-hour candles: rolling 370-day archive, partitioned by calendar month.
# Bootstrap requests up to 5000 + 2200 older bars once, as supported by provider.
hourly_last = read(DATA / "usdjpy_1h.json")
if not state.get("hourly_bootstrap_completed") or due(hourly_last.get("fetched_at"), 1):
    try:
        if not state.get("hourly_bootstrap_completed"):
            batch = fetch("1h", 5000)
            archive("1h", HOURS, batch, lambda s: s[:7], hour_cutoff)
            oldest = batch[-1]["datetime"]
            if oldest[:10] > hour_cutoff:
                older_end = (parse_jst(oldest) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
                older = fetch("1h", 2200, end_date=older_end)
                archive("1h", HOURS, older, lambda s: s[:7], hour_cutoff)
            state["hourly_bootstrap_completed"] = True
        else:
            batch = fetch("1h", 200)
            archive("1h", HOURS, batch, lambda s: s[:7], hour_cutoff)
        # Small compatibility feed for ordinary analysis, do not load full year.
        store(DATA / "usdjpy_1h.json", payload("1h", batch[:200]))
    except Exception as exc:
        errors.append("1h: " + str(exc))
else:
    print("1h refresh not yet due")
# Historical range endpoints fill any gap left by outputsize-only bootstrap.
# A one-time result (including provider rejection) is recorded to prevent an
# expensive repeated request on every hourly/on-demand invocation.
if state.get("hourly_bootstrap_completed") and not state.get("hourly_tail_probe_completed"):
    months = sorted(HOURS.glob("*.json"))
    oldest_bar = next((values(p)[-1]["datetime"] for p in months if values(p)), None)
    if oldest_bar and oldest_bar[:10] > hour_cutoff:
        try:
            start = hour_cutoff + " 00:00:00"
            end = (parse_jst(oldest_bar) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            extension = fetch("1h", start_date=start, end_date=end)
            archive("1h", HOURS, extension, lambda s: s[:7], hour_cutoff)
            state["hourly_tail_probe_returned_bars"] = len(extension)
        except Exception as exc:
            state["hourly_tail_probe_error"] = str(exc)
            errors.append("1h historical tail: " + str(exc))
    state["hourly_tail_probe_completed"] = True
archive("1h", HOURS, [], lambda s: s[:7], hour_cutoff)
manifest(HOURS, "1h", "month_jst")

# Daily history is a separate event-study archive: once per day max,
# newest 7 candles only after initial 10-year bootstrap; never repeatedly
# overwrite historical years. Provider exchange-day labels are preserved.
daily_now_jst = NOW.astimezone(JST)
daily_date_jst = daily_now_jst.date().isoformat()
last_daily_attempt = state.get("daily_last_attempt_utc")
last_daily_attempt_jst = (
    datetime.fromisoformat(last_daily_attempt.replace("Z", "+00:00")).astimezone(JST).date().isoformat()
    if last_daily_attempt else None
)
if daily_now_jst.hour >= 8 and last_daily_attempt_jst != daily_date_jst:
    state["daily_last_attempt_utc"] = NOW.isoformat()
    try:
        if not state.get("daily_bootstrap_completed"):
            daily_batch = fetch("1day", 3500)
            state["daily_bootstrap_completed"] = True
        else:
            daily_batch = fetch("1day", 7)
        archive("1day", DAYS, daily_batch, lambda s: s[:4], day_cutoff)
        state["daily_last_success_utc"] = NOW.isoformat()
        recent_daily = normalize(values(DATA / "usdjpy_1day.json") + daily_batch, "1day")
        # Preserve legacy 200-bar daily view without frequent remote requests.
        store(DATA / "usdjpy_1day.json", payload("1day", recent_daily[:200]))
    except Exception as exc:
        errors.append("1day: " + str(exc))
archive("1day", DAYS, [], lambda s: s[:4], day_cutoff)
manifest(DAYS, "1day", "year_exchange")
state["last_run_utc"] = NOW.isoformat()
state["errors"] = errors
store(STATE_PATH, state)

for obsolete in ("5min", "15min", "4h"):
    (DATA / ("usdjpy_" + obsolete + ".json")).unlink(missing_ok=True)
if errors:
    print("Archive warnings:", errors)
print("USDJPY update complete")
