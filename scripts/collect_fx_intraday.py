import datetime as dt
import json
import math
import pathlib
import urllib.request
import urllib.parse
import statistics
from concurrent.futures import ThreadPoolExecutor

OUT=pathlib.Path("data/fx_intraday.json")
SYMBOLS={"usd_jpy":"USDJPY=X","eur_jpy":"EURJPY=X","gbp_jpy":"GBPJPY=X","eur_usd":"EURUSD=X","gbp_usd":"GBPUSD=X","yen_future":"6J=F","dxy":"DX-Y.NYB","us10y":"^TNX"}
def fetch(symbol,now):
    url="https://query1.finance.yahoo.com/v8/finance/chart/"+urllib.parse.quote(symbol,safe="")+"?interval=5m&range=5d"
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=25) as r:
        x=json.load(r)["chart"]["result"][0]
    if x["meta"]["symbol"] not in ({symbol,"JPY=X","USDJPY=X"} if symbol in ("JPY=X","USDJPY=X") else {symbol}) or x["meta"].get("dataGranularity")!="5m":
        raise ValueError("Symbol or granularity mismatch: "+str((symbol,x["meta"].get("symbol"),x["meta"].get("dataGranularity"))))
    q=x["indicators"]["quote"][0]
    bars={}
    # Exclude last 20 minutes conservatively because source quotes can be delayed.
    for i,t in enumerate(x["timestamp"]):
        if t%300 or t+1200>now: continue
        o,h,l,c=[q[k][i] for k in ("open","high","low","close")]
        if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in (o,h,l,c)): continue
        if not l<=min(o,c)<=max(o,c)<=h: continue
        v=q.get("volume",[None]*len(x["timestamp"]))[i]
        bars[str(t)]={"close":c,"open":o,"high":h,"low":l,"volume":v if isinstance(v,int) and v>=0 else None}
    if not bars: raise ValueError("No validated completed bars")
    return {"symbol":symbol,"url":url,"latest_epoch":max(map(int,bars)),"bars":bars,"exchange":x["meta"].get("exchangeName"),"instrument":x["meta"].get("instrumentType"),"source_delay":"delayed_or_unknown"}
def analyze(series):
    spot=series.get("usd_jpy",{}).get("bars",{})
    def move(name,t,n):
        b=series.get(name,{}).get("bars",{})
        if not all(str(t-i*300) in b for i in range(n+1)): return None
        return b[str(t)]["close"]/b[str(t-n*300)]["close"]-1
    events=[]
    for t in sorted(map(int,spot)):
        n=6
        if not all(str(t-i*300) in spot for i in range(n+1)): continue
        pips=100*(spot[str(t)]["close"]-spot[str(t-n*300)]["close"])
        if pips>-20: continue
        moves={k:move(k,t,n) for k in series}
        cross=[moves.get(k) for k in ("eur_jpy","gbp_jpy")]
        dollar=[moves.get(k) for k in ("eur_usd","gbp_usd")]
        label="insufficient_or_mixed"
        if all(x is not None for x in cross+dollar):
            if all(x<-.0007 for x in cross) and all(abs(x)<.0007 for x in dollar): label="yen_strength_consistent"
            elif all(x>.0007 for x in dollar) and all(abs(x)<.0007 for x in cross): label="dollar_weakness_consistent"
            elif all(x<-.0007 for x in cross) and all(x>.0007 for x in dollar): label="mixed_yen_strength_and_dollar_weakness"
        f=series.get("yen_future",{}).get("bars",{})
        def vol(end):
            a=[f.get(str(end-i*300),{}).get("volume") for i in range(n)]
            return sum(a) if all(isinstance(v,int) for v in a) else None
        current=vol(t)
        prior=[vol(t-j*86400) for j in range(1,5)]
        prior=[v for v in prior if v is not None and v>0]
        ratio=current/statistics.mean(prior) if current is not None and len(prior)>=3 else None
        expected={"dxy":-1,"us10y":-1,"yen_future":1}
        crosschecks={k:("UNAVAILABLE" if moves.get(k) is None else "FLAT" if moves[k]==0 else "ALIGNED" if moves[k]*direction>0 else "OPPOSED") for k,direction in expected.items()}
        item={"directional_crosschecks_not_causality":crosschecks,"end_bar_start_utc":dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat(),"window_minutes":30,"usd_jpy_pips":round(pips,2),"returns":moves,"classification":label,"yen_futures_volume":current,"same_utc_time_baseline_days":len(prior),"volume_ratio":ratio}
        if events and t-events[-1][0]<=1800:
            if pips<events[-1][1]["usd_jpy_pips"]: events[-1]=(t,item)
        else: events.append((t,item))
    return [v for _,v in events][-30:]
def main():
    now=int(dt.datetime.now(dt.timezone.utc).timestamp())
    series={};errors={}
    def collect(item):
        name,symbol=item
        try: return name,fetch(symbol,now),None
        except Exception as e: return name,None,str(e)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name,result,error in pool.map(collect,SYMBOLS.items()):
            if error is not None: errors[name]=error
            else: series[name]=result
    if not series: raise RuntimeError(str(errors))
    payload={"retrieved_at_utc":dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat(),"provider":"Yahoo Finance unofficial chart endpoint; delayed; not official CME or EBS; futures active contract only","series":series,"errors":errors,"events":analyze(series),"limitations":"30-minute returns are descriptive not causal. Futures volume is not spot volume or all-contract volume. Baseline uses same UTC time, not holiday/DST adjusted."}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload,separators=(",",":"))+"\n")
    print("sources",list(series),"errors",errors,"events",len(payload["events"]))
if __name__=="__main__": main()
