#!/usr/bin/env python3
"""Smoke-test the production Cloudflare MCP without reading GitHub or Twelve Data credentials."""
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

url = "https://usdjpy-chart.xxhappyfrenzyxx.workers.dev/mcp"
rpc = {
    "jsonrpc": "2.0", "id": 37, "method": "tools/call",
    "params": {"name": "get_usdjpy_analysis",
               "arguments": {"minute_bars": 12, "five_minute_bars": 3,
                             "fifteen_minute_bars": 3, "hourly_bars": 5,
                             "four_hour_bars": 4, "daily_bars": 5}}
}
# Assert the model is offered only the canonical current-analysis tool
# plus the historical archive reader. The old handlers may remain internally.
list_rpc = {"jsonrpc":"2.0","id":36,"method":"tools/list","params":{}}
list_req = urllib.request.Request(url, data=json.dumps(list_rpc).encode("utf-8"),
    headers={"Content-Type":"application/json",
             "Accept":"application/json, text/event-stream"},
    method="POST")
with urllib.request.urlopen(list_req, timeout=20) as response:
    tool_list = json.load(response)
names = {t["name"] for t in tool_list["result"]["tools"]}
assert names == {"get_usdjpy_analysis", "get_usdjpy_history"}, names

req = urllib.request.Request(url, data=json.dumps(rpc).encode("utf-8"),
    headers={"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream",
             "User-Agent": "USDJPY-MCP-Live-Smoke/1"},
    method="POST")
with urllib.request.urlopen(req, timeout=50) as response:
    message = json.load(response)
assert message.get("jsonrpc") == "2.0" and message.get("id") == 37, message
result = message.get("result", {})
if result.get("isError") is True:
    raise RuntimeError("MCP integration returned error: " + str(result.get("content"))[:600])
data = result.get("structuredContent")
if data is None:
    data = json.loads(result["content"][0]["text"])
assert data["symbol"] == "USD/JPY", data
assert data["freshness_ok"] is True, data.get("analysis_integration")
assert data["latest_confirmed_1min_bar"] == data["live_overlay"]["last_confirmed_1min_start_jst"]
assert 0 <= data["twelve_data_requests"] <= 1
assert len(data["series"]) == 6
integrated = data["analysis_integration"]
assert integrated["python_engine"] == "analyze_usdjpy.py (Cloudflare Python Worker)"
assert integrated["python_snapshot_source"] == "PYTHON_WORKER_INLINE"
assert "python_snapshot_lag_in_candles" in integrated
assert integrated["requires_on_demand_python_refresh"] is False
assert integrated["full_python_engine_recomputed_for_current_1min"] is True
assert integrated["python_snapshot_lag_in_candles"] == 0
assert integrated["single_mcp_call_completed"] is True
assert "market_structure" in data["python_analysis_snapshot"]
assert data["python_analysis_snapshot"]["data_quality"]["last_confirmed_1min_start_jst"] == data["latest_confirmed_1min_bar"]
audit = {
    "tested_at_utc": datetime.now(timezone.utc).isoformat(),
    "status": "PASS",
    "tool": "get_usdjpy_analysis",
    "latest_confirmed_1min_start_jst": data["latest_confirmed_1min_bar"],
    "python_snapshot_source": integrated["python_snapshot_source"],
    "python_snapshot_lag_in_candles": integrated["python_snapshot_lag_in_candles"],
    "python_engine_recomputed_for_current_1min": integrated["full_python_engine_recomputed_for_current_1min"],
    "twelve_data_requests": data["twelve_data_requests"],
    "all_six_timeframes_present": True,
}
Path("data/mcp_integration_smoke_status.json").write_text(
    json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(audit, ensure_ascii=False))
