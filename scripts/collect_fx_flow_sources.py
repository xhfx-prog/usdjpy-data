import csv
import datetime as dt
import re
import json
import pathlib
import urllib.request
import urllib.parse
from html.parser import HTMLParser

BASE="https://www.bny.com/corporate/global/en/solutions/platforms/execution-services/iflow.html"
EBS="https://www.cmegroup.com/markets/ebs/fx-market-profile.html"
OPTIONS="https://www.cmegroup.com/markets/fx/g10/japanese-yen/quotes/options"
TFX = "https://www.tfx.co.jp/en/historical/fx/"
TFX_CSV = r"/kawase/document/PRT-010-CSV-003-(\d{8})\.CSV"
OUT=pathlib.Path("data/fx_flow_sources.json")
class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links={}; self.href=None; self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag=="a": self.href=dict(attrs).get("href"); self.parts=[]
    def handle_data(self,data):
        if self.href: self.parts.append(data)
    def handle_endtag(self,tag):
        if tag=="a" and self.href:
            url=urllib.parse.urljoin(BASE,self.href)
            title=" ".join(" ".join(self.parts).split())
            if urllib.parse.urlsplit(url).hostname=="www.bny.com" and "/iflow/" in url and title:
                self.links[url]=title
            self.href=None
def fetch(url, encoding="utf-8"):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read(4000001)
        if len(raw)>4000000: raise ValueError("Page exceeds size limit")
        return raw.decode(encoding, errors="strict")
def collect_tfx():
    """Read the latest published Click 365 USD/JPY daily exchange statistics."""
    links = {match[2]: match[1] for match in re.finditer(
        rf'href="({TFX_CSV})"', fetch(TFX), re.IGNORECASE
    )}
    if not links:
        raise ValueError("No daily CSV links on TFX historical data page")
    trade_date = max(links)
    report_path = links[trade_date]
    url = urllib.parse.urljoin(TFX, report_path)
    rows = list(csv.reader(fetch(url, encoding="cp932").splitlines()))
    if not rows or rows[0][:2] != ["H00", "PRT-010-CSV-003"] or rows[0][3:4] != [trade_date]:
        raise ValueError("Unexpected TFX report header or trade date")
    matches = [row for row in rows if row[:4] == ["D01", trade_date, "1", "USD/JPY"]]
    if len(matches) != 1 or len(matches[0]) != 19:
        raise ValueError("Unexpected TFX USD/JPY daily row")
    volume, open_interest = map(int, matches[0][-2:])
    if min(volume, open_interest) < 0:
        raise ValueError("Negative TFX volume or open interest")
    return {
        "source": url,
        "status": "daily_exchange_statistics",
        "market": "TFX Click 365",
        "symbol": "USD/JPY",
        "trade_date": dt.datetime.strptime(trade_date, "%Y%m%d").date().isoformat(),
        "volume": volume,
        "open_interest": open_interest,
    }

def main():
    payload={"checked_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
             "bny":{"source":BASE,"status":"not_checked","reports":[]},
             "ebs":{"source":EBS,"status":"numeric_feed_not_configured","volume":None,"spread":None},
             "options":{"source":OPTIONS,"status":"numeric_feed_not_configured","risk_reversal":None,"cvol":None}}
    try:
        p=Links(); p.feed(fetch(BASE))
        reports=[{"url":u,"title":t} for u,t in p.links.items() if u.endswith(".html") and u.count("/")>=11]
        payload["bny"].update(status="links_found" if reports else "no_report_links",reports=reports[:80])
    except Exception as e:
        payload["bny"].update(status="fetch_failed",error=str(e))
    try:
        payload["tfx"] = collect_tfx()
    except (OSError, ValueError) as error:
        payload["tfx"] = {"source": TFX, "status": "fetch_failed", "error": str(error)}
    print("TFX:", payload["tfx"])
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
    print(payload["bny"]["status"],len(payload["bny"]["reports"]))
if __name__=="__main__": main()
