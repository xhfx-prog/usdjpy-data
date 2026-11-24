"""Scheduled macro discovery, with explicit source coverage and no causal claims."""
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from investigate_world_news import collect, deduplicate, request_bytes
import xml.etree.ElementTree as ET

def cross_market(now):
    """Public daily observations; never label these as intraday reactions."""
    from concurrent.futures import ThreadPoolExecutor
    import csv, io
    ids={"us_2y_yield":"DGS2","us_10y_yield":"DGS10","broad_dollar_not_dxy":"DTWEXBGS","wti_spot":"DCOILWTICO","sp500_cash":"SP500"}
    def fetch(item):
        name,series=item
        url="https://fred.stlouisfed.org/graph/fredgraph.csv?id="+series+"&cosd="+(now-timedelta(days=21)).date().isoformat()
        try:
            rows=list(csv.reader(io.StringIO(request_bytes(url).decode("utf-8-sig"))))
            values=[]
            for row in rows[1:]:
                try: values.append((datetime.strptime(row[0],"%Y-%m-%d").date(),float(row[1])))
                except (ValueError,IndexError): continue
            values.sort()
            if len(values)<2: raise ValueError("fewer than two observations")
            d,v=values[-1]; p,pv=values[-2]
            return name,{"status":"DAILY_ONLY","source":url,"observation_date":d.isoformat(),"previous_date":p.isoformat(),"value":v,"previous_value":pv,"change":round(v-pv,5),"age_calendar_days":(now.date()-d).days}
        except Exception as e: return name,{"status":"ERROR","source":url,"error":str(e)[:200]}
    with ThreadPoolExecutor(max_workers=5) as pool:
        series=dict(pool.map(fetch,ids.items()))
    return {"status":"DAILY_CONTEXT_ONLY","series":series,"intraday_correlation":"NOT_AVAILABLE","dxy":"NOT_CONNECTED","sp500_futures":"NOT_CONNECTED"}

def main():
    now=datetime.now(timezone.utc)
    result=collect(now,pre_minutes=90,post_minutes=0,now=now)
    path=Path("data/macro_latest.json")
    try: old=json.loads(path.read_text())
    except (OSError,ValueError): old={}
    cutoff=(now-timedelta(hours=24)).isoformat()
    rows=deduplicate(result["candidates"]+old.get("candidates",[]))
    rows=[x for x in rows if x.get("source_timestamp_utc") and x["source_timestamp_utc"]>=cutoff]
    rows.sort(key=lambda x:x["source_timestamp_utc"],reverse=True)
    feeds=result["news_sources"]
    # BLS official calendar: preserve actual source fields, not guessed dates.
    calendar={"status":"ERROR","source":"https://www.bls.gov/schedule/news_release/bls.ics"}
    try:
        raw=request_bytes(calendar["source"]).decode("utf-8")
        if "BEGIN:VCALENDAR" not in raw: raise ValueError("invalid calendar")
        events=[]
        for block in raw.split("BEGIN:VEVENT")[1:]:
            fields={}
            for line in block.splitlines():
                key,sep,value=line.partition(":")
                if sep and key.split(";")[0] in ("DTSTART","SUMMARY","URL","UID"):
                    fields[key]=value
            for key,value in fields.items():
                if key.startswith("DTSTART"):
                    from zoneinfo import ZoneInfo
                    try:
                        if value.endswith("Z"): dt=datetime.strptime(value,"%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                        elif "TZID=America/New_York" in key: dt=datetime.strptime(value,"%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo("America/New_York"))
                        else: continue
                        if now<=dt<=now+timedelta(hours=72):
                            events.append({"at_jst":dt.astimezone(ZoneInfo("Asia/Tokyo")).isoformat(),"summary":fields.get("SUMMARY"),"source":fields.get("URL",calendar["source"])})
                    except ValueError: continue
        calendar.update(status="OK",events=sorted(events,key=lambda x:x["at_jst"]),coverage="BLS_ONLY")
    except Exception as e: calendar["error"]=str(e)[:200]
    obj={"generated_at_utc":now.isoformat(),"scan_from_utc":(now-timedelta(minutes=90)).isoformat(),
         "status":"OK" if all(x["status"]=="ok" for x in feeds) else "PARTIAL" if any(x["status"]=="ok" for x in feeds) else "FAILED",
         "news_sources":feeds,"candidates":rows[:200],"calendar":calendar,
         "cross_market": cross_market(now),"confirmed_catalyst":None}
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n")
if __name__=="__main__": main()
