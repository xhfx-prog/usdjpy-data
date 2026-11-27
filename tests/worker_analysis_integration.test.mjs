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

test("release fallback excludes future and expired records and preserves live sources", () => {
  const start = code.indexOf("function releaseEvidence(");
  const end = code.indexOf("async function liveEconomicReleases", start);
  assert.ok(start >= 0 && end > start);
  const reconcile = new Function(code.slice(start, end) + "return releaseEvidence;")();
  const cutoff = Date.parse("2026-11-25T14:00:00Z");
  const available = {url:"gmo", status:"VENDOR_ACTUALS", events:[]};
  const failed = {url:"bea", status:"UNAVAILABLE", error:"HTTP 503"};
  const valid = {at_utc:"2026-11-25T13:30:00Z", official_actual:"3.3%"};
  const saved = [{url:"bea", events:[valid, {at_utc:"2026-11-25T15:00:00Z"},
    {at_utc:"2026-11-23T00:00:00Z"}]}];
  const result = reconcile([available, failed], saved, cutoff, cutoff);
  assert.equal(result.status, "PARTIAL");
  assert.deepEqual(result.sources[0], available);
  assert.equal(result.sources[1].status, "SCHEDULED_FALLBACK");
  assert.equal(result.sources[1].live_error, "HTTP 503");
  assert.deepEqual(result.sources[1].events, [valid]);
  assert.equal(reconcile([failed], saved, cutoff, cutoff).status, "SCHEDULED_FALLBACK");
  assert.equal(reconcile([failed], [], cutoff, cutoff).status, "UNAVAILABLE");
});

test("calendar reconciliation keeps forecasts separate and matches only the exact event", () => {
  const first = code.indexOf("function releaseIdentity(");
  const last = code.indexOf("function reportProjection", first);
  const join = new Function(code.slice(first, last) + "return reconcileScheduleActuals;")();
  const at = "2026-11-26T23:30:00Z";
  const schedule = {title:"Tokyo Core CPI y/y", currency:"JPY", at_utc:at,
    forecast:"3.0%", previous:"2.6%", result_status:"AWAITING_ACTUAL"};
  const release = {title:"11月東京都区部消費者物価指数(CPI、生鮮食料品除く)(前年同月比)",
    currency:"JPY", at_utc:at, vendor_actual:"2.9%", vendor_forecast:"2.9%"};
  const otherTime = {...schedule, at_utc:"2026-11-26T23:31:00Z"};
  const sources = [{url:"vendor", status:"VENDOR_ACTUALS", events:[release]}];
  const [result, untouched] = join([schedule, otherTime], sources, Date.parse(at));
  assert.equal(result.actual, "2.9%");
  assert.equal(result.result_status, "VENDOR_ACTUAL");
  assert.equal(result.forecast, "3.0%");
  assert.equal(result.alternate_forecast, "2.9%");
  assert.deepEqual(untouched, otherTime);
  assert.deepEqual(join([schedule], sources, Date.parse(at) - 1), [schedule]);
  sources[0].events.push({...release, vendor_actual:"3.1%"});
  assert.deepEqual(join([schedule], sources, Date.parse(at)), [schedule]);
});

const tfxStart = code.indexOf("function tfxAt(");
const tfxEnd = code.indexOf("async function savedMarketEvidence(", tfxStart);
const tfxEvidence = new Function(code.slice(tfxStart, tfxEnd) + "return tfxEvidence;")();

test("TFX daily availability follows observation time, with weekly data independent", () => {
  const older = {trade_date: "2026-11-25", publication_date_jst: "2026-11-26",
    first_observed_at_utc: "2026-11-26T00:00:00Z", volume: 20307, open_interest: 353335};
  const newer = {trade_date: "2026-11-26", publication_date_jst: "2026-11-27",
    first_observed_at_utc: "2026-11-26T23:20:00Z", volume: 9034, open_interest: 353770};
  const weekly = {trade_date: "2026-11-17", publication_date_jst: "2026-11-24",
    first_observed_at_utc: "2026-11-24T01:40:00Z", sell_open_interest: 358202, buy_open_interest: 133289};
  const evidence = tfxEvidence({schema_version: 1, market: "TFX Click 365", symbol: "USD/JPY",
    unit: "contracts", contract_size_usd: 10000,
    daily: {history: [older, newer], status: "SOURCE_VERIFIED", checked_at_utc: "2026-11-26T23:21:00Z"},
    weekly: {history: [weekly], status: "SCHEDULED_FALLBACK", checked_at_utc: "2026-11-26T23:21:00Z"},
  }, Date.parse("2026-11-27T00:00:00Z"));
  assert.equal(evidence.report_windows["06"].daily.latest.volume, 20307);
  assert.equal(evidence.report_windows["09"].daily.latest.volume, 9034);
  assert.equal(evidence.daily.status, "LATEST_PUBLISHED");
  assert.equal(evidence.weekly.status, "SCHEDULED_FALLBACK");
  assert.equal(evidence.weekly.latest.sell_open_interest, 358202);
  assert.equal(evidence.report_windows["06"].weekly.latest.buy_open_interest, 133289);
});
