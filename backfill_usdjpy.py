import os,json,time,urllib.request,urllib.parse
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
JST=ZoneInfo("Asia/Tokyo"); NY=ZoneInfo("America/New_York")
now=datetime.now(JST); first=(now-timedelta(days=56)).date()
folder=Path("data/history_1min"); folder.mkdir(parents=True,exist_ok=True)
def closed(t):
 n=t.astimezone(NY); d=n.weekday()
 return d==5 or (d==4 and n.hour>=17) or (d==6 and n.hour<17)
def clean(c):
 try:
  t=datetime.strptime(c["datetime"],"%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
  o,h,l,z=[float(c[x]) for x in ("open","high","low","close")]
  return 0<l<=o<=h and l<=z<=h and not closed(t) and t+timedelta(minutes=1)<=now
 except (ValueError,KeyError,TypeError): return False
def read(path):
 if not path.exists(): return []
 return json.loads(path.read_text(encoding="utf-8")).get("data",{}).get("values",[])
def save(path,date,bars):
 obj={"source":"Twelve Data","symbol":"USD/JPY","interval":"1min","timezone":"Asia/Tokyo","session_date_jst":date,"fetched_at":datetime.now(timezone.utc).isoformat(),"data":{"status":"ok","values":bars}}
 path.write_text(json.dumps(obj,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
count=0
for i in range(0,56,2):
 d=first+timedelta(days=i)
 start=datetime(d.year,d.month,d.day,tzinfo=JST)
 end=start+timedelta(days=2)-timedelta(minutes=1)
 if start>=now: break
 if all(closed(start+timedelta(hours=h)) for h in range(48)): continue
 p=urllib.parse.urlencode({"symbol":"USD/JPY","interval":"1min","timezone":"Asia/Tokyo","start_date":start.strftime("%Y-%m-%dT%H:%M:%S"),"end_date":end.strftime("%Y-%m-%dT%H:%M:%S"),"apikey":os.environ["TWELVE_API_KEY"]})
 req=urllib.request.Request("https://api.twelvedata.com/time_series?"+p,headers={"User-Agent":"USDJPY-Backfill"})
 with urllib.request.urlopen(req,timeout=50) as resp: data=json.load(resp)
 if data.get("status")!="ok": raise RuntimeError(str(data.get("message",data)))
 values=[v for v in data.get("values",[]) if clean(v)]
 groups={}
 for v in values: groups.setdefault(v["datetime"][:10],[]).append(v)
 for day,v in groups.items():
  path=folder/(day+".json")
  merged={x["datetime"]:x for x in read(path) if clean(x)}
  for x in v: merged[x["datetime"]]=x
  save(path,day,[merged[k] for k in sorted(merged,reverse=True)])
 count+=1; print(d,"returned",len(values),"requests",count,flush=True)
 time.sleep(10)
sessions=[]
for path in sorted(folder.glob("????-??-??.json"),reverse=True):
 v=read(path)
 if v: sessions.append({"date_jst":path.stem,"path":str(path),"count":len(v),"first":v[-1]["datetime"],"last":v[0]["datetime"]})
Path("data/history_1min_index.json").write_text(json.dumps({"updated_at":datetime.now(timezone.utc).isoformat(),"retention_calendar_days":56,"sessions":sessions},separators=(",",":")),encoding="utf-8")
print("COMPLETE",count,"requests",len(sessions),"days")
