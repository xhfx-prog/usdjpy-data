"""Scheduled macro discovery, with explicit source coverage and no causal claims."""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from investigate_world_news import collect, deduplicate, request_bytes
import xml.etree.ElementTree as ET
from economic_calendar import collect_calendar

def economic_release_snapshot(previous, now):
    url = "https://usdjpy-chart.xxhappyfrenzyxx.workers.dev/economic-releases"
    cutoff = now - timedelta(hours=36)
    saved = {item["url"]: item for item in previous.get("sources", [])}

    def with_fallback(item):
        older = saved.get(item["url"], {})
        events = [event for event in older.get("events", [])
                  if cutoff <= datetime.fromisoformat(event["at_utc"]) <= now]
        if events:
            return {**older, "events": events, "status": "SCHEDULED_FALLBACK",
                    "live_error": item.get("error")}
        return item

    try:
        latest = json.loads(request_bytes(url, timeout=14))
        if not isinstance(latest.get("sources"), list) or not latest["sources"]:
            raise ValueError("Missing release sources")
        if not isinstance(latest.get("checked_at_utc"), str):
            raise ValueError("Missing release check timestamp")
        checked = datetime.fromisoformat(latest["checked_at_utc"])
        seen_urls = set()
        sources = []
        for item in latest["sources"]:
            if not isinstance(item.get("url"), str) or not isinstance(item.get("events"), list):
                raise ValueError("Invalid release source")
            if item["url"] in seen_urls:
                raise ValueError("Duplicate release source")
            seen_urls.add(item["url"])
            if item.get("status") in ("VENDOR_ACTUALS", "OFFICIAL_ACTUALS"):
                observed = datetime.fromisoformat(item["observed_at_utc"])
                older = saved.get(item["url"], {})
                def identity(event):
                    return event["currency"], event["title"], event["at_utc"]
                first_seen = {identity(event): event.get("first_scheduled_observed_at_utc")
                              for event in older.get("events", [])}
                for event in item["events"]:
                    released = datetime.fromisoformat(event["at_utc"])
                    if not (released <= checked <= observed) or not (event.get("vendor_actual") or event.get("official_actual")):
                        raise ValueError("Invalid economic release figure or timestamp")
                    first = first_seen.get(identity(event)) or observed.isoformat()
                    event["first_scheduled_observed_at_utc"] = first
                    event["first_scheduled_observed_after_release_seconds"] = int(
                        (datetime.fromisoformat(first) - released).total_seconds())
            sources.append(with_fallback(item) if item.get("status") == "UNAVAILABLE" else item)
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
        message = str(error)[:200]
        sources = [with_fallback({"name": source.get("name"), "url": source_url,
                                  "events": [], "status": "UNAVAILABLE", "error": message})
                   for source_url, source in saved.items()]
        return {**previous, "sources": sources, "last_attempt_utc": now.isoformat(), "error": message}
    return {**latest, "sources": sources, "last_attempt_utc": now.isoformat()}


def japanese_daily_yields(now, fetcher=request_bytes):
    """Read the Ministry of Finance's daily JGB reference yield curve."""
    import csv
    import io
    import math
    import re
    from zoneinfo import ZoneInfo

    today = now.astimezone(ZoneInfo("Asia/Tokyo")).date()
    url = "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"
    maturities = {"jp_2y_yield": "2年", "jp_10y_yield": "10年"}
    try:
        rows = list(csv.reader(io.StringIO(fetcher(url).decode("cp932"))))
        header = next(row for row in rows if row and row[0] == "基準日")
        columns = {name: header.index(label) for name, label in maturities.items()}
        values = {name: {} for name in maturities}
        for row in rows:
            match = re.fullmatch(r"R(\d+)\.(\d+)\.(\d+)", row[0].strip()) if row else None
            if not match:
                continue
            year, month, day = map(int, match.groups())
            observed = date(2018 + year, month, day)
            if observed > today:
                continue
            for name, column in columns.items():
                value = float(row[column])
                if not math.isfinite(value):
                    raise ValueError("Nonfinite JGB yield")
                values[name][observed] = value
        result = {}
        for name, observations in values.items():
            latest = sorted(observations.items())
            if not latest:
                raise ValueError("Missing JGB yield observations")
            day, value = latest[-1]
            previous_day, previous_value = latest[-2] if len(latest) > 1 else (None, None)
            result[name] = {"status": "DAILY_ONLY", "source": url, "unit": "percent",
                            "observation_date": day.isoformat(), "value": value,
                            "previous_date": previous_day.isoformat() if previous_day else None,
                            "previous_value": previous_value,
                            "change": round(value - previous_value, 5) if previous_value is not None else None,
                            "age_calendar_days": (today - day).days}
        return result
    except (OSError, UnicodeError, ValueError, StopIteration, IndexError) as error:
        return {name: {"status": "ERROR", "source": url, "error": str(error)[:180]}
                for name in maturities}


