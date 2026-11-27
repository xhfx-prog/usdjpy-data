"""Offline audit of committed data. No provider API calls."""
import json, math, hashlib
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

if __package__:
    from .collect_tfx import load_json as load, verify_snapshot
else:
    from collect_tfx import load_json as load, verify_snapshot


def intraday_gap_candidates(stamps, interval):
    step = timedelta(minutes=1 if interval == "1min" else 60)
    jst = ZoneInfo("Asia/Tokyo")
    ny = ZoneInfo("America/New_York")
    candidates = []
    for left, right in zip(stamps, stamps[1:]):
        before = datetime.fromisoformat(left).replace(tzinfo=jst)
        after = datetime.fromisoformat(right).replace(tzinfo=jst)
        if after - before <= step:
            continue
        missing = 0
        cursor = before + step
        while cursor < after:
            market = cursor.astimezone(ny)
            if market.weekday() < 4 or (market.weekday() == 4 and market.hour < 17) or (
                    market.weekday() == 6 and market.hour >= 17):
                missing += 1
            cursor += step
        if missing:
            candidates.append({"after": left, "before": right, "potentially_open_bars": missing})
    return candidates

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

def run(root, *, require_tfx=False):
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
    tfx_key = "tfx_usdjpy.json"
    if tfx_key in parsed:
        try:
            verify_snapshot(parsed[tfx_key])
        except (ValueError, KeyError, TypeError) as error:
            results[tfx_key].update(status="FAIL", error=str(error))
        else:
            for name in ("daily", "weekly"):
                feed = parsed[tfx_key][name]
                results[tfx_key][name] = {key: feed[key] for key in (
                    "status", "total_records", "actual_oldest", "actual_newest",
                    "missing_source_trade_dates", "last_success_at_utc")}
                if feed["missing_source_trade_dates"] or not feed["history"]:
                    results[tfx_key]["status"] = "FAIL"
    manifests={}
    for interval in ("1min","1h","1day"):
        name=f"history_{interval}_index.json"; errors=[]
        archive = {}
        compatible_count = None
        gaps = []
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
                if not q.get("ok"):
                    continue
                actual.append(q)
                partition = Path(key).stem
                rows = parsed[key]["data"]["values"]
                stamps = {row["datetime"] for row in rows}
                if (parsed[key].get("interval") != interval or key != f"history_{interval}/{partition}.json"
                        or any(stamp[:len(partition)] != partition for stamp in stamps)):
                    errors.append("wrong interval or partition " + key)
                if stamps & archive.keys():
                    errors.append("overlapping partitions " + key)
                archive.update((row["datetime"], row) for row in rows)
            if sum(x["count"] for x in actual)!=idx["total_bars"]: errors.append("total mismatch")
            if actual and (min(x["first"] for x in actual)!=idx["actual_oldest"] or max(x["last"] for x in actual)!=idx["actual_newest"]): errors.append("bounds mismatch")
            physical={k for k in parsed if k.startswith(f"history_{interval}/")}
            snapshot = parsed[f"usdjpy_{interval}.json"]["data"]["values"]
            shared = [row for row in snapshot if row["datetime"] in archive]
            compatible_count = len(shared)
            if len(shared) != len(snapshot):
                errors.append(f"{len(snapshot) - len(shared)} snapshot bars missing from archive")
            discrepancies = sum(any(float(row[field]) != float(archive[row["datetime"]][field])
                                    for field in ("open", "high", "low", "close")) for row in shared)
            if discrepancies:
                errors.append(f"{discrepancies} snapshot bars disagree with archive")
            if interval in ("1min", "1h"):
                gaps = intraday_gap_candidates(sorted(archive), interval)
            if physical-seen: errors.append("unindexed partitions: "+", ".join(sorted(physical-seen)))
        except Exception as exc: errors.append(str(exc))
        manifests[interval]={"status":"FAIL" if errors else "PASS","errors":errors,
                             "snapshot_bars_checked": compatible_count,
                             "retention_calendar_days": parsed.get(name, {}).get("retention_calendar_days"),
                             "actual_oldest": parsed.get(name, {}).get("actual_oldest"),
                             "actual_newest": parsed.get(name, {}).get("actual_newest"),
                             "candidate_open_session_gap_count": len(gaps),
                             "candidate_open_session_gaps": gaps[:30]}
    macro=parsed.get("macro_latest.json",{}); fx=parsed.get("fx_intraday.json",{})
    news=macro.get("candidates",[]); calendar=macro.get("economic_calendar",{})
    coverage={"macro_status":macro.get("status"),"news_count":len(news),"news_count_matches":len(news)==macro.get("candidate_count"),"news_missing_timestamp":sum(not x.get("source_timestamp_utc") for x in news),"calendar_status":calendar.get("status"),"calendar_events":len(calendar.get("events",[])),"calendar_actuals":sum(bool(x.get("vendor_actual")) for x in calendar.get("events",[])),"fx_series":{k:{"bars":len(v.get("bars",{})),"latest_epoch":v.get("latest_epoch")} for k,v in fx.get("series",{}).items()},"fx_errors":fx.get("errors",{}),"fx_events":len(fx.get("events",[]))}
    releases=macro.get("economic_release_snapshot", {})
    sources=releases.get("sources", [])
    release_errors=[]
    coverage["release_sources"]={}
    for source in sources:
        name=source.get("name")
        events=source.get("events", [])
        if not name or name in coverage["release_sources"]:
            release_errors.append("missing or duplicate economic source name")
            continue
        coverage["release_sources"][name]={"status":source.get("status"),"actuals":len(events)}
        for event in events:
            try:
                issued=datetime.fromisoformat(event["at_utc"].replace("Z","+00:00"))
                observed=datetime.fromisoformat(source["observed_at_utc"].replace("Z","+00:00"))
                if issued > observed or not (event.get("vendor_actual") or event.get("official_actual")):
                    raise ValueError("invalid figure or timestamp")
            except (TypeError, ValueError, KeyError) as error:
                release_errors.append(f"{name}: {error}")
    if macro and (not coverage["news_count_matches"] or coverage["news_missing_timestamp"]):
        release_errors.append("macro news count or timestamp mismatch")
    coverage["macro_integrity_errors"]=release_errors
    required=["macro_latest.json","fx_intraday.json","usdjpy_1min.json","usdjpy_1h.json","usdjpy_1day.json","mof_securities_weekly.json"]
    if require_tfx:
        required.append(tfx_key)
    missing=[k for k in required if k not in parsed]
    return {"checked_at_utc":datetime.now(timezone.utc).isoformat(),"status":"FAIL" if missing or release_errors or any(v["status"]=="FAIL" for v in list(results.values())+list(manifests.values())) else "PASS","missing_required":missing,"files":results,"manifests":manifests,"coverage":coverage,"note":"PASS denotes file integrity, not market freshness or complete external coverage. Gap intervals include market closures."}

if __name__=="__main__":
    result=run(Path("data"), require_tfx="--require-tfx" in sys.argv)
    Path("/tmp/usdjpy_quality_audit.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k!="files"},ensure_ascii=False))
    print("Files checked:", len(result["files"]), "Failed:",
          [k for k, v in result["files"].items() if v["status"] == "FAIL"])
    sys.exit(result["status"]!="PASS")
