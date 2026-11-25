"""Probe public release sources; HTTP success is not release verification."""
import concurrent.futures
import datetime as dt
import json
import urllib.request
import hashlib
import re
from pathlib import Path

SOURCES = {
    "gmo": "https://www.gaikaex.com/gaikaex/mark/calendar/",
    "minkabu": "https://fx.minkabu.jp/indicators",
    "fxstreet": "https://www.fxstreet.com/economic-calendar",
    "investing": "https://www.investing.com/economic-calendar/",
    "bea": "https://www.bea.gov/news/current-releases",
    "dol": "https://www.dol.gov/ui/data.pdf",
    "census": "https://www.census.gov/manufacturing/m3/adv/pdf/durgd.pdf",
}
def probe(item):
    name, url = item
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"USDJPY research source monitor/1.0", "Cache-Control":"no-cache", "Pragma":"no-cache"})
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read(4_000_000)
            return dict(source=name,url=url,fetched_at=now,http_status=response.status,
                server_date=response.headers.get("Date"),age=response.headers.get("Age"),
                last_modified=response.headers.get("Last-Modified"),bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),release_verified=False,
                status="transport_ok_unverified")
    except Exception as exc:
        return dict(source=name,url=url,fetched_at=now,status="error",error=str(exc),release_verified=False)

if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
        results=list(pool.map(probe,SOURCES.items()))
    out=Path("data/economic_source_probe.json")
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(results,ensure_ascii=False,indent=2))
