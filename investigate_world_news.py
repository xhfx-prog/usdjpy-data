#!/usr/bin/env python3
"""On-demand worldwide-news discovery BEFORE searching for FX recaps.

The output contains candidate events, NOT confirmed causes. An article's
publication/indexing timestamp is not necessarily the underlying event time.
"""
import argparse
import email.utils
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

UTC=timezone.utc
JST=ZoneInfo("Asia/Tokyo")
SCHEMA="1.0.0"
GDELT="https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE="https://news.google.com/rss/search"
OFFICIAL={
    "fed_release":"https://www.federalreserve.gov/feeds/press_all.xml",
    "fed_speech":"https://www.federalreserve.gov/feeds/speeches.xml",
}
TOPICS={
    "geopolitics":'(Iran OR Israel OR Qatar OR Ukraine OR Russia OR ceasefire OR negotiations OR sanctions OR military OR war)',
    "us_government":'(Trump OR tariffs OR Treasury OR "White House" OR "State Department" OR Congress)',
    "energy":'(OPEC OR crude OR petroleum OR Hormuz OR pipeline OR LNG OR refinery OR embargo)',
    "monetary_economy":'("Federal Reserve" OR "Bank of Japan" OR "interest rates" OR inflation OR payrolls OR employment OR recession)',
    "global_risks":'(China OR Taiwan OR "trade agreement" OR "debt crisis" OR "bank failure" OR bond OR financial)',
}
CHANNEL_EFFECTS={
    "geopolitics":["oil_supply_risk","risk_appetite","safe_haven_yen","us_rates_possible"],
    "us_government":["trade_growth_inflation","us_rates_possible","risk_appetite"],
    "energy":["crude_oil","inflation_expectations","rates_if_verified"],
    "monetary_economy":["us_jp_rate_differential","treasury_yields","policy_expectations"],
    "global_risks":["risk_appetite","capital_flows","us_jp_yields"],
    "official_fed":["us_rate_expectations","us_treasury_yields"],
}
RECAP_PAT=re.compile(r'\b(?:USD/JPY|USDJPY|forex|FX market|yen (?:gains|falls|weakens|strengthens)|dollar (?:gains|falls) (?:against|versus) yen)\b',re.I)

def parse_time(s):
    d=datetime.fromisoformat(s.replace("Z","+00:00"))
    if d.tzinfo is None:
        raise ValueError("move_at_jst requires an ISO8601 timezone offset")
    return d.astimezone(JST)

def iso(d):
    return d.isoformat(timespec="seconds")

def gdelt_time(d):
    return d.astimezone(UTC).strftime("%Y%m%d%H%M%S")

def request_bytes(url,timeout=16):
    headers={"User-Agent":"USDJPY-world-news-event-scanner/1.0",
             "Accept":"application/json, application/xml, text/xml, */*"}
    req=urllib.request.Request(url,headers=headers)
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return r.read(1500000)

def gdelt_url(topic,start,end,hints=None):
    q=TOPICS[topic]
    if hints:
        q="("+" OR ".join('"'+h.replace('"',"")+'"' for h in hints[:6])+")"
    params={"query":q,"mode":"artlist","format":"json","maxrecords":"150",
            "sort":"datedesc","startdatetime":gdelt_time(start),
            "enddatetime":gdelt_time(end)}
    return GDELT+"?"+urllib.parse.urlencode(params)

