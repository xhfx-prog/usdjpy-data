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

def intraday_evidence(now, articles):
    """Public delayed chart data, no paid API calls; timestamp-aligned observations only."""
    from concurrent.futures import ThreadPoolExecutor
    import urllib.parse
    symbols={"usd_jpy":"JPY=X","dxy":"DX-Y.NYB","wti_futures":"CL=F","sp500_futures":"ES=F","us_10y_yield_index":"^TNX"}
    def fetch(item):
        name,symbol=item
        url="https://query1.finance.yahoo.com/v8/finance/chart/"+urllib.parse.quote(symbol,safe="")+"?interval=5m&range=2d"
        try:
            obj=json.loads(request_bytes(url))["chart"]["result"][0]
            q=obj["indicators"]["quote"][0]["close"]
            bars={int(t):float(v) for t,v in zip(obj["timestamp"],q) if v is not None and int(t)%300==0 and int(t)+300<=now.timestamp()}
            if not bars: raise ValueError("no completed bars")
            return name,{"status":"OBSERVED_PROVIDER_DELAY_UNVERIFIED","source":url,"last_bar_utc":datetime.fromtimestamp(max(bars),timezone.utc).isoformat(),"bars":bars}
        except Exception as e: return name,{"status":"ERROR","source":url,"error":str(e)[:200],"bars":{}}
    with ThreadPoolExecutor(max_workers=5) as pool:
        series=dict(pool.map(fetch,symbols.items()))
    # Use saved canonical USDJPY candles for move detection, not Yahoo FX.
    moves=[]
    try:
        from zoneinfo import ZoneInfo
        raw=json.loads(Path("data/usdjpy_1min.json").read_text())["data"]["values"]
        fx={int(datetime.strptime(b["datetime"],"%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Tokyo")).timestamp()):float(b["close"]) for b in raw}
        for t in sorted(fx):
            if t<now.timestamp()-24*3600 or t+60>now.timestamp() or any(t-i*60 not in fx for i in range(6)): continue
            pips=(fx[t]-fx[t-300])*100
            if abs(pips)>=10: moves.append((t,pips))
        selected=[]
        for t,pips in sorted(moves,key=lambda x:abs(x[1]),reverse=True):
            if all(abs(t-u)>900 for u,_ in selected): selected.append((t,pips))
            if len(selected)>=8: break
        evidence=[]
        for t,pips in sorted(selected,reverse=True):
            start=t-300
            leads=[]
            for a in articles:
                try: ts=datetime.fromisoformat(a["source_timestamp_utc"]).timestamp()
                except (ValueError,TypeError,KeyError): continue
                if start-1800<=ts<start: leads.append(a)
            aligned={}
            for name,info in series.items():
                bars=info["bars"]
                # Exact completed 5m boundaries enclosing the FX move; no nearest stale quote.
                before=(start//300)*300-300
                after=((t+60+299)//300)*300-300
                if before in bars and after in bars and after>before and after+300<=now.timestamp():
                    aligned[name]={"from_bar_utc":datetime.fromtimestamp(before,timezone.utc).isoformat(),"to_bar_utc":datetime.fromtimestamp(after,timezone.utc).isoformat(),"change":round(bars[after]-bars[before],6)}
                else: aligned[name]={"status":"NO_ALIGNED_BARS"}
            evidence.append({"window_start_utc":datetime.fromtimestamp(start,timezone.utc).isoformat(),"window_end_utc":datetime.fromtimestamp(t+60,timezone.utc).isoformat(),"usd_jpy_change_pips":round(pips,3),"preceding_publications":sorted(leads,key=lambda a:a["priority_score_not_causal_confidence"],reverse=True)[:5],"cross_market":aligned,"causality":"UNCONFIRMED"})
        move_status="OBSERVED"
    except Exception as e:
        evidence=[];move_status="ERROR: "+str(e)[:180]
    return {"status":"OBSERVED" if any(x["bars"] for x in series.values()) else "UNAVAILABLE","move_status":move_status,"series":{k:{a:b for a,b in v.items() if a!="bars"} for k,v in series.items()},"moves":evidence,"note":"Publication time is not event time; chart delay and exchange sessions may differ. No causal attribution."}

def main():
    now=datetime.now(timezone.utc)
    result=collect(now,pre_minutes=90,post_minutes=0,now=now,sources=("google_rss","official_rss"))
    path=Path("data/macro_latest.json")
    try: old=json.loads(path.read_text())
    except (OSError,ValueError): old={}
    cutoff=(now-timedelta(hours=24)).isoformat()
    # Bootstrap missing history in bounded windows using the existing scanner.
    backfill=[]
    if not old.get("history_bootstrapped"):
        for hours in (3,6,9,12,15,18,21):
            anchor=now-timedelta(hours=hours)
            scan=collect(anchor,pre_minutes=180,post_minutes=0,now=now,sources=("google_rss","official_rss"))
            backfill.extend(scan["candidates"])
    rows=deduplicate(result["candidates"]+old.get("candidates",[])+backfill)
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
         "news_sources":feeds,"history_bootstrapped":True,"candidates":rows[:1200],"calendar":calendar,
         "cross_market": cross_market(now),"intraday_evidence":intraday_evidence(now,rows),"confirmed_catalyst":None}
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n")
if __name__=="__main__": main()
