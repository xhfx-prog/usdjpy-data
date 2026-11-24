"""Offline audit of committed data. No provider API calls."""
import json, math, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

def load(p):
    return json.loads(p.read_text(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))

def audit_bars(obj, interval):
    rows=obj["data"]["values"]
    if not rows: raise ValueError("empty OHLC")
    stamps=[r["datetime"] for r in rows]
    invalid=0
    for r in rows:
        try:
            o,h,l,c=(float(r[k]) for k in ("open","high","low","close"))
            if not all(math.isfinite(v) for v in (o,h,l,c)) or not 0<l<=min(o,c)<=max(o,c)<=h: invalid+=1
            datetime.fromisoformat(r["datetime"])
        except (ValueError,TypeError,KeyError): invalid+=1
    duplicate=len(stamps)-len(set(stamps))
    ordered=stamps==sorted(stamps,reverse=True)
    gaps=0
    if interval in ("1min","1h"):
        delta=timedelta(minutes=1 if interval=="1min" else 60)
        for a,b in zip(sorted(set(stamps)),sorted(set(stamps))[1:]):
            if datetime.fromisoformat(b)-datetime.fromisoformat(a)>delta: gaps+=1
    return {"count":len(rows),"first":min(stamps),"last":max(stamps),"invalid":invalid,"duplicates":duplicate,"descending":ordered,"gap_intervals_including_market_closures":gaps,"ok":not invalid and not duplicate and ordered}

def run(root):
    results={}; parsed={}
    for p in sorted(root.rglob("*.json")):
        key=str(p.relative_to(root))
        try:
            obj=load(p); parsed[key]=obj
            results[key]={"status":"PASS","bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
            if isinstance(obj,dict) and obj.get("interval") in ("1min","1h","1day") and isinstance(obj.get("data"),dict):
                q=audit_bars(obj,obj["interval"]);results[key]["ohlc"]=q
                if not q["ok"]: results[key]["status"]="FAIL"
        except Exception as exc: results[key]={"status":"FAIL","error":str(exc)[:300]}
    manifests={}
    for interval in ("1min","1h","1day"):
        name=f"history_{interval}_index.json"; errors=[]
        try:
            idx=parsed[name]; entries=idx.get("sessions",idx.get("partitions",[]))
            if not entries: errors.append("empty manifest")
            seen=set(); actual=[]
            for e in entries:
                key=e["path"].removeprefix("data/")
                if key in seen: errors.append("duplicate partition "+key)
                seen.add(key)
                q=results.get(key,{}).get("ohlc",{})
                if not q.get("ok") or (q.get("count"),q.get("first"),q.get("last"))!=(e["count"],e["first"],e["last"]): errors.append("partition mismatch "+key)
                if q: actual.append(q)
            if sum(x["count"] for x in actual)!=idx["total_bars"]: errors.append("total mismatch")
            if actual and (min(x["first"] for x in actual)!=idx["actual_oldest"] or max(x["last"] for x in actual)!=idx["actual_newest"]): errors.append("bounds mismatch")
            physical={k for k in parsed if k.startswith(f"history_{interval}/")}
            if physical-seen: errors.append("unindexed partitions: "+", ".join(sorted(physical-seen)))
        except Exception as exc: errors.append(str(exc))
        manifests[interval]={"status":"FAIL" if errors else "PASS","errors":errors}
    macro=parsed.get("macro_latest.json",{}); fx=parsed.get("fx_intraday.json",{})
    news=macro.get("candidates",[]); calendar=macro.get("economic_calendar",{})
    coverage={"macro_status":macro.get("status"),"news_count":len(news),"news_count_matches":len(news)==macro.get("candidate_count"),"news_missing_timestamp":sum(not x.get("source_timestamp_utc") for x in news),"calendar_status":calendar.get("status"),"calendar_events":len(calendar.get("events",[])),"calendar_actuals":sum(bool(x.get("vendor_actual")) for x in calendar.get("events",[])),"fx_series":{k:{"bars":len(v.get("bars",{})),"latest_epoch":v.get("latest_epoch")} for k,v in fx.get("series",{}).items()},"fx_errors":fx.get("errors",{}),"fx_events":len(fx.get("events",[]))}
    required=["macro_latest.json","fx_intraday.json","usdjpy_1min.json","usdjpy_1h.json","usdjpy_1day.json","mof_securities_weekly.json"]
    missing=[k for k in required if k not in parsed]
    return {"checked_at_utc":datetime.now(timezone.utc).isoformat(),"status":"FAIL" if missing or any(v["status"]=="FAIL" for v in list(results.values())+list(manifests.values())) else "PASS","missing_required":missing,"files":results,"manifests":manifests,"coverage":coverage,"note":"PASS denotes file integrity, not market freshness or complete external coverage. Gap intervals include market closures."}

if __name__=="__main__":
    import sys
    result=run(Path("data"))
    Path("/tmp/usdjpy_quality_audit.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k!="files"},ensure_ascii=False))
    sys.exit(result["status"]!="PASS")
