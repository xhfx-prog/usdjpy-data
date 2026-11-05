
const REPO = "xhfx-prog/usdjpy-data";
const ROOT = `https://raw.githubusercontent.com/${REPO}/main/data/`;
const VERSION = "2025-11-25";
const STALE_MS = 120000;
const MAX = 2000;
const PERIOD = {
  "1min": 1, "5min": 5, "15min": 15,
  "1h": 60, "4h": 240
};

function respond(x, status = 200) {
  return new Response(JSON.stringify(x), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store"
    }
  });
}

function count(x, fallback) {
  const n = Number(x);
  return Number.isInteger(n) && n > 0
    ? Math.min(n, MAX) : fallback;
}

function timestamp(s) {
  return Date.parse(s.replace(" ", "T") + "+09:00");
}

function label(t) {
  return new Date(t + 32400000)
    .toISOString().slice(0, 19).replace("T", " ");
}

function valid(b) {
  if (!b || !b.datetime) return false;
  const [o, h, l, c] = ["open", "high", "low", "close"]
    .map(k => Number(b[k]));
  return [o,h,l,c].every(Number.isFinite) &&
    l > 0 && l <= o && o <= h && l <= c && c <= h;
}

function completed(rows, minutes) {
  return rows.filter(b =>
    valid(b) &&
    Number.isFinite(timestamp(b.datetime)) &&
    timestamp(b.datetime) + minutes * 60000 <= Date.now()
  );
}

function merge(preferred, old) {
  const m = new Map();
  for (const b of old) if (valid(b)) m.set(b.datetime, b);
  for (const b of preferred) if (valid(b)) m.set(b.datetime, b);
  return [...m.values()].sort((a,b) =>
    b.datetime.localeCompare(a.datetime));
}

