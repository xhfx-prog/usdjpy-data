"""Public calendar feed: vendor-reported figures, not verified official actuals."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
import re
from investigate_world_news import request_bytes

URL="https://nfs.faireconomy.media/ff_calendar_thisweek.json"
UTC=timezone.utc
JST=ZoneInfo("Asia/Tokyo")
NY=ZoneInfo("America/New_York")

def surprise(actual, forecast):
    pattern=r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))([%KMBT]?)"
    a=re.fullmatch(pattern,str(actual).strip().replace(",",""))
    f=re.fullmatch(pattern,str(forecast).strip().replace(",",""))
    if not a or not f or a[2]!=f[2]: return None
    try: return {"difference":str(Decimal(a[1])-Decimal(f[1])),"unit":a[2] or "reported_unit"}
    except InvalidOperation: return None

def collect_calendar(now, previous=None, fetcher=request_bytes):
    previous=previous or {}
    saved={x["id"]:dict(x) for x in previous.get("events",[]) if x.get("id")}
    try:
        rows=json.loads(fetcher(URL))
        if not isinstance(rows,list) or not rows: raise ValueError("empty or invalid calendar")
        count=0
        for row in rows:
            if row.get("country") not in ("USD","JPY"): continue
            try:
                dt=datetime.fromisoformat(row["date"])
                if dt.tzinfo is None: continue
            except (KeyError,ValueError,TypeError): continue
            if not now-timedelta(days=3)<=dt<=now+timedelta(days=7): continue
            title=row.get("title","")
            if not title: continue
            key=row["country"]+"|"+title+"|"+dt.astimezone(UTC).isoformat()
            event=saved.get(key,{"id":key,"title":title,"currency":row["country"],"at_utc":dt.astimezone(UTC).isoformat(),"at_jst":dt.astimezone(JST).isoformat(),"at_et":dt.astimezone(NY).isoformat()})
            event.update(impact=row.get("impact"),vendor_forecast=row.get("forecast") or None,vendor_previous=row.get("previous") or None,last_seen_utc=now.isoformat())
            if now<dt and row.get("forecast"):
                event["pre_release_forecast"]=row["forecast"]
                event["forecast_observed_at_utc"]=now.isoformat()
            if now<dt and row.get("previous"):
                event["pre_release_previous"]=row["previous"]
            if now>=dt and row.get("actual"):
                event["vendor_actual"]=row["actual"]
                event["actual_observed_at_utc"]=now.isoformat()
            event["surprise"]=surprise(event.get("vendor_actual"),event.get("pre_release_forecast"))
            event["official_actual_verified"]=False
            event["result_status"]="VENDOR_ACTUAL" if event.get("vendor_actual") else "AWAITING_ACTUAL" if now>=dt else "SCHEDULED"
            saved[key]=event
            count+=1
        status="OK" if count else "NO_MATCHING_EVENTS"
        error=None
    except Exception as exc:
        status="ERROR"
        error=str(exc)[:200]
    events=sorted((x for x in saved.values() if datetime.fromisoformat(x["at_utc"])>=now-timedelta(days=3)),key=lambda x:x["at_utc"])
    return {"status":status,"source":URL,"source_type":"THIRD_PARTY_CALENDAR_NOT_OFFICIAL","checked_at_utc":now.isoformat(),"error":error,"events":events,"note":"Actual may be absent from this feed; never infer it from previous or forecast. Surprise requires a forecast captured before release."}
