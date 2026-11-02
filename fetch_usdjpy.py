
import os
import json
import urllib.request
import urllib.parse

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


# Twelve Data API key
API_KEY = os.environ["TWELVE_API_KEY"]

# Fetch only essential timeframes
INTERVALS = {
    "1min": 1500,
    "1h": 200,
    "1day": 200,
}

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)

JST = ZoneInfo("Asia/Tokyo")
NY = ZoneInfo("America/New_York")


def market_is_closed(dt_utc):
    """Check regular FX weekend closure in New York time."""
    ny = dt_utc.astimezone(NY)
    day = ny.weekday()
    hour = ny.hour

    return (
        (day == 4 and hour >= 17)
        or day == 5
        or (day == 6 and hour < 17)
    )


def valid_ohlc(candle):
    """Check candle price consistency."""
    try:
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        c = float(candle["close"])
        return (
            0 < l <= h
            and l <= o <= h
            and l <= c <= h
        )
    except (KeyError, TypeError, ValueError):
        return False


def is_weekend_candle(candle, interval):
    """Remove weekend candles using New York FX hours."""
    if interval == "1day":
        # Daily candle boundaries depend on the data provider.
        return False

    dt = datetime.strptime(
        candle["datetime"], "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=JST)

    return market_is_closed(dt.astimezone(timezone.utc))


now_utc = datetime.now(timezone.utc)

if market_is_closed(now_utc):
    print("FX market closed: skip API requests")
    raise SystemExit(0)


for interval, outputsize in INTERVALS.items():

    params = urllib.parse.urlencode({
        "symbol": "USD/JPY",
        "interval": interval,
        "outputsize": outputsize,
        "timezone": "Asia/Tokyo",
        "apikey": API_KEY,
    })

    url = "https://api.twelvedata.com/time_series?" + params

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "USDJPY-Data-Collector"}
    )

    with urllib.request.urlopen(
        request, timeout=30
    ) as response:
        result = json.load(response)

    if result.get("status") != "ok":
        raise RuntimeError(
            f"{interval}: "
            f"{result.get('message', 'API error')}"
        )

    values = result.get("values", [])

    values = [
        candle
        for candle in values
        if valid_ohlc(candle)
        and not is_weekend_candle(candle, interval)
    ]

    if not values:
        raise RuntimeError(
            f"{interval}: No valid candles"
        )

    # Exclude the currently forming intraday candle.
    if interval != "1day":
        minutes = 1 if interval == "1min" else 60
        now_jst = now_utc.astimezone(JST)

        confirmed = []
        for candle in values:
            start = datetime.strptime(
                candle["datetime"],
                "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=JST)

            from datetime import timedelta
            if start + timedelta(minutes=minutes) <= now_jst:
                confirmed.append(candle)

        values = confirmed

    if not values:
        raise RuntimeError(
            f"{interval}: No confirmed candles"
        )

    result["values"] = values

    data = {
        "source": "Twelve Data",
        "symbol": "USD/JPY",
        "interval": interval,
        "timezone": (
            "Exchange timezone"
            if interval == "1day"
            else "Asia/Tokyo"
        ),
        "fetched_at": now_utc.isoformat(),
        "data": result,
    }

    output_file = (
        OUTPUT_DIR / f"usdjpy_{interval}.json"
    )

    output_file.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print(
        f"Saved {interval}: {len(values)} candles"
    )

print("USD/JPY update completed")
