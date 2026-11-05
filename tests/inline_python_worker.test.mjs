import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";
const text=readFileSync("cloudflare/worker.js","utf8");
const a=text.indexOf("async function analysisWithFreshPython(");
const b=text.indexOf("// Historical OHLC exclusively from GitHub.",a);
assert.ok(a>=0 && b>a,"Inline Python integration source exists");
const make=new Function("bundle","history","merge","count","Request",
 text.slice(a,b)+"return analysisWithFreshPython;");
const candle={datetime:"2026-11-05 23:47:00",open:"158.20",high:"158.30",low:"158.19",close:"158.25"};
const base={symbol:"USD/JPY",latest_confirmed_1min_bar:candle.datetime,
 fetched_at:"2026-11-05T14:49:00Z",freshness_ok:true,twelve_data_requests:1,twelve_cache_hit:false,
 series:Object.fromEntries(["1min","5min","15min","1h","4h","1day"].map(interval=>
 [interval,{values:interval==="1min"?[candle]: interval==="1h"?Array(200).fill(candle):
 interval==="1day"?Array(150).fill(candle):[],returned_bars:1}]))};
function instantiate(pyResult){
 const calls=[];
 const engine=make(async()=>base,async()=>({values:Array(1000).fill(candle)}),
  (a,b)=>a.concat(b), (v,d)=>v??d,Request);
 const env={PYTHON_ANALYZER:{fetch:async request=>{
   calls.push(JSON.parse(await request.text()));
   return Response.json(pyResult);
 }}};
 return {call:()=>engine(env,{}),calls};
}
test("one MCP call returns inline Python computation on identical confirmed minute",async()=>{
 const report={execution:"CLOUDFLARE_PYTHON_WORKER_INLINE",full_python_engine_recomputed:true,
   analysis_input_latest_confirmed_1min_start_jst:candle.datetime,
   input_bar_counts:{"1min":500,"1h":1200,"1day":150},
   analysis:{generated_at_utc:"2026-11-05T14:49:00Z",data_quality:{
     last_confirmed_1min_start_jst:candle.datetime,last_close:Number(candle.close)}}};
 const instance=instantiate(report);
 const result=await instance.call();
 assert.equal(instance.calls.length,1);
 assert.equal(instance.calls[0].source,"USDJPY_MCP_FRESH_BUNDLE");
 assert.equal(result.analysis_integration.full_python_engine_recomputed_for_current_1min,true);
 assert.equal(result.analysis_integration.python_snapshot_lag_in_candles,0);
 assert.equal(result.analysis_integration.requires_on_demand_python_refresh,false);
 assert.equal(result.python_analysis_snapshot,report.analysis);
});
test("reject Python output referencing an older minute",async()=>{
 const report={execution:"CLOUDFLARE_PYTHON_WORKER_INLINE",full_python_engine_recomputed:true,
 analysis_input_latest_confirmed_1min_start_jst:"2026-11-05 23:46:00",
 analysis:{data_quality:{last_confirmed_1min_start_jst:"2026-11-05 23:46:00",last_close:158.25}}};
 await assert.rejects(instantiate(report).call(),/VALIDATION_FAILED/);
});
test("fail closed when private Python service is unavailable",async()=>{
 const engine=make(async()=>base,async()=>({values:[]}),()=>[],(v,d)=>v??d,Request);
 await assert.rejects(engine({},{}),/SERVICE_BINDING_MISSING/);
});
