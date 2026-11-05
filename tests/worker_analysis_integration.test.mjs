import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";

const code = readFileSync("cloudflare/worker.js", "utf8");
const start = code.indexOf("async function analysisWithLiveBars(");
const end = code.indexOf("// Historical OHLC exclusively from GitHub.", start);
assert.ok(start >= 0 && end > start, "Python/live integration must exist");
const factory = new Function("bundle", "github", "count", "timestamp",
  code.slice(start, end) + "return analysisWithLiveBars;");

const current = {
  datetime: "2026-11-05 22:38:00",
  open: "158.40", high: "158.48", low: "158.39", close: "158.43"
};
const live = {
  fetched_at: "2026-11-05T13:40:00Z", freshness_ok: true,
  twelve_data_requests: 1, twelve_cache_hit: false,
  series: Object.fromEntries(["1min","5min","15min","1h","4h","1day"]
    .map(x => [x, {values: x === "1min" ? [current] : []}]))
};
function analysis(at, close) {
  return {
    instrument: "USD/JPY",
    generated_at_utc: "2026-11-05T13:41:00Z",
    analysis_asof_jst: "2026-11-05T22:41:00+09:00",
    data_quality: {
      last_confirmed_1min_start_jst: at,
      last_close: close
    },
    market_structure: { timeframe_assessments: { "1h": {state: "UP"} } }
  };
}
function analyzer(input, scheduled) {
  return factory(
    async () => live,
    async path => path.includes("analysis_live") ? input : scheduled,
    (n, defaultValue) => Number.isInteger(n) && n > 0 ? n : defaultValue,
    s => Date.parse(s.replace(" ", "T") + "+09:00")
  );
}

test("matching request-bound Python recomputation is reported current", async () => {
  const requested = {
    request_id: "mcp-test-1",
    recomputed_from_live_1min: true,
    last_confirmed_1min_start_jst: current.datetime,
    input_live_fetched_at: live.fetched_at,
    analysis: analysis(current.datetime, 158.43)
  };
  const result = await analyzer(requested, analysis("2026-11-05 22:12:00",158.39))();
  assert.equal(result.analysis_integration.python_snapshot_source, "PYTHON_ON_DEMAND");
  assert.equal(result.analysis_integration.python_snapshot_request_id,"mcp-test-1");
  assert.equal(result.analysis_integration.python_snapshot_lag_in_candles,0);
  assert.equal(result.analysis_integration.full_python_engine_recomputed_for_current_1min,true);
  assert.equal(result.analysis_integration.requires_on_demand_python_refresh,false);
});
test("outdated Python snapshot cannot be presented as current", async () => {
  const requested = {
    request_id:"mcp-test-2", recomputed_from_live_1min:true,
    last_confirmed_1min_start_jst:"2026-11-05 22:35:00",
    analysis:analysis("2026-11-05 22:35:00",158.41)
  };
  const result=await analyzer(requested,analysis("2026-11-05 22:12:00",158.39))();
  assert.equal(result.analysis_integration.python_snapshot_lag_in_candles,3);
  assert.equal(result.analysis_integration.full_python_engine_recomputed_for_current_1min,false);
  assert.equal(result.analysis_integration.no_current_signal_from_stale_python_decision,true);
});
test("fallback scheduled Python snapshot is clearly labeled", async () => {
  const result=await analyzer(null,analysis("2026-11-05 22:12:00",158.39))();
  assert.equal(result.analysis_integration.python_snapshot_source,"PYTHON_SCHEDULED");
  assert.equal(result.analysis_integration.requires_on_demand_python_refresh,true);
});
test("newer scheduled result supersedes older on-demand report",async()=>{
  const requested={request_id:"mcp-test-3",recomputed_from_live_1min:true,
    last_confirmed_1min_start_jst:"2026-11-05 22:12:00",
    analysis:analysis("2026-11-05 22:12:00",158.39)};
  const result=await analyzer(requested,analysis("2026-11-05 22:37:00",158.42))();
  assert.equal(result.analysis_integration.python_snapshot_source,"PYTHON_SCHEDULED");
});