def google_url(topic,minutes,hints=None):
    if hints:
        q="("+" OR ".join(hints[:6])+")"
    else:
        q=TOPICS[topic]
    q+=" when:"+str(max(1,(minutes+59)//60))+"h"
    return GOOGLE+"?"+urllib.parse.urlencode(
        {"q":q,"hl":"en-US","gl":"US","ceid":"US:en"})

def make_article(title,url,publisher,time_value,time_kind,channel,provider,retrieved_at,language):
    title=re.sub(r"\s+"," ",title).strip()[:320]
    url=(url or "").strip()[:1200]
    if urllib.parse.urlparse(url).scheme not in ("https","http"):
        url=""
    host=urllib.parse.urlparse(url).hostname or ""
    publisher=(publisher or host).strip()[:140]
    return {"title":title,"url":url,"publisher":publisher,"domain":host,
            "source":provider,"channel":channel,"language":language,
            "source_timestamp_utc":iso(time_value.astimezone(UTC)) if time_value else None,
            "source_timestamp_kind":time_kind,"retrieved_at_utc":iso(retrieved_at)}

def parse_gdelt(raw,channel,retrieved_at):
    obj=json.loads(raw)
    if not isinstance(obj,dict) or not isinstance(obj.get("articles",[]),list):
        raise ValueError("unexpected GDELT JSON")
    out=[]
    for x in obj.get("articles",[]):
        sd=x.get("seendate")
        try:
            when=datetime.strptime(sd.replace("T","")[:14],"%Y%m%d%H%M%S").replace(tzinfo=UTC) if sd else None
        except ValueError:
            when=None
        out.append(make_article(x.get("title",""),x.get("url",""),x.get("domain",""),when,
                     "GDELT_INDEXED_AT_NOT_PUBLICATION",channel,"GDELT_DOC",
                     retrieved_at,x.get("language","")))
    return out

def parse_rss(raw,channel,retrieved_at,provider):
    root=ET.fromstring(raw)
    out=[]
    for item in root.findall(".//item"):
        txt=lambda k:(item.findtext(k) or "").strip()
        pub=txt("pubDate") or txt("published")
        try:
            when=email.utils.parsedate_to_datetime(pub).astimezone(UTC) if pub else None
        except (ValueError,TypeError):
            when=None
        out.append(make_article(unescape(txt("title")),txt("link"),
                   txt("source") or provider,when,
                   "RSS_PUBDATE_NOT_EVENT_TIME" if when else "UNKNOWN",
                   channel,provider,retrieved_at,"en"))
    return out

def classify_chronology(article,start,move,end):
    value=article.get("source_timestamp_utc")
    if not value:
        return "TIME_UNVERIFIED"
    dt=datetime.fromisoformat(value)
    if dt<start or dt>end:
        return "OUT_OF_WINDOW"
    if dt==move:
        return "SAME_MINUTE_ORDER_UNKNOWN"
    if dt<move:
        return "SOURCE_TIMESTAMP_BEFORE_MOVE_EVENT_TIME_UNVERIFIED"
    return "POST_MOVE_CANNOT_BE_TRIGGER_FROM_THIS_TIMESTAMP"

# Explanatory FX recaps belong to the final cross-check, NEVER the first search.
# These are search templates for an investigator, not evidence of causation.
def recap_followup_queries(move):
    date=move.astimezone(JST).date().isoformat()
    return [
        f'USD JPY {date} Reuters market reaction explanation',
        f'ドル円 {date} 変動 理由 市場 解説',
    ]

def classify_event_stage(title):
    """Describe evidence in a headline, not the likelihood of a future policy."""
    t=title.lower()
    action=re.search(
        r"\b(?:implemented|imposed|enacted|signed into law|entered into force|"
        r"intervened|rate check conducted|launched strikes|began attacks|"
        r"tariffs took effect|officially announced|formally approved)\b",t)
    if action:
        return "REPORTED_ACTION_VERIFY_PRIMARY_SOURCE"
    if re.search(r"\b(?:delayed|postponed|suspended|withdrawn|reversed|rescinded|"
                 r"cancelled|walked back)\b",t):
        return "REPORTED_REVERSAL_VERIFY"
    if re.search(r"\b(?:said|says|warned|warns|suggested|threatened|threatens|"
                 r"hinted|remarks|commented|reiterated|vowed)\b",t):
        return "STATEMENT_ONLY_CHECK_NOVELTY"
    return "UNCLASSIFIED_VERIFY"

def novelty_check(article):
    """A single title cannot certify novelty, surprise or implementation."""
    stage=classify_event_stage(article["title"])
    article["event_stage_headline_only"]=stage
    article["novelty_vs_prior_statement"]="NOT_CHECKED"
    article["implementation_verified"]=False
    article["market_expectation_surprise"]="NOT_CHECKED"
    article["primary_source_verified"]=False
    return stage

def ranking(article,move):
    # Priority of an investigation candidate, NOT probability of causation.
    prior=article["chronology"].startswith("SOURCE_TIMESTAMP_BEFORE")
    host=article.get("domain","")
    trusted=("reuters.com","apnews.com","bloomberg.com","ft.com","wsj.com",
             "bbc.com","aljazeera.com","federalreserve.gov","state.gov","whitehouse.gov")
    publisher=article.get("publisher","").lower()
    title=article["title"].lower()
    stage=novelty_check(article)
    score=15 if prior else 0
    if any(host.endswith(x) or x.split(".")[0] in publisher for x in trusted):
        score+=5
    if article["source"]=="OFFICIAL_RSS":
        score+=8
    # Economic and geopolitical STATE CHANGES, not routine company publicity.
    # These weights rank leads to inspect; they do NOT claim event causation.
    high_impact=r"\b(?:approv(?:e|ed|al)|accept(?:s|ed)?|agree(?:s|d)?|signs?|ceasefire|peace deal|truce|halt(?:s|ed)?|ends?|cut(?:s)?|suspend(?:s|ed)?|attack(?:s|ed)?|strike(?:s|s)?|invad(?:e|es|ed)|deploy(?:s|ed)?|sanction(?:s|ed)?|tariff(?:s)?|ban(?:s|ned)?|resum(?:e|es|ed)|reject(?:s|ed)?)\b"
    important=r"\b(?:iran|israel|qatar|russia|ukraine|taiwan|china|u\\.s\\.|united states|trump|federal reserve|boj|bank of japan|opec|crude oil|hormuz|treasury|bond yields|inflation|payrolls|cpi|interest rates)\b"
    if re.search(high_impact,title):
        score+=8
    # Statements about possible future policy are leads, not executed changes.
    # Do not amplify routine rhetoric simply because a famous official said it.
    if stage=="STATEMENT_ONLY_CHECK_NOVELTY":
        score-=8
    if stage=="REPORTED_ACTION_VERIFY_PRIMARY_SOURCE":
        score+=6
    if stage=="REPORTED_REVERSAL_VERIFY":
        score+=5
    if re.search(important,title):
        score+=5
    if len(re.findall(r"\b(?:iran|israel|qatar|russia|ukraine|united states|u\\.s\\.|trump|china|taiwan|opec)\b",title))>=2:
        score+=4
    if re.search(r"\b(?:peace|negotiat|deal|oil supply|military|diplomat|energy|interest rates|central bank)\b",title):
        score+=3
    if re.search(r"\b(?:earnings|first quarter results|launches.*etf|stock upgrade|company revenue|webinar|announces fiscal|individual share)\b",title):
        score-=12
    if RECAP_PAT.search(article["title"]):
        score-=12
    if article["channel"] in ("geopolitics","energy"):
        score+=2
    if article["source_timestamp_utc"]:
        delta=abs((move-datetime.fromisoformat(article["source_timestamp_utc"])).total_seconds())/60
        score+=max(0,5-min(delta/10,5))
    return round(score,2)

def deduplicate(rows):
    unique={}
    for article in rows:
        if not article["title"] or not article["url"]:
            continue
        p=urllib.parse.urlparse(article["url"])
        host=p.hostname or ""
        key=host+p.path.rstrip("/")
        if host.endswith("news.google.com"):
            key="google-"+re.sub("[^a-z0-9]","",article["title"].lower())[:100]
        old=unique.get(key)
        if old is None or (not old["source_timestamp_utc"] and article["source_timestamp_utc"]):
            unique[key]=article
    return list(unique.values())

def collect(move,pre_minutes=45,post_minutes=10,hints=None,
            fetcher=request_bytes,sources=("gdelt","google_rss","official_rss"),now=None):
    now=now or datetime.now(UTC)
    move=move.astimezone(UTC)
    start=move-timedelta(minutes=pre_minutes)
    end=min(move+timedelta(minutes=post_minutes),now)
    if move>now+timedelta(minutes=1):
        raise ValueError("move_at in future")
    if end<=start:
        raise ValueError("search interval invalid")
    jobs=[]
    if "gdelt" in sources and now-start<timedelta(days=89):
        for topic in TOPICS:
            jobs.append(("GDELT_DOC",topic,gdelt_url(topic,start,end)))
        if hints:
            jobs.append(("GDELT_DOC","context_hints",gdelt_url("geopolitics",start,end,hints)))
    if "google_rss" in sources:
        age=max(1,int((now-start).total_seconds()/60))
        if age<=24*60:
            for topic in TOPICS:
                jobs.append(("GOOGLE_RSS",topic,google_url(topic,age)))
            if hints:
                jobs.append(("GOOGLE_RSS","context_hints",google_url("geopolitics",age,hints)))
    if "official_rss" in sources:
        for topic,url in OFFICIAL.items():
            jobs.append(("OFFICIAL_RSS","official_fed",url))
    articles=[]
    diagnostics=[]
    def do(job):
        provider,topic,url=job
        try:
            raw=fetcher(url)
            found=(parse_gdelt(raw,topic,now) if provider=="GDELT_DOC"
                   else parse_rss(raw,topic,now,provider))
            return found,{"source":provider,"topic":topic,"status":"ok","returned":len(found)}
        except Exception as exc:
            return [],{"source":provider,"topic":topic,"status":"error","error":str(exc)[:220]}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for found,status in pool.map(do,jobs):
            articles.extend(found)
            diagnostics.append(status)
    candidates=[]
    for article in deduplicate(articles):
        article["chronology"]=classify_chronology(article,start,move,end)
        if article["chronology"]=="OUT_OF_WINDOW":
            continue
        article["priority_score_not_causal_confidence"]=ranking(article,move)
        channel=article["channel"]
        if channel=="context_hints":
            title=article["title"].lower()
            channel=("geopolitics" if re.search(r"iran|israel|qatar|ukraine|russia|peace|war|sanction",title)
                     else "monetary_economy" if re.search(r"federal reserve|boj|inflation|rate",title)
                     else "energy" if re.search(r"crude|opec|oil|pipeline",title)
                     else "global_risks")
        article["hypothesis_paths_not_verified"]=CHANNEL_EFFECTS.get(channel,[])
        article["trigger_confirmed"]=False
        candidates.append(article)
    candidates.sort(key=lambda x:(
        x["chronology"].startswith("SOURCE_TIMESTAMP_BEFORE"),
        x["priority_score_not_causal_confidence"],
        x["source_timestamp_utc"] or ""),reverse=True)
    return {
        "schema_version":SCHEMA,"scanner":"world_news_first_no_fx_cause_search",
        "generated_at_utc":iso(now),"move_at_jst":iso(move.astimezone(JST)),
        "searched_from_jst":iso(start.astimezone(JST)),
        "searched_to_jst":iso(end.astimezone(JST)),
        "hints":hints or [],"news_sources":diagnostics,
        "investigation_order":[
            "VERIFY_MOVE_ONSET_FROM_TIMESTAMPED_OHLC",
            "DISCOVER_WORLD_EVENTS_AND_OFFICIAL_RELEASES_WITHOUT_FX_RECAP_SEARCH",
            "VERIFY_EVENT_TIME_NOVELTY_IMPLEMENTATION_AND_CROSS_MARKET_REACTIONS",
            "ONLY_THEN_CROSSCHECK_FX_RECAP_EXPLANATIONS"],
        "recap_followup_queries_not_executed":recap_followup_queries(move),
        "discovery_status":"NEWS_CANDIDATES_FOUND" if candidates else "NO_VERIFIED_NEWS_CANDIDATES",
        "chronology_note":"Index/publication timestamps do not prove underlying EVENT time; post-move publication is not evidence of an initial trigger.",
        "interpretation_rule":"Verify event timestamps, competing events, novelty versus earlier official statements, action versus rhetoric, and oil/yield/equity reactions before assigning a cause. Do NOT infer irrelevance from a quiet net move: opposing catalysts may offset.",
        "candidates":candidates[:80],
        "totals":{"raw_results":len(articles),"in_window_unique":len(candidates),
           "timestamp_before_move":sum(c["chronology"].startswith("SOURCE_TIMESTAMP_BEFORE") for c in candidates),
           "timestamp_after_move":sum(c["chronology"].startswith("POST_MOVE") for c in candidates)},
        "market_crosschecks":{"oil":"NOT_FETCHED","us_2y_yield":"NOT_FETCHED",
          "us_10y_yield":"NOT_FETCHED","dxy":"NOT_FETCHED","sp500_futures":"NOT_FETCHED"},
        "confirmed_catalyst":None}

def read_move_price(path,move):
    try:
        obj=json.loads(Path(path).read_text(encoding="utf-8"))
        candles=obj.get("data",{}).get("values",[])
    except (OSError,ValueError) as exc:
        return {"status":"DATA_UNAVAILABLE","detail":str(exc)[:160]}
    bars={}
    for b in candles:
        try:
            t=datetime.strptime(b["datetime"],"%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
            bars[t]=b
        except (KeyError,ValueError):
            continue
    previous=sorted(t for t in bars if move-timedelta(minutes=6)<=t<move)
    period=sorted(t for t in bars if move<=t<move+timedelta(minutes=30))
    if not previous or not period:
        return {"status":"INSUFFICIENT_OHLC","move_at_jst":iso(move)}
    pre=float(bars[previous[-1]]["close"])
    low=min(float(bars[t]["low"]) for t in period)
    high=max(float(bars[t]["high"]) for t in period)
    return {"status":"OBSERVED","move_at_jst":iso(move),
      "price_type":"OHLC_BID_ASK_MID_UNSPECIFIED",
      "pre_move_close":round(pre,5),"first_30min_low":round(low,5),
      "first_30min_high":round(high,5),
      "up_pips":round((high-pre)*100,3),
      "down_pips":round((pre-low)*100,3)}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--move-at",required=True)
    p.add_argument("--pre-minutes",type=int,default=45)
    p.add_argument("--post-minutes",type=int,default=10)
    p.add_argument("--hints",default="")
    p.add_argument("--request-id",default="manual")
    p.add_argument("--no-network",action="store_true")
    p.add_argument("--out",default="data/news_trigger_latest.json")
    p.add_argument("--ohlc",default="data/usdjpy_1min.json")
    args=p.parse_args(argv)
    if not (5<=args.pre_minutes<=240 and 0<=args.post_minutes<=120):
        p.error("pre_minutes must be 5..240, post_minutes 0..120")
    move=parse_time(args.move_at)
    hints=[h.strip() for h in args.hints.split(",") if 2<=len(h.strip())<=36][:6]
    sources=() if args.no_network else ("gdelt","google_rss","official_rss")
    obj=collect(move,args.pre_minutes,args.post_minutes,hints,sources=sources)
    obj["request_id"]=args.request_id
    obj["price_context"]=read_move_price(args.ohlc,move)
    output=Path(args.out)
    output.parent.mkdir(exist_ok=True,parents=True)
    payload=json.dumps(obj,ensure_ascii=False,indent=2)+"\n"
    output.write_text(payload,encoding="utf-8")
    # Preserve each on-demand investigation instead of overwriting the record.
    # Separate publication-time metadata from later market-based conclusions.
    import hashlib
    archive=Path("data/news_investigations")/move.strftime("%Y-%m-%d")
    archive.mkdir(exist_ok=True,parents=True)
    suffix=hashlib.sha256(args.request_id.encode()).hexdigest()[:10]
    archived=archive/(move.strftime("%H%M")+"_"+suffix+".json")
    archived.write_text(payload,encoding="utf-8")
    print("news investigation archived:",archived)
    print("world news scan:",obj["discovery_status"],
          "candidates:",obj["totals"]["in_window_unique"],
          "feeds_ok:",sum(s["status"]=="ok" for s in obj["news_sources"]),
          "feeds_error:",sum(s["status"]=="error" for s in obj["news_sources"]))
    return 0

if __name__=="__main__":
    sys.exit(main())
