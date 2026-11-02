
import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

API_KEY = os.environ["TWELVE_API_KEY"]
INTERVALS = ["5min", "15min", "1h", "4h", "1day"]
OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)

for interval in INTERVALS:
    params = urllib.parse.urlencode({
        "symbol": "USD/JPY",
        "interval": interval,
        "outputsize": 200,
        "timezone": "Asia/Tokyo",
        "apikey": API_KEY
    })

    url = "https://api.twelvedata.com/time_series?" + params

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "USDJPY-Data-Collector"}
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)

    if result.get("status") != "ok" or not result.get("values"):
        raise RuntimeError(
            f"Data retrieval failed for {interval}: "
            f"{result.get('message', 'Invalid response')}"
        )

    data = {
        "source": "Twelve Data",
        "symbol": "USD/JPY",
        "interval": interval,
        "timezone": (
            "Exchange timezone"
            if interval == "1day"
            else "Asia/Tokyo"
        ),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "data": result
    }

    output_file = OUTPUT_DIR / f"usdjpy_{interval}.json"
    output_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"Saved {interval}: {len(result['values'])} candles")
