import datetime as dt
import json
import pathlib
import re
import subprocess
import tempfile
import urllib.request

URL = "https://www.cmegroup.com/daily_bulletin/current/Section01B_Summary_Volume_And_Open_Interest_FX_Futures_And_Options.pdf"
OUT = pathlib.Path("data/cme_jpy_daily.json")

def parse(text):
    dates = re.findall(r"\b(?:Mon|Tue|Wed|Thu|Fri),\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})", text)
    if not dates:
        raise ValueError("Bulletin trade date missing")
    day = dt.datetime.strptime(dates[0], "%b %d, %Y").date().isoformat()
    head = text.split("FX FUTURES AND OPTIONS")[0]
    status = re.search(r"\b(FINAL|PRELIMINARY)\b", text)
    if not status:
        raise ValueError("Bulletin status missing")
    rows = re.findall(r"^\s*JY\s+JAPANESE YEN FUTURE[S]?\s+(.+)$", text, re.M)
    if len(rows) != 1:
        raise ValueError("Expected exactly one standard yen futures row")
    row = rows[0].replace(",", "")
    # Three optional exchange-volume columns precede overall volume and OI.
    m = re.fullmatch(r"\s*([\d\s]+?)\s+([\d]+)\s+([\d]+)\s+([+-])\s*(\d+)\s+(\d+)\s+(\d+)\s*", row)
    if not m:
        raise ValueError("Unrecognized row layout: " + row)
    components, volume, oi, sign, change, _, _ = m.groups()
    volume, oi, change = int(volume), int(oi), int(change) * (1 if sign == "+" else -1)
    if sum(map(int, components.split())) != volume or oi <= 0 or oi-change <= 0:
        raise ValueError("Volume/OI validation failed")
    return {"trade_date":day,"status":status.group(1),"volume":volume,"open_interest":oi,"oi_change":change,"source":URL}

def self_test():
    sample = "FINAL\n Fri, Nov 13, 2026 PG01B\nJY JAPANESE YEN FUTURE 182279 1981 184260 385761 - 1377 169305 320949\n"
    x = parse(sample)
    assert (x["volume"],x["open_interest"],x["oi_change"]) == (184260,385761,-1377)
    try:
        parse(sample.replace("184260","184261"))
    except ValueError:
        return
    raise AssertionError("Bad volume accepted")

def main():
    self_test()
    with tempfile.TemporaryDirectory() as tmp:
        pdf = pathlib.Path(tmp)/"bulletin.pdf"
        req = urllib.request.Request(URL, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read(20_000_000)
        if not raw.startswith(b"%PDF"):
            raise ValueError("Not a PDF")
        pdf.write_bytes(raw)
        text = subprocess.check_output(["pdftotext","-f","1","-l","1","-layout",str(pdf),"-"],text=True)
    item = parse(text)
    now = dt.datetime.now(dt.timezone.utc)
    age = (now.date()-dt.date.fromisoformat(item["trade_date"])).days
    if age < -1 or age > 7:
        raise ValueError("Stale or invalid bulletin date")
    item["retrieved_at_utc"] = now.isoformat()
    old = json.loads(OUT.read_text()) if OUT.exists() else {"history":[]}
    records = {r["trade_date"]:r for r in old["history"]}
    prev = records.get(item["trade_date"])
    if not prev or prev["status"] != "FINAL" or item["status"] == "FINAL":
        if not prev or any(prev.get(k)!=item[k] for k in ("status","volume","open_interest","oi_change")):
            records[item["trade_date"]] = item
    history = sorted(records.values(),key=lambda x:x["trade_date"])[-400:]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"schema_version":1,"product":"CME standard Japanese yen futures (JY/6J), all maturities","latest":history[-1],"history":history},indent=2)+"\n")
    print(json.dumps(history[-1]))

if __name__ == "__main__":
    main()
