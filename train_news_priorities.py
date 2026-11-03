#!/usr/bin/env python3
"""Offline event-study trainer for world-news prioritization on USD/JPY.

Historical FX recaps are weak *teaching labels*, never proof of causation.
Pre-event world headlines are features, not automatically known trigger times.
No forecasts or inferred FX/news causal probabilities are emitted.
Python 3.11, stdlib only. News retrieval respects limited requests.
"""
import argparse
import collections
import email.utils
import json
import math
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

JST=ZoneInfo("Asia/Tokyo")
UTC=timezone.utc
GOOGLE="https://news.google.com/rss/search"
VERSION="1.0.0"
# Include economically meaningful action words. Patterns overlap and represent
# descriptive article subject, never fixed prediction of FX direction.
CATEGORIES={
  "fed_us_rates":r"\b(?:federal reserve|fed (?:chair|rate|policy)|fomc|powell|us (?:yields|treasury)|bond yields|rate cut|rate hike)\b",
  "boj_japan_rates":r"\b(?:bank of japan|boj|ueda|japan(?:ese)? (?:interest rates|yields)|jgb|yield curve)\b",
  "us_inflation":r"\b(?:cpi|consumer price|pce|inflation|producer price|ppi)\b",
  "us_employment":r"\b(?:payrolls|jobs report|unemployment|jobless|employment (?:data|figures|report)|jolts)\b",
  "us_politics_tariffs":r"\b(?:trump|tariff|trade war|white house|us congress|us treasury secretary|fiscal stimulus|tax cuts)\b",
  "geopolitics_war_peace":r"\b(?:iran|israel|qatar|russia|ukraine|ceasefire|peace talks|military strike|sanctions|taiwan|strait of hormuz)\b",
  "energy_supply":r"\b(?:crude oil|oil price|opec|brent|wti|petroleum|energy supply|lng|oil production)\b",
  "risk_financial_system":r"\b(?:bank failure|bank crisis|credit crunch|equity selloff|stock market plunge|risk aversion|safe haven|financial crisis)\b",
  "china_economy":r"\b(?:china(?:'s)? economy|chinese economic|yuan|pboc|china (?:tariff|trade|growth)|beijing)\b",
  "intervention":r"\b(?:fx intervention|currency intervention|japan interven|finance ministry intervention|mof intervention|rate check)\b",
}
COMPILED={k:re.compile(v,re.I) for k,v in CATEGORIES.items()}
RECAP=re.compile(r"(?:dollar.{0,24}yen|yen.{0,30}dollar|usd/?jpy|japanese yen|dollar.{0,24}japan)",re.I)

def utc_iso(dt):
    return dt.astimezone(UTC).isoformat(timespec="seconds")

