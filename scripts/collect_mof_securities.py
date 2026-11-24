import csv
import datetime as dt
import hashlib
import io
import json
import pathlib
import re
import unicodedata
import urllib.request

URL = "https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/week.csv"
OUT = pathlib.Path("data/mof_securities_weekly.json")

def decode(raw):
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    raise ValueError("Unsupported CSV encoding")

def normalize(rows):
    headers = [r for r in rows if r and r[0].strip()=="期間\nPeriod"]
    if len(headers)!=1 or len(headers[0])!=23:
        raise ValueError("Unknown MOF header layout")
    h = headers[0]
    for i, token in ((1,"株式"),(4,"中長期債"),(8,"短期債"),(11,"合計"),(12,"株式"),(15,"中長期債"),(19,"短期債"),(22,"合計")):
        if token not in re.sub(r"\s+","",h[i]):
            raise ValueError("Unexpected column "+str(i))
    if not any("Unit: 100 million Yen" in c for r in rows for c in r):
        raise ValueError("Unknown units")
    if not any("ネットのプラス" in c and "取得超" in c for r in rows for c in r):
        raise ValueError("Unknown sign convention")
    history=[]
    for r in rows:
        if not r:
            continue
        period=unicodedata.normalize("NFKC",r[0]).replace(" ","")
        m=re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d{1,2})~(\d{1,2})\.(\d{1,2})",period)
        if not m:
            continue
        y,mo,d,em,ed=map(int,m.groups())
        if y<2014:
            continue
        start=dt.date(y,mo,d)
        end=dt.date(y+(em<mo),em,ed)
        if not 0<=(end-start).days<=7 or len(r)!=23:
            raise ValueError("Invalid weekly row")
        def n(i):
            return int(unicodedata.normalize("NFKC",r[i]).strip().replace(",",""))
        groups={}
        for label,offset in (("outbound",0),("inbound",11)):
            v={k:n(i+offset) for k,i in (("equity_net",3),("long_debt_net",6),("short_debt_net",10),("total_net",11))}
            for i in (1,4,8):
                if abs(n(i+offset)-n(i+1+offset)-n(i+2+offset))>2:
                    raise ValueError("Acquisition/disposition mismatch")
            if abs(sum(v[k] for k in ("equity_net","long_debt_net","short_debt_net"))-v["total_net"])>3:
                raise ValueError("Total mismatch")
            groups[label]=v
        history.append({"start":start.isoformat(),"end":end.isoformat(),**groups})
    if not history or any(a["end"]>=b["start"] for a,b in zip(history,history[1:])):
        raise ValueError("Invalid history ordering")
    return {"unit":"100 million JPY","positive":"net acquisition","history":history,"latest":history[-1]}

def main():
    req = urllib.request.Request(URL, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=40) as resp:
        raw = resp.read(5_000_001)
    if len(raw)>5_000_000:
        raise ValueError("CSV too large")
    content = decode(raw)
    if "<html" in content.lower()[:1000]:
        raise ValueError("HTML instead of CSV")
    rows = list(csv.reader(io.StringIO(content)))
    if len(rows)<10 or max(map(len,rows))<5:
        raise ValueError("Unrecognized CSV layout")
    digest = hashlib.sha256(raw).hexdigest()
    normalized = normalize(rows)
    if OUT.exists() and json.loads(OUT.read_text()).get("sha256")==digest and json.loads(OUT.read_text()).get("schema_status")=="validated_v1":
        print("Unchanged MOF data")
        return
    payload = {"source":URL,"retrieved_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
        "sha256":digest,"schema_status":"validated_v1","rows":rows,"normalized":normalized}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
    print("Archived",len(rows),"rows")

if __name__=="__main__":
    main()