// v4.1: Fix GitHub archive paths.
// Index paths already include "data/".
async function github(path) {
  const normalized = path.replace(/^data\//, "");
  const res = await fetch(ROOT + normalized, {
    cf: { cacheTtl: 0 }
  });
  if (!res.ok) {
    throw Error(
      "GitHub HTTP " + res.status + ": " + normalized
    );
  }
  return res.json();
}

async function saved(path, minutes) {
  const j = await github(path);
  if (!Array.isArray(j.data?.values)) {
    throw Error("Invalid GitHub data: " + path);
  }
  return minutes
    ? completed(j.data.values, minutes)
    : j.data.values.filter(valid);
}

// Only 1-minute candles are requested from Twelve Data.
async function live(env, size) {
  if (!env.TWELVE_API_KEY) {
    throw Error("TWELVE_API_KEY missing");
  }

  // Reuse the same cache entry for ordinary 1min, 5min and 15min analysis requests.
  const n = size <= 500 ? 500 : size <= 1500 ? 1500 : 2000;
  const key = new Request(
    `https://usdjpy-cache.invalid/1min?size=${n}`
  );

  const cache = typeof caches !== "undefined"
    ? caches.default : null;

  if (cache) {
    const hit = await cache.match(key);
    if (hit) {
      const j = await hit.json();
      return {
        values: completed(j.values, 1),
        calls: 0,
        cacheHit: true
      };
    }
  }

  const u = new URL("https://api.twelvedata.com/time_series");
  u.searchParams.set("symbol", "USD/JPY");
  u.searchParams.set("interval", "1min");
  u.searchParams.set("timezone", "Asia/Tokyo");
  u.searchParams.set("outputsize", String(n));
  u.searchParams.set("apikey", env.TWELVE_API_KEY);

  const res = await fetch(u.toString());
  if (!res.ok) {
    throw Error("Twelve Data HTTP " + res.status);
  }

  const data = await res.json();
  if (data.status === "error" || !Array.isArray(data.values)) {
    throw Error(data.message || "Twelve Data error");
  }

  if (cache) {
    try {
      await cache.put(key, new Response(
        JSON.stringify({ values: data.values }), {
          headers: {
            "Content-Type": "application/json",
            "Cache-Control": "public, max-age=45"
          }
        }
      ));
    } catch (_) {}
  }

  return {
    values: completed(data.values, 1),
    calls: 1,
    cacheHit: false
  };
}

// Aggregate only when every constituent candle exists.
function aggregate(rows, minutes, unit = 1) {
  const span = minutes * 60000;
  const step = unit * 60000;
  const groups = new Map();

  for (const b of rows) {
    const t = timestamp(b.datetime);
    if (!Number.isFinite(t)) continue;

    const start = Math.floor(
      (t + 32400000) / span
    ) * span - 32400000;

    if (start + span > Date.now()) continue;
    if (!groups.has(start)) groups.set(start, []);
    groups.get(start).push({ t, b });
  }

  const out = [];

  for (const [start, items] of groups) {
    items.sort((a,b) => a.t - b.t);
    const required = minutes / unit;

    if (items.length !== required) continue;
    if (items.some((x,i) => x.t !== start + i * step)) {
      continue;
    }

    out.push({
      datetime: label(start),
      open: items[0].b.open,
      high: String(Math.max(
        ...items.map(x => Number(x.b.high))
      )),
      low: String(Math.min(
        ...items.map(x => Number(x.b.low))
      )),
      close: items[items.length - 1].b.close
    });
  }

  return out.sort((a,b) =>
    b.datetime.localeCompare(a.datetime));
}

async function minuteData(env, needed = 180) {
  let old = [];
  let githubError = null;

  try {
    old = await saved("usdjpy_1min.json", 1);
  } catch (e) {
    githubError = String(e.message);
  }

  const last = old[0];
  const age = last
    ? Date.now() - timestamp(last.datetime) - 60000
    : Infinity;

  let fresh = [];
  let calls = 0;
  let cacheHit = false;
  let liveError = null;

  if (age >= STALE_MS || old.length < Math.min(needed, 1500)) {
    try {
      const n = Math.min(2000, Math.max(
        180,
        Math.ceil(age / 60000) + 10 || 180,
        needed
      ));
      const r = await live(
        env, Number.isFinite(n) ? n : 2000
      );
      fresh = r.values;
      calls = r.calls;
      cacheHit = r.cacheHit;
    } catch (e) {
      liveError = String(e.message);
    }
  }

  const rows = completed(merge(fresh, old), 1);
  const latest = rows[0]?.datetime || null;
  const actualAge = latest
    ? (Date.now() - timestamp(latest) - 60000) / 60000
    : null;

  return {
    rows,
    latest,
    ageMinutes: actualAge,
    freshnessOK: actualAge !== null &&
      actualAge < 2,
    calls,
    cacheHit,
    githubError,
    liveError
  };
}

function pack(interval, rows, size, source) {
  const values = rows.slice(0, size);
  return {
    interval,
    source,
    latest_confirmed_bar: values[0]?.datetime || null,
    returned_bars: values.length,
    values
  };
}


// The current-price analysis tools must not silently return stale minute candles.
// Historical queries remain available separately through get_usdjpy_history.
function requireFreshMinuteData(m) {
  if (m.freshnessOK && m.latest) return;
  const age = m.ageMinutes == null ? "unknown" : m.ageMinutes.toFixed(2);
  throw Error(
    "STALE_1MIN_DATA: latest_confirmed_1min_bar=" + (m.latest || "none") +
    "; minutes_since_close=" + age +
    "; GitHub=" + (m.githubError || "ok") +
    "; TwelveData=" + (m.liveError || "not fresh") +
    ". Do not present stale USDJPY prices as current; report data unavailable."
  );
}

// One request returns all six timeframes.
async function bundle(env, args = {}) {
  const sizes = {
    "1min": count(args.minute_bars, 180),
    "5min": count(args.five_minute_bars, 24),
    "15min": count(args.fifteen_minute_bars, 16),
    "1h": count(args.hourly_bars, 72),
    "4h": count(args.four_hour_bars, 24),
    "1day": count(args.daily_bars, 30)
  };

  const needed = Math.min(2000, Math.max(
    sizes["1min"],
    sizes["5min"] * 5 + 5,
    sizes["15min"] * 15 + 15,
    120
  ));

  const [m, h, d] = await Promise.all([
    minuteData(env, needed),
    saved("usdjpy_1h.json", 60),
    saved("usdjpy_1day.json", null)
  ]);
  requireFreshMinuteData(m);

  // Fill delayed GitHub hourly data using only complete
  // 60-minute blocks from the recent minute data.
  const recentHours = aggregate(m.rows, 60, 1);
  const hours = merge(recentHours, h);

  const series = {
    "1min": pack(
      "1min", m.rows, sizes["1min"], "1min"
    ),
    "5min": pack(
      "5min",
      aggregate(m.rows, 5),
      sizes["5min"],
      "derived 1min"
    ),
    "15min": pack(
      "15min",
      aggregate(m.rows, 15),
      sizes["15min"],
      "derived 1min"
    ),
    "1h": pack(
      "1h",
      hours,
      sizes["1h"],
      "GitHub + complete recent 1min"
    ),
    "4h": pack(
      "4h",
      aggregate(hours, 240, 60),
      sizes["4h"],
      "derived 1h"
    ),
    "1day": pack(
      "1day",
      d,
      sizes["1day"],
      "GitHub archive"
    )
  };

  return {
    symbol: "USD/JPY",
    fetched_at: new Date().toISOString(),
    latest_confirmed_1min_bar: m.latest,
    minutes_since_1min_close: m.ageMinutes,
    freshness_ok: m.freshnessOK,
    twelve_data_requests: m.calls,
    twelve_cache_hit: m.cacheHit,
    github_error: m.githubError,
    twelve_data_error: m.liveError,
    archive_write_performed: false,
    series
  };
}

// A single MCP call combines freshly acquired 1min bars with the exact
// GitHub Python-analyzer snapshot. The snapshot is NEVER relabeled as a
// computation using the live bars; its valid-as-of time is always explicit.
// A new Python evaluation requires rerunning analyze_usdjpy.py in GitHub.
// One current-data request returns live OHLC and the newest valid *Python*
// analysis (hourly or request-bound on-demand). Never treat an older Python
// snapshot as recalculated using later 1min candles.
async function analysisWithLiveBars(env, args = {}) {
  const [live, snapshots] = await Promise.all([
    bundle(env, {
      minute_bars: count(args.minute_bars, 500),
      five_minute_bars: count(args.five_minute_bars, 60),
      fifteen_minute_bars: count(args.fifteen_minute_bars, 24),
      hourly_bars: count(args.hourly_bars, 200),
      four_hour_bars: count(args.four_hour_bars, 48),
      daily_bars: count(args.daily_bars, 150)
    }),
    Promise.allSettled([
      github("analysis_live_latest.json"),
      github("analysis_latest.json")
    ])
  ]);
  const candidates = [];
  const liveSnapshot = snapshots[0].status === "fulfilled" ? snapshots[0].value : null;
  if (liveSnapshot?.recomputed_from_live_1min === true &&
      liveSnapshot.analysis?.instrument === "USD/JPY" &&
      liveSnapshot.analysis?.data_quality?.last_confirmed_1min_start_jst ===
        liveSnapshot.last_confirmed_1min_start_jst) {
    candidates.push({ report: liveSnapshot.analysis,
      source: "PYTHON_ON_DEMAND",
      request_id: liveSnapshot.request_id,
      input_live_fetched_at: liveSnapshot.input_live_fetched_at });
  }
  const scheduled = snapshots[1].status === "fulfilled" ? snapshots[1].value : null;
  if (scheduled?.instrument === "USD/JPY" &&
      scheduled.data_quality?.last_confirmed_1min_start_jst) {
    candidates.push({ report: scheduled, source: "PYTHON_SCHEDULED",
      request_id: null, input_live_fetched_at: null });
  }
  if (!candidates.length) throw Error("NO_VALID_PYTHON_ANALYSIS_SNAPSHOT");
  candidates.sort((a,b) =>
    b.report.data_quality.last_confirmed_1min_start_jst.localeCompare(
      a.report.data_quality.last_confirmed_1min_start_jst));
  const selected = candidates[0];
  const engine = selected.report;
  if (!engine.market_structure?.timeframe_assessments)
    throw Error("INVALID_PYTHON_ANALYSIS_SNAPSHOT");

  const current = live.series["1min"].values[0];
  if (!current) throw Error("NO_LIVE_1MIN_DATA");
  const engineBar = engine.data_quality.last_confirmed_1min_start_jst;
  const gap = Math.round(
    (timestamp(current.datetime) - timestamp(engineBar)) / 60000
  );
  const snapshotCurrent = gap === 0 &&
    Number(current.close) === Number(engine.data_quality.last_close);
  const recent = live.series["1min"].values.slice(0, 180);
  const liveHigh = Math.max(...recent.map(x => Number(x.high)));
  const liveLow = Math.min(...recent.map(x => Number(x.low)));
  const oldClose = Number(engine.data_quality.last_close);
  const newClose = Number(current.close);
  const liveOverlay = {
    last_confirmed_1min_start_jst: current.datetime,
    latest_open: Number(current.open),
    latest_high: Number(current.high),
    latest_low: Number(current.low),
    latest_close: newClose,
    change_from_python_snapshot_pips:
      Number(((newClose - oldClose) * 100).toFixed(3)),
    python_snapshot_close: oldClose,
    observed_recent_minutes: recent.length,
    observed_from_jst: recent[recent.length - 1]?.datetime || null,
    recent_window_high: Number.isFinite(liveHigh) ? liveHigh : null,
    recent_window_low: Number.isFinite(liveLow) ? liveLow : null,
    recent_window_mid: Number.isFinite(liveHigh + liveLow)
      ? Number(((liveHigh + liveLow) / 2).toFixed(5)) : null
  };
  return {
    symbol: "USD/JPY",
    fetched_at: live.fetched_at,
    latest_confirmed_1min_bar: current.datetime,
    freshness_ok: live.freshness_ok,
    twelve_data_requests: live.twelve_data_requests,
    twelve_cache_hit: live.twelve_cache_hit,
    analysis_integration: {
      live_bars_acquired_before_return: true,
      python_engine: "analyze_usdjpy.py (GitHub Actions)",
      python_snapshot_source: selected.source,
      python_snapshot_request_id: selected.request_id,
      python_snapshot_live_input_fetched_at: selected.input_live_fetched_at,
      python_snapshot_generated_at_utc: engine.generated_at_utc,
      python_snapshot_analysis_asof_jst: engine.analysis_asof_jst,
      python_snapshot_last_1min_start_jst: engineBar,
      python_snapshot_lag_in_candles: gap,
      python_snapshot_matches_current_1min: snapshotCurrent,
      python_engine_was_run_on_live_request: selected.source === "PYTHON_ON_DEMAND",
      full_python_engine_recomputed_for_current_1min: snapshotCurrent,
      requires_on_demand_python_refresh: !snapshotCurrent,
      interpretation_policy: snapshotCurrent
        ? "PYTHON_ANALYSIS_MATCHES_CURRENT_LIVE_CANDLE"
        : "PYTHON_ANALYSIS_IS_HISTORICAL_ONLY_RECOMPUTE_BEFORE_USING_CURRENT_SIGNALS",
      no_current_signal_from_stale_python_decision: !snapshotCurrent
    },
    live_overlay: liveOverlay,
    series: live.series,
    python_analysis_snapshot: engine
  };
}

// Historical OHLC exclusively from GitHub.
// No Twelve Data API calls.
async function history(args) {
  const interval = args.interval || "1h";
  if (!["1h", "4h", "1day"].includes(interval)) {
    throw Error("Unsupported history interval");
  }

  const native = interval === "4h" ? "1h" : interval;
  const size = count(args.size, 200);
  const index = await github(
    `history_${native}_index.json`
  );
  const partitions = index.partitions || [];

  let chosen = [];
  let estimated = 0;
  const required = interval === "4h"
    ? size * 4 + 4 : size;

  for (const p of partitions) {
    if (args.start_date &&
        p.last < args.start_date) continue;

    if (args.end_date &&
        p.first > args.end_date) continue;

    chosen.push(p);
    estimated += p.count || 0;

    if (!args.start_date && !args.end_date &&
        estimated >= required) break;
  }

  if (chosen.length > 24) {
    throw Error("Too many history partitions");
  }

  const batches = await Promise.all(
    chosen.map(p =>
      saved(p.path, native === "1h" ? 60 : null)
    )
  );

  let rows = merge([], batches.flat());

  if (args.start_date) {
    rows = rows.filter(x =>
      x.datetime.slice(0,10) >= args.start_date
    );
  }

  if (args.end_date) {
    rows = rows.filter(x =>
      x.datetime.slice(0,10) <= args.end_date
    );
  }

  if (interval === "4h") {
    rows = aggregate(rows, 240, 60);
  }

  return {
    interval,
    source: "GitHub historical archive",
    available_oldest: index.actual_oldest,
    available_newest: index.actual_newest,
    total_saved_bars: index.total_bars,
    paths: chosen.map(p => p.path),
    twelve_data_requests: 0,
    values: rows.slice(0, size)
  };
}

const definitions = [
  {
    name: "get_usdjpy_analysis",
    description:
      "MANDATORY FIRST tool for any user request analyzing CURRENT USD/JPY. " +
      "Fetch the newest confirmed 1-minute OHLC with conditional one-call Twelve Data access, " +
      "derive all six timeframes, and attach the deterministic Python analysis_latest.json " +
      "with explicit as-of and lag checks. If snapshot is stale, submit confirmed " +
      "1min bars and fetched_at to control/live_analysis_request.json through GitHub " +
      "and verify data/analysis_live_latest.json, then check freshness again. DO NOT present " +
      "its trend/SR/decision as recalculated or current. Includes observed live OHLC overlay. " +
      "Never answer current USDJPY analysis from the Python snapshot alone.",
    inputSchema: {
      type: "object",
      properties: {
        minute_bars: { type:"integer",minimum:1,maximum:2000 },
        five_minute_bars: { type:"integer",minimum:1,maximum:2000 },
        fifteen_minute_bars: { type:"integer",minimum:1,maximum:2000 },
        hourly_bars: { type:"integer",minimum:1,maximum:2000 },
        four_hour_bars: { type:"integer",minimum:1,maximum:2000 },
        daily_bars: { type:"integer",minimum:1,maximum:2000 }
      },
      additionalProperties: false
    },
    annotations: { readOnlyHint:true, destructiveHint:false, openWorldHint:true }
  },
  {
    name: "get_usdjpy_bundle",
    description:
      "For OHLC-only use; prefer get_usdjpy_analysis for current USDJPY market analysis. " +
      "Return 1min, 5min, 15min, 1h, 4h and daily OHLC together, " +
      "with a conditional single 1min Twelve Data fetch. " +
      "Rejects stale 1min candles (> 2 min after last close). " +
      "Always check fetched_at, latest_confirmed_1min_bar and freshness_ok.",
    inputSchema: {
      type: "object",
      properties: {
        minute_bars: {
          type:"integer",minimum:1,maximum:2000
        },
        five_minute_bars: {
          type:"integer",minimum:1,maximum:2000
        },
        fifteen_minute_bars: {
          type:"integer",minimum:1,maximum:2000
        },
        hourly_bars: {
          type:"integer",minimum:1,maximum:2000
        },
        four_hour_bars: {
          type:"integer",minimum:1,maximum:2000
        },
        daily_bars: {
          type:"integer",minimum:1,maximum:2000
        }
      },
      additionalProperties: false
    },
    annotations: {
      readOnlyHint:true,
      destructiveHint:false
    }
  },
  {
    name: "get_latest_usdjpy",
    description:
      "Backward-compatible OHLC tool. Prefer get_usdjpy_bundle " +
      "for current analysis to minimize API requests.",
    inputSchema: {
      type: "object",
      properties: {
        interval: {
          type:"string",
          enum:["1min","5min","15min","1h","4h","1day"]
        },
        size: {
          type:"integer",minimum:1,maximum:2000
        }
      },
      additionalProperties:false
    },
    annotations: { readOnlyHint:true }
  },
  {
    name: "get_usdjpy_history",
    description:
      "Read long-term hourly, derived 4h and daily OHLC " +
      "from GitHub without Twelve Data API calls.",
    inputSchema: {
      type:"object",
      properties: {
        interval: {
          type:"string",enum:["1h","4h","1day"]
        },
        size: {
          type:"integer",minimum:1,maximum:2000
        },
        start_date: {type:"string"},
        end_date: {type:"string"}
      },
      required:["interval"],
      additionalProperties:false
    },
    annotations:{readOnlyHint:true}
  }
];

async function handleMCP(request, env) {
  if (request.method !== "POST") {
    return new Response(null, {status:405});
  }

  let rpc;

  try {
    rpc = await request.json();
  } catch {
    return respond({
      jsonrpc:"2.0",
      id:null,
      error:{code:-32700,message:"Parse error"}
    },400);
  }

  const id = rpc.id ?? null;

  if (rpc.id === undefined) {
    return new Response(null,{status:202});
  }

  let result;

  try {
    switch(rpc.method) {
      case "initialize":
        result = {
          protocolVersion: VERSION,
          capabilities:{
            tools:{listChanged:false}
          },
          serverInfo:{
            name:"usdjpy-market-data",
            version:"4.1.0"
          }
        };
        break;

      case "ping":
        result = {};
        break;

      case "tools/list":
        result = {tools:definitions};
        break;

      case "tools/call": {
        const name = rpc.params?.name;
        const args = rpc.params?.arguments || {};
        let data;

        if (name === "get_usdjpy_analysis") {
          data = await analysisWithLiveBars(env,args);
        } else if (name === "get_usdjpy_bundle") {
          data = await bundle(env,args);
        } else if (name === "get_usdjpy_history") {
          data = await history(args);
        } else if (name === "get_latest_usdjpy") {
          const interval = args.interval || "1min";
          const size = count(args.size,100);

          if (["1h","4h","1day"].includes(interval)) {
            data = await history({interval,size});
          } else {
            const m = await minuteData(
              env,
              Math.min(
                2000,
                size*PERIOD[interval]+PERIOD[interval]
              )
            );
            requireFreshMinuteData(m);

            const values = interval === "1min"
              ? m.rows.slice(0,size)
              : aggregate(
                  m.rows,PERIOD[interval]
                ).slice(0,size);

            data = {
              interval,
              values,
              latest_confirmed_bar:
                values[0]?.datetime||null,
              freshness_ok:m.freshnessOK,
              twelve_data_requests:m.calls,
              twelve_cache_hit:m.cacheHit
            };
          }
        } else {
          throw Error("Unknown tool");
        }

        result = {
          content:[
            {type:"text",text:JSON.stringify(data)}
          ],
          structuredContent:data,
          isError:false
        };
        break;
      }

      default:
        return respond({
          jsonrpc:"2.0",
          id,
          error:{
            code:-32601,
            message:"Method not found"
          }
        });
    }

    return respond({
      jsonrpc:"2.0",id,result
    });
  } catch(e) {
    return respond({
      jsonrpc:"2.0",
      id,
      result:{
        content:[
          {type:"text",text:String(e.message)}
        ],
        isError:true
      }
    });
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/mcp") {
      return handleMCP(request,env);
    }

    if (url.pathname === "/bundle") {
      try {
        return respond(await bundle(env));
      } catch(e) {
        return respond({
          error:String(e.message)
        },502);
      }
    }

    return respond({
      service:"USDJPY Market Data",
      version:"4.1.0",
      mcp:"/mcp",
      bundle:"/bundle"
    });
  }
};