def parse_jst_bar(s):
    return datetime.strptime(s,"%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)

def load_hourly(root,cutoff=None,now=None):
    data=[]
    for path in sorted((Path(root)/"history_1h").glob("*.json")):
        try:
            obj=json.loads(path.read_text(encoding="utf-8"))
            if obj.get("interval")!="1h" or obj.get("timezone")!="Asia/Tokyo":
                continue
            data+=obj.get("data",{}).get("values",[])
        except (OSError,ValueError):
            continue
    unique={}
    for row in data:
        try:
            dt=parse_jst_bar(row["datetime"])
            if cutoff and dt<cutoff:
                continue
            if now and dt+timedelta(hours=1)>now:
                continue
            o,h,l,c=(float(row[x]) for x in ("open","high","low","close"))
            if not (0<l<=o<=h and l<=c<=h):
                continue
            if dt.astimezone(ZoneInfo("America/New_York")).weekday()==5:
                continue
            unique[row["datetime"]]={"t":dt,"open":o,"high":h,"low":l,
                                     "close":c,"range_pips":round((h-l)*100,3)}
        except (KeyError,ValueError,TypeError):
            continue
    return sorted(unique.values(),key=lambda b:b["t"])

def pick_samples(bars,per_month=2,period_end=None):
    """Select shocks and matched-hour quiet controls without future price labels."""
    groups=collections.defaultdict(list)
    for b in bars:
        if period_end and b["t"]>period_end:
            continue
        groups[b["t"].strftime("%Y-%m")].append(b)
    samples=[]
    for month,rows in sorted(groups.items()):
        if len(rows)<75:
            continue
        shocks=[]
        used_dates=set()
        for b in sorted(rows,key=lambda x:(-x["range_pips"],x["t"])):
            if b["t"].date() in used_dates:
                continue
            used_dates.add(b["t"].date())
            shocks.append(b)
            if len(shocks)==per_month:
                break
        controls=[]
        for shock in shocks:
            candidates=[x for x in rows if x["t"].hour==shock["t"].hour
                        and x["t"].date() not in used_dates
                        and all(x["t"].date()!=y["t"].date() for y in controls)]
            if not candidates:
                candidates=[x for x in rows if x["t"].date() not in used_dates
                            and all(x["t"].date()!=y["t"].date() for y in controls)]
            if candidates:
                controls.append(min(candidates,key=lambda x:(x["range_pips"],abs(x["t"].weekday()-shock["t"].weekday()),x["t"])))
        for kind,subset in (("large_move",shocks),("quiet_control",controls)):
            for x in subset:
                samples.append({"kind":kind,"month":month,"bar_start_jst":x["t"].isoformat(),
                                "hour_jst":x["t"].hour,
                                "range_pips":x["range_pips"],
                                "hour_return_pips":round((x["close"]-x["open"])*100,3)})
    return sorted(samples,key=lambda x:x["bar_start_jst"])


def daily_bars(hourly):
    """Aggregate provider 1h observations to JST calendar-day study bars.

    This is a retrospective *daily* association study, NOT a minute-level
    news-catalyst study. Exclude partial days with fewer than 16 observations.
    """
    days=collections.defaultdict(list)
    for b in hourly:
        days[b["t"].date()].append(b)
    out=[]
    for day,bs in sorted(days.items()):
        bs.sort(key=lambda x:x["t"])
        if len(bs)<16:
            continue
        start=datetime.combine(day,datetime.min.time(),tzinfo=JST)
        out.append({"t":start,"high":max(x["high"] for x in bs),
                    "low":min(x["low"] for x in bs),"open":bs[0]["open"],
                    "close":bs[-1]["close"],"hour_count":len(bs),
                    "range_pips":round((max(x["high"] for x in bs)-
                                         min(x["low"] for x in bs))*100,3)})
    return out


def daily_samples(hourly,per_month=2):
    days=daily_bars(hourly)
    groups=collections.defaultdict(list)
    for day in days:
        groups[day["t"].strftime("%Y-%m")].append(day)
    out=[]
    for month,bs in sorted(groups.items()):
        if len(bs)<12:
            continue
        extremes=sorted(bs,key=lambda x:(-x["range_pips"],x["t"]))[:per_month]
        used={x["t"].date() for x in extremes}
        controls=[]
        for shock in extremes:
            remaining=[x for x in bs if x["t"].date() not in used]
            if not remaining:
                continue
            same=[x for x in remaining if x["t"].weekday()==shock["t"].weekday()]
            choice=min(same or remaining,key=lambda x:(x["range_pips"],x["t"]))
            controls.append(choice)
            used.add(choice["t"].date())
        for kind,subset in (("large_move",extremes),("quiet_control",controls)):
            for x in subset:
                out.append({"kind":kind,"month":month,
                            "bar_start_jst":x["t"].isoformat(),
                            "hour_jst":None,"study_date_jst":x["t"].date().isoformat(),
                            "range_pips":x["range_pips"],
                            "hour_return_pips":None,
                            "daily_return_pips":round((x["close"]-x["open"])*100,3),
                            "study_hours_observed":x["hour_count"]})
    return sorted(out,key=lambda x:x["bar_start_jst"])


def analyze_daily_articles(case,now,fetcher=None,sleep_seconds=3):
    """Retrospective same-day story topics; no publication-to-minute inference."""
    if fetcher is None:
        fetcher=fetch
    day=datetime.fromisoformat(case["bar_start_jst"])
    queries=(
        ("world",'(Iran OR Israel OR Qatar OR Trump OR tariff OR "Federal Reserve" OR "Bank of Japan" OR OPEC OR crude OR Treasury OR inflation OR payrolls OR ceasefire)'),
        ("fx_recap",'("dollar yen" OR "japanese yen" OR "USD/JPY")'),
    )
    allworld=[]
    allrecap=[]
    diagnostics=[]
    for kind,query in queries:
        url=google_url(query,day,day+timedelta(hours=15))
        try:
            rows=parse_rss(fetcher(url))
            diagnostics.append({"type":kind,"status":"ok","articles":len(rows)})
        except Exception as exc:
            rows=[]
            diagnostics.append({"type":kind,"status":"error",
                                "error":str(exc)[:140]})
        seen=set()
        for row in rows:
            published=datetime.fromisoformat(row["pub_utc"]).astimezone(JST)
            if published.date()!=day.date():
                continue
            ident=re.sub("[^a-z0-9]","",row["title"].lower())[:120]
            if ident in seen:
                continue
            seen.add(ident)
            entry={"title":row["title"],"url":row["url"],"source_pub_utc":row["pub_utc"],
                   "category_hints":cats(row["title"]),"intraday_event_time_verified":False}
            if kind=="world":
                allworld.append(entry)
            elif RECAP.search(row["title"]):
                allrecap.append(entry)
        if sleep_seconds:
            time.sleep(sleep_seconds)
    topic_set=sorted({c for row in allworld for c in row["category_hints"]})
    recap_set=sorted({c for row in allrecap for c in row["category_hints"]})
    return {"sources":diagnostics,
            "daily_topics":topic_set,"recap_explanations":recap_set,
            "world_daily_headlines":allworld[:12],
            "fx_daily_educational_headlines":allrecap[:12],
            "daily_world_count":len(allworld),
            "daily_fx_recap_count":len(allrecap),
            "chronology":"CALENDAR_DATE_ONLY_NOT_A_VERIFIED_EARLY_TRIGGER"}


def google_url(query,start,end):
    # Google after/before are day-level filters, not proof of intraday availability.
    q=query+" after:"+start.astimezone(JST).date().isoformat()
    q+=" before:"+(end.astimezone(JST).date()+timedelta(days=1)).isoformat()
    return GOOGLE+"?"+urllib.parse.urlencode({"q":q,"hl":"en-US","gl":"US","ceid":"US:en"})

def fetch(url,timeout=18):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; USDJPY-EvidenceStudy/1.0)",
                                            "Accept":"application/rss+xml,application/xml,text/xml"})
    with urllib.request.urlopen(req,timeout=timeout) as res:
        return res.read(2000000)