def american_daily_yields(now, fetcher=request_bytes):
    """Read official Treasury par yields and retain an explicit observation date."""
    from zoneinfo import ZoneInfo

    url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
           "?data=daily_treasury_yield_curve&field_tdr_date_value_month=")
    ny_day = now.astimezone(ZoneInfo("America/New_York")).date()
    maturities = {"us_2y_yield": "BC_2YEAR", "us_10y_yield": "BC_10YEAR"}
    data_ns = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
    meta_ns = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}"
    observations = {}
    try:
        for month in (ny_day, ny_day.replace(day=1) - timedelta(days=1)):
            root = ET.fromstring(fetcher(url + month.strftime("%Y%m")))
            for row in root.iter(meta_ns + "properties"):
                observed = date.fromisoformat(row.findtext(data_ns + "NEW_DATE")[:10])
                if observed <= ny_day:
                    observations[observed] = {
                        name: float(row.findtext(data_ns + field)) for name, field in maturities.items()
                    }
            if len(observations) >= 2:
                break
        day, previous = sorted(observations, reverse=True)[:2]
        return {name: {"status": "DAILY_ONLY", "source": url + day.strftime("%Y%m"), "unit": "percent",
                       "observation_date": day.isoformat(), "value": observations[day][name],
                       "previous_date": previous.isoformat(), "previous_value": observations[previous][name],
                       "change": round(observations[day][name] - observations[previous][name], 5),
                       "age_calendar_days": (ny_day - day).days}
                for name in maturities}
    except (OSError, ET.ParseError, TypeError, ValueError):
        return {}


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
    with ThreadPoolExecutor(max_workers=7) as pool:
        japan = pool.submit(japanese_daily_yields, now)
        treasury = pool.submit(american_daily_yields, now)
        series = dict(pool.map(fetch, ids.items()))
        series.update(japan.result())
        series.update(treasury.result())
    return {"status":"DAILY_CONTEXT_ONLY","series":series,"intraday_correlation":"NOT_AVAILABLE","dxy":"NOT_CONNECTED","sp500_futures":"NOT_CONNECTED"}

