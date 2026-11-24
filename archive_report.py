"""Archive the canonical production analysis at scheduled JST report slots."""
import json, os, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
JST=timezone(timedelta(hours=9))
now=datetime.now(JST)
slot={"0 21 * * 0-4":6,"0 0 * * 1-5":9}.get(os.environ.get("SCHEDULE"))
if slot is None: raise SystemExit("Unknown scheduled slot")
target=now.replace(hour=slot,minute=0,second=0,microsecond=0)
if not 0<=(now-target).total_seconds()<=1800: raise SystemExit("Scheduled slot missed; no mislabeled snapshot")
url="https://usdjpy-chart.xxhappyfrenzyxx.workers.dev/mcp"
rpc={"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_usdjpy_analysis","arguments":{"minute_bars":1200,"hourly_bars":200,"daily_bars":200}}}
req=urllib.request.Request(url,data=json.dumps(rpc).encode(),headers={"Content-Type":"application/json"},method="POST")
with urllib.request.urlopen(req,timeout=90) as response: result=json.load(response)["result"]
if result.get("isError"): raise RuntimeError(str(result.get("content"))[:500])
data=result["structuredContent"]
assert data["freshness_ok"] and data["analysis_integration"]["python_snapshot_lag_in_candles"]==0
projection=json.loads(result["content"][0]["text"])
root=Path("data/report_snapshots")/target.strftime("%Y-%m-%d")
root.mkdir(parents=True,exist_ok=True)
path=root/f"{slot:02d}.json"
if path.exists(): raise SystemExit("Snapshot already exists")
record={"scheduled_at_jst":target.isoformat(),"captured_at_jst":datetime.now(JST).isoformat(),"report":projection}
if slot==9:
    prior=root/"06.json"
    if prior.exists():
        a=json.loads(prior.read_text())["report"]
        b=projection
        record["comparison"]={"status":"CAPTURED_SNAPSHOTS","06_bar":a["latest_confirmed_1min_bar"],"09_bar":b["latest_confirmed_1min_bar"],"close_change_pips":round((b["live_overlay"]["latest_close"]-a["live_overlay"]["latest_close"])*100,3),"06_direction":a.get("parent_direction"),"09_direction":b.get("parent_direction"),"exact_six_to_nine":b.get("six_to_nine")}
    else: record["comparison"]={"status":"MISSING_06_SNAPSHOT"}
path.write_text(json.dumps(record,ensure_ascii=False,indent=2)+"\n")
print(path)