def parse_rss(raw):
    root=ET.fromstring(raw)
    results=[]
    for item in root.findall(".//item"):
        title=" ".join((item.findtext("title") or "").split())
        url=(item.findtext("link") or "").strip()
        ts=item.findtext("pubDate")
        try:
            pub=email.utils.parsedate_to_datetime(ts).astimezone(UTC) if ts else None
        except (TypeError,ValueError):
            pub=None
        if title and pub:
            results.append({"title":title[:300],"url":url[:800],
                            "pub_utc":utc_iso(pub)})
    return results

def cats(title):
    return sorted(k for k,v in COMPILED.items() if v.search(title))

def analyze_articles(case,now,fetcher=fetch,sleep_seconds=3):
    t=datetime.fromisoformat(case["bar_start_jst"])
    before=t-timedelta(hours=3)
    after=t+timedelta(hours=9)
    # One worldwide pre-move news search and one historically educational FX
    # recap search. Recaps can explain but cannot be pre-move signal.
    queries=(
        ("world", '(Iran OR Israel OR Qatar OR Trump OR tariff OR "Federal Reserve" OR "Bank of Japan" OR OPEC OR crude OR Treasury OR inflation OR payrolls OR ceasefire)'),
        ("fx_recap", '("dollar yen" OR "japanese yen" OR "USD/JPY")'),
    )
    report={"sources":[],"world_prestart":[],"world_within_hour":[],
            "recap_post_hour":[],"world_after_hour":[],
            "prestart_categories":[],"hour_categories":[],"recap_explanations":[]}
    for qtype,q in queries:
        url=google_url(q,before,after)
        try:
            entries=parse_rss(fetcher(url))
            report["sources"].append({"type":qtype,"status":"ok","articles":len(entries)})
        except Exception as exc:
            report["sources"].append({"type":qtype,"status":"error","error":str(exc)[:140]})
            entries=[]
        dedup=set()
        for row in entries:
            published=datetime.fromisoformat(row["pub_utc"])
            if published<before or published>after:
                continue
            key=re.sub(r"[^a-z0-9]","",row["title"].lower())[:120]
            if key in dedup:
                continue
            dedup.add(key)
            c=cats(row["title"])
            rec={"pub_utc":row["pub_utc"],"title":row["title"],"url":row["url"],
                 "categories":c}
            if qtype=="world":
                if published<t:
                    report["world_prestart"].append(rec)
                elif published<t+timedelta(hours=1):
                    report["world_within_hour"].append(rec)
                else:
                    report["world_after_hour"].append(rec)
            else:
                if published>=t+timedelta(hours=1) and RECAP.search(row["title"]):
                    report["recap_post_hour"].append(rec)
        if sleep_seconds:
            time.sleep(sleep_seconds)
    # Bound retained metadata; entire articles are NOT downloaded/reproduced.
    report["prestart_categories"]=sorted({c for x in report["world_prestart"] for c in x["categories"]})
    report["hour_categories"]=sorted({c for x in report["world_within_hour"] for c in x["categories"]})
    report["recap_explanations"]=sorted({c for x in report["recap_post_hour"] for c in x["categories"]})
    for key in ("world_prestart","world_within_hour","world_after_hour","recap_post_hour"):
        report[key]=report[key][:8]
    return report

