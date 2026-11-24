import datetime as dt
import json
import pathlib
import urllib.request
import urllib.parse
from html.parser import HTMLParser

BASE="https://www.bny.com/corporate/global/en/solutions/platforms/execution-services/iflow.html"
EBS="https://www.cmegroup.com/markets/ebs/fx-market-profile.html"
OPTIONS="https://www.cmegroup.com/markets/fx/g10/japanese-yen/quotes/options"
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
def fetch(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read(4000001)
        if len(raw)>4000000: raise ValueError("Page exceeds size limit")
        return raw.decode("utf-8",errors="replace")
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
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
    print(payload["bny"]["status"],len(payload["bny"]["reports"]))
if __name__=="__main__": main()
