import csv
import datetime as dt
import hashlib
import io
import json
import pathlib
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
    if OUT.exists() and json.loads(OUT.read_text()).get("sha256")==digest:
        print("Unchanged MOF data")
        return
    payload = {"source":URL,"retrieved_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
        "sha256":digest,"schema_status":"raw_csv_semantics_not_yet_validated","rows":rows}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
    print("Archived",len(rows),"rows")

if __name__=="__main__":
    main()