def intraday_evidence(now, articles):
    """Public delayed chart data, no paid API calls; timestamp-aligned observations only."""
    from concurrent.futures import ThreadPoolExecutor
    import urllib.parse
    symbols={"usd_jpy":"JPY=X","dxy":"DX-Y.NYB","wti_futures":"CL=F","sp500_futures":"ES=F","us_10y_yield_index":"^TNX","us_10y_treasury_futures":"ZN=F","us_2y_treasury_futures":"ZT=F"}
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
    with ThreadPoolExecutor(max_workers=7) as pool:
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
            during=[]
            followups=[]
            for a in articles:
                try: ts=datetime.fromisoformat(a["source_timestamp_utc"]).timestamp()
                except (ValueError,TypeError,KeyError): continue
                if start-1800<=ts<start: leads.append(a)
                elif start<=ts<t+60: during.append(a)
                elif t+60<=ts<t+960: followups.append(a)
            aligned={}
            for name,info in series.items():
                bars=info["bars"]
                # Exact completed 5m boundaries enclosing the FX move; no nearest stale quote.
                before=(start//300)*300-300
                after=((t+60+299)//300)*300-300
                if before in bars and after in bars and after>before and after+300<=now.timestamp():
                    aligned[name]={"from_bar_utc":datetime.fromtimestamp(before,timezone.utc).isoformat(),"to_bar_utc":datetime.fromtimestamp(after,timezone.utc).isoformat(),"change":round(bars[after]-bars[before],6),"change_pct":round((bars[after]/bars[before]-1)*100,6) if bars[before] else None,"direction":"UP" if bars[after]>bars[before] else "DOWN" if bars[after]<bars[before] else "FLAT"}
                else: aligned[name]={"status":"NO_ALIGNED_BARS"}
            fx_direction=1 if pips>0 else -1
            checks={}
            for name,expected in (("dxy",fx_direction),("us_10y_yield_index",fx_direction),("us_10y_treasury_futures",-fx_direction),("us_2y_treasury_futures",-fx_direction)):
                change=aligned.get(name,{}).get("change")
                checks[name]="UNAVAILABLE" if change is None else "FLAT" if change==0 else "ALIGNED" if change*expected>0 else "OPPOSED"
            evidence.append({"directional_crosschecks_not_causality":checks,"window_start_utc":datetime.fromtimestamp(start,timezone.utc).isoformat(),"window_end_utc":datetime.fromtimestamp(t+60,timezone.utc).isoformat(),"usd_jpy_change_pips":round(pips,3),"preceding_publications":sorted(leads,key=lambda a:a["priority_score_not_causal_confidence"],reverse=True)[:5],"during_move_publications":sorted(during,key=lambda a:a["priority_score_not_causal_confidence"],reverse=True)[:8],"post_move_publications_not_initial_triggers":sorted(followups,key=lambda a:a["priority_score_not_causal_confidence"],reverse=True)[:5],"cross_market":aligned,"causality":"UNCONFIRMED"})
        publication_reactions=[]
        for a in sorted(articles,key=lambda x:x.get("priority_score_not_causal_confidence",0),reverse=True)[:100]:
            try: ts=datetime.fromisoformat(a["source_timestamp_utc"]).timestamp()
            except (ValueError,TypeError,KeyError): continue
            base=int(ts//60)*60-60
            reactions={}
            for horizon in (5,15,30):
                end=int(ts//60)*60+(horizon-1)*60
                if end+60<=now.timestamp() and all(k in fx for k in range(base,end+1,60)):
                    reactions[str(horizon)]=round((fx[end]-fx[base])*100,3)
                else: reactions[str(horizon)]=None
            market={}
            before=int(ts//300)*300-300
            after=int(ts//300)*300+600
            for name,info in series.items():
                bars=info["bars"]
                if after+300<=now.timestamp() and all(k in bars for k in range(before,after+1,300)):
                    market[name]=round(bars[after]-bars[before],6)
                else: market[name]=None
            publication_reactions.append({"title":a["title"],"url":a["url"],"source_timestamp_utc":a["source_timestamp_utc"],"source_timestamp_kind":a.get("source_timestamp_kind"),"usd_jpy_change_pips_by_minutes":reactions,"cross_market_approximately_15min_change":market,"causality":"UNCONFIRMED"})
        move_status="OBSERVED"
    except Exception as e:
        evidence=[];publication_reactions=[];move_status="ERROR: "+str(e)[:180]
    return {"status":"OBSERVED" if any(x["bars"] for x in series.values()) else "UNAVAILABLE","move_status":move_status,"series":{k:{a:b for a,b in v.items() if a!="bars"} for k,v in series.items()},"moves":evidence,"publication_reactions":publication_reactions,"note":"Publication time is not event time; chart delay and exchange sessions may differ. No causal attribution."}

def official_calendar_links():
    # Source registry only: these links are not parsed event records.
    return {"status":"REFERENCE_ONLY_NOT_PARSED","sources":{
        "BEA":"https://www.bea.gov/news/schedule",
        "Federal Reserve":"https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "Bank of Japan":"https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm",
        "Japan Statistics Bureau":"https://www.stat.go.jp/english/info/guide/asu/index.html",
        "Japan Cabinet Office":"https://www.esri.cao.go.jp/en/sna/kouhyou/kouhyou_top.html"}}

def main():
    from functools import lru_cache
    # Reuse identical successful feed responses within this run only.
    cached_fetch=lru_cache(maxsize=128)(request_bytes)
    now=datetime.now(timezone.utc)
    result=collect(now,pre_minutes=360,post_minutes=0,now=now,sources=("google_rss","official_rss"),candidate_limit=600,fetcher=cached_fetch)
    path=Path("data/macro_latest.json")
    try: old=json.loads(path.read_text())
    except (OSError,ValueError): old={}
    cutoff=(now-timedelta(hours=24)).isoformat()
    # Bootstrap missing history in bounded windows using the existing scanner.
    backfill=[]
    if not old.get("history_bootstrapped"):
        for hours in (3,6,9,12,15,18,21):
            anchor=now-timedelta(hours=hours)
            scan=collect(anchor,pre_minutes=180,post_minutes=0,now=now,sources=("google_rss","official_rss"),candidate_limit=600,fetcher=cached_fetch)
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
    obj={"generated_at_utc":now.isoformat(),"scan_from_utc":(now-timedelta(minutes=360)).isoformat(),
         "status":"OK" if all(x["status"]=="ok" for x in feeds) else "PARTIAL" if any(x["status"]=="ok" for x in feeds) else "FAILED",
         "news_sources":feeds,"history_bootstrapped":True,"candidates":rows,"candidate_count":len(rows),"calendar":calendar,"additional_calendar_sources":official_calendar_links(),"economic_calendar":collect_calendar(now,old.get("economic_calendar")),
         "economic_release_snapshot": economic_release_snapshot(old.get("economic_release_snapshot") or {}, now),
         "cross_market": cross_market(now),"intraday_evidence":intraday_evidence(now,rows),"confirmed_catalyst":None}
    releases = obj["economic_release_snapshot"]
    print(json.dumps({"economic_snapshot_error": releases.get("error"),
                      "sources": [{"name": item.get("name"), "status": item.get("status"),
                                   "events": len(item.get("events", [])), "error": item.get("error")}
                                  for item in releases.get("sources", [])]}, ensure_ascii=False))
    if obj["candidate_count"] != len(obj["candidates"]):
        raise ValueError("Macro candidate count mismatch")
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n")
if __name__=="__main__": main()
