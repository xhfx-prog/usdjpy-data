import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";

const source=readFileSync("cloudflare/worker.js","utf8");
const start=source.indexOf("const MAX_RPC_BYTES =");
const end=source.indexOf("// Anonymous MCP access remains read-only",start);
assert.ok(start>=0 && end>start,"Security boundary must exist");
const make=new Function("respond",
  source.slice(start,end)+"return {validateMCPEnvelope,validateToolArgs,checkedRPC};");
const impl=make((data,status=200)=>({status,data}));
const url={pathname:"/mcp",origin:"https://usdjpy-chart.xxhappyfrenzyxx.workers.dev"};
const request=(headers={},method="POST")=>({method,headers:{get:name=>headers[name.toLowerCase()]??null}});

test("normal ChatGPT JSON RPC request is allowed",()=>{
  assert.equal(impl.validateMCPEnvelope(request({"content-type":"application/json; charset=utf-8"}),url),null);
});
test("cross-origin request is denied before tools/call",()=>{
  assert.equal(impl.validateMCPEnvelope(request({"content-type":"application/json","origin":"https://attacker.example"}),url).status,403);
});
test("large and non-JSON bodies are rejected",()=>{
  assert.equal(impl.validateMCPEnvelope(request({"content-type":"application/json","content-length":"20000"}),url).status,413);
  assert.equal(impl.validateMCPEnvelope(request({"content-type":"text/plain"}),url).status,415);
});
test("tool arguments are checked before market API calls",()=>{
  assert.equal(impl.validateToolArgs("get_usdjpy_analysis",{minute_bars:500}).minute_bars,500);
  assert.throws(()=>impl.validateToolArgs("get_usdjpy_analysis",{minute_bars:500000}),/INVALID_BAR_COUNT/);
  assert.throws(()=>impl.validateToolArgs("get_usdjpy_analysis",{unexpected:true}),/UNKNOWN_TOOL_OR_ARGUMENT/);
  assert.throws(()=>impl.validateToolArgs("get_usdjpy_history",{interval:"1h",start_date:"2026-11-06",end_date:"2026-11-05"}),/INVALID_DATE_RANGE/);
});
test("deprecated anonymous bundle endpoint has no public route",()=>{
  const exposed=source.slice(source.lastIndexOf("export default"));
  assert.ok(!exposed.includes('url.pathname === "/bundle"'));
  assert.ok(exposed.includes('url.pathname!=="/mcp"'));
});