def enrich(samples,now,fetcher=fetch,sleep_seconds=3,max_cases=None,granularity="daily"):
    output=[]
    failures=0
    for i,case in enumerate(samples):
        if max_cases is not None and i>=max_cases:
            break
        article=(analyze_daily_articles(case,now,fetcher,sleep_seconds)
                 if granularity=="daily" else analyze_articles(case,now,fetcher,sleep_seconds))
        obj={**case,"news":article}
        output.append(obj)
        failures+=sum(s["status"]=="error" for s in article["sources"])
        print("case",i+1,len(samples),case["month"],case["kind"],
              "range",case["range_pips"],
              "topics",article.get("daily_topics",article.get("prestart_categories",[])),
              "errors",failures,flush=True)
        if failures>=12:
            break
    return output,failures

def fit(samples,holdout_from="2026-08"):
    train=[x for x in samples if x["month"]<holdout_from]
    test=[x for x in samples if x["month"]>=holdout_from]
    outcome={"train_cases":len(train),"holdout_cases":len(test),
             "usable":False,"reason":None,"category_stats":{},"holdout_metrics":{}}
    def counts(rows,cat):
        yes=[r for r in rows if r["kind"]=="large_move"]
        no=[r for r in rows if r["kind"]=="quiet_control"]
        # prestart only: ex-post recap never enters an actionable live prior
        big=sum(cat in r["news"].get("daily_topics",r["news"].get("prestart_categories",[])) for r in yes)
        calm=sum(cat in r["news"].get("daily_topics",r["news"].get("prestart_categories",[])) for r in no)
        return big,calm,len(yes),len(no)
    for cat in CATEGORIES:
        big,calm,nb,nc=counts(train,cat)
        rate_big=(big+1)/(nb+2) if nb else 0
        rate_calm=(calm+1)/(nc+2) if nc else 0
        logratio=math.log(rate_big/rate_calm) if nb and nc else 0
        outcome["category_stats"][cat]={
          "shock_cases_with_prestart_news":big,"quiet_cases_with_prestart_news":calm,
          "sample_shocks":nb,"sample_quiet":nc,
          "smoothed_likelihood_ratio":round(math.exp(logratio),3) if nb and nc else None,
          "weight_prior_points":round(max(-4,min(4,3*logratio)),2),
          "recap_weak_label_shock_cases":sum(cat in r["news"]["recap_explanations"]
                                              for r in train if r["kind"]=="large_move")}
    # No learning from a few unrepresentative or inaccessible articles.
    good_big=sum(r["kind"]=="large_move" and any(z["status"]=="ok" and z["articles"]>0
                   for z in r["news"]["sources"]) for r in train)
    good_quiet=sum(r["kind"]=="quiet_control" and any(z["status"]=="ok" and z["articles"]>0
                   for z in r["news"]["sources"]) for r in train)
    outcome["source_covered_shocks"]=good_big
    outcome["source_covered_quiet"]=good_quiet
    if good_big<12 or good_quiet<12 or len(test)<10:
        outcome["reason"]="INSUFFICIENT_HISTORICAL_NEWS_OR_HOLDOUT_COVERAGE"
        return outcome
    def predictor(row):
        cats_present=row["news"].get("daily_topics",row["news"].get("prestart_categories",[]))
        score=sum(outcome["category_stats"][c]["weight_prior_points"]
                  for c in cats_present)
        return max(-12,min(12,score))
    baseline=sum(r["kind"]=="large_move" for r in test)/len(test)
    pos=[predictor(x) for x in test if x["kind"]=="large_move"]
    neg=[predictor(x) for x in test if x["kind"]=="quiet_control"]
    # Fraction of correctly ordered shock/quiet pairs, with ties half credit.
    if not pos or not neg:
        outcome["reason"]="NO_HOLDOUT_PAIR_COVERAGE"
        return outcome
    auc=sum(1 if a>b else .5 if a==b else 0 for a in pos for b in neg)/(len(pos)*len(neg))
    outcome["holdout_metrics"]={"auc_priority_shock_vs_quiet":round(auc,3),
        "chance_baseline":0.5,"n_shocks":len(pos),"n_quiet":len(neg),
        "period":holdout_from+" onward"}
    # Only turn weights on if they outperform chance on strictly future cases.
    outcome["usable"]=auc>=.57 and len(pos)>=5 and len(neg)>=5
    outcome["reason"]="HOLDOUT_PASS_ASSOCIATION_ONLY" if outcome["usable"] else "HOLDOUT_NOT_BETTER_THAN_CHANCE"
    return outcome

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",default="data")
    p.add_argument("--out",default="data/news_priority_training_latest.json")
    p.add_argument("--model-out",default="data/news_priority_model.json")
    p.add_argument("--per-month",type=int,default=2)
    p.add_argument("--granularity",choices=["daily","hourly"],default="daily")
    p.add_argument("--max-cases",type=int,default=52)
    p.add_argument("--sleep-seconds",type=float,default=3)
    p.add_argument("--holdout-from",default="2026-08")
    p.add_argument("--asof",default=None)
    args=p.parse_args()
    now=datetime.fromisoformat(args.asof) if args.asof else datetime.now(JST)
    now=now.astimezone(JST)
    bars=load_hourly(args.data_dir,
         cutoff=now-timedelta(days=365),now=now)
    samples=(daily_samples(bars,per_month=args.per_month) if args.granularity=="daily"
             else pick_samples(bars,per_month=args.per_month,period_end=now-timedelta(hours=2)))
    cases,errors=enrich(samples,now,sleep_seconds=args.sleep_seconds,
                        max_cases=args.max_cases,granularity=args.granularity)
    learned=fit(cases,holdout_from=args.holdout_from)
    obj={"schema_version":VERSION,"generated_at_utc":utc_iso(datetime.now(UTC)),
         "requested_lookback_days":365,"market_bars_available":len(bars),
         "actual_oldest_jst":bars[0]["t"].isoformat() if bars else None,
         "actual_newest_jst":bars[-1]["t"].isoformat() if bars else None,
         "sampling":("largest daily range vs quiet matched-weekday controls, per month"
                     if args.granularity=="daily" else
                     "largest hourly range vs quiet matched-hour controls, per month"),
         "granularity":args.granularity,
         "hourly_bar_not_exact_move_onset":True,
         "article_coverage":"Google News RSS historical headlines only; not full article bodies",
         "news_time_rule":"Historical daily association only; RSS publication time cannot certify earliest dissemination",
         "recap_usage":"Ex-post educational weak labels only; NEVER train with recaps as prior observable news",
         "network_errors":errors,"cases":cases,"fit":learned}
    out=Path(args.out)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    model={"schema_version":VERSION,"source":str(out),"generated_at_utc":obj["generated_at_utc"],
           "training_period_end_jst":now.date().isoformat(),
           "allow_live_prior":False,
           "shadow_model_ready":learned["usable"],
           "holdout":learned["holdout_metrics"],"reason":learned["reason"],
           "adoption_gate":"Daily retrospective associations must pass prospective intraday validation before affecting live ranking",
           "category_prior_points":{k:v["weight_prior_points"] for k,v in learned["category_stats"].items()},
           "note":"Association-only. Never assert that commentary proves causation."}
    modelpath=Path(args.model_out)
    modelpath.parent.mkdir(parents=True,exist_ok=True)
    modelpath.write_text(json.dumps(model,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("TRAINING_RESULT",json.dumps({"cases":len(cases),"errors":errors,
       "train":learned["train_cases"],"holdout":learned["holdout_cases"],
       "usable":learned["usable"],"reason":learned["reason"],
       "metrics":learned["holdout_metrics"]},ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
