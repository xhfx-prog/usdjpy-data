
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
  const currentSnapshot = /^(macro_|fx_intraday[.]json$|fx_flow_sources[.]json$|mof_securities_weekly[.]json$)/.test(normalized);
  const url = ROOT + normalized + (currentSnapshot ? "?minute=" + Math.floor(Date.now() / 60000) : "");
  const res = await fetch(url, {
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
      const cached = completed(j.values, 1);
      const latest = cached[0];
      // Do not return already-stale cached OHLC merely because cache TTL has not expired.
      if (latest && Date.now() - timestamp(latest.datetime) - 60000 < STALE_MS) {
        return {
          values: cached,
          calls: 0,
          cacheHit: true
        };
      }
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
  // A GitHub Actions Python run takes time; 1-3 subsequent minutes are normal.
  // Permit recent structure as *context* only when the market has barely moved.
  // This is NOT a claim that Python evaluated the current 1-minute bar.
  const deltaPips = (Number(current.close) -
    Number(engine.data_quality.last_close)) * 100;
  const runAgeMinutes = (Date.now() -
    Date.parse(engine.generated_at_utc)) / 60000;
  const recentPythonContextUsable = (
    snapshotCurrent || (
      Number.isFinite(runAgeMinutes) &&
      runAgeMinutes >= 0 && runAgeMinutes <= 5 &&
      gap >= 0 && gap <= 3 &&
      Math.abs(deltaPips) <= 5
    )
  );
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
      python_analysis_recent_context_usable: recentPythonContextUsable,
      python_snapshot_age_minutes: Math.round(runAgeMinutes * 100) / 100,
      live_close_change_since_python_pips: Math.round(deltaPips * 1000) / 1000,
      requires_on_demand_python_refresh: !recentPythonContextUsable,
      interpretation_policy: snapshotCurrent
        ? "PYTHON_ANALYSIS_MATCHES_CURRENT_LIVE_CANDLE"
        : recentPythonContextUsable
          ? "PYTHON_ANALYSIS_VALID_RECENT_CONTEXT_WITH_LIVE_1MIN_PRICE_OVERLAY_NOT_RECOMPUTED_AT_CURRENT_BAR"
          : "PYTHON_ANALYSIS_IS_HISTORICAL_ONLY_RECOMPUTE_BEFORE_USING_CURRENT_SIGNALS",
      no_current_signal_from_stale_python_decision: !recentPythonContextUsable
    },
    live_overlay: liveOverlay,
    series: live.series,
    python_analysis_snapshot: engine
  };
}

// Load retained 1-minute session history without any Twelve Data requests.
// This extends the deterministic Python engine's 5min/15min support and
// resistance computation across >=72h, instead of relying on only 1200 minutes.
async function recentMinuteArchive() {
  const index = await github("history_1min_index.json");
  const sessions = index.sessions;
  if (!Array.isArray(sessions) || !sessions.length) {
    throw Error("GITHUB_MINUTE_HISTORY_INDEX_EMPTY");
  }
  const selected = sessions.slice(0, 6);
  const parts = await Promise.all(selected.map(async session => {
    if (typeof session.path !== "string" ||
        !session.path.startsWith("data/history_1min/") ||
        !/^[0-9]{4}-[0-9]{2}-[0-9]{2}[.]json$/.test(session.path.slice(18))) {
      throw Error("UNSAFE_MINUTE_ARCHIVE_PATH");
    }
    return saved(session.path, 1);
  }));
  return merge([], parts.flat()).slice(0, 6500);
}

function confirmedDaily(rows, now = Date.now()) {
  const today = label(now).slice(0, 10);
  return rows.filter(row => {
    const day = row.datetime;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || day >= today) return false;
    const weekday = new Date(day + "T00:00:00Z").getUTCDay();
    return weekday > 0 && weekday < 6;
  });
}

// Real-time full canonical Python evaluation, synchronous inside ONE MCP call.
// The private Python Worker is only reachable over a Cloudflare Service Binding.
// On any error, fail closed; never substitute a stale GitHub Python snapshot.
async function analysisWithFreshPython(env, args = {}) {
  if (!env.PYTHON_ANALYZER?.fetch) {
    throw Error("PYTHON_ANALYZER_SERVICE_BINDING_MISSING");
  }
  const [live, archiveHours, archiveMinutes, archiveDays] = await Promise.all([
    bundle(env, {
      minute_bars: Math.max(1200, count(args.minute_bars, 1)),
      five_minute_bars: Math.max(60, count(args.five_minute_bars, 60)),
      fifteen_minute_bars: Math.max(24, count(args.fifteen_minute_bars, 24)),
      hourly_bars: Math.max(200, count(args.hourly_bars, 200)),
      four_hour_bars: Math.max(48, count(args.four_hour_bars, 48)),
      daily_bars: Math.max(200, count(args.daily_bars, 1))
    }),
    history({interval: "1h", size: 1200}),
    recentMinuteArchive(),
    history({interval: "1day", size: Math.min(MAX, Math.max(220, count(args.daily_bars, 1)))})
      .catch(error => ({values: [], error: String(error.message).slice(0, 160)}))
  ]);
  if (!live.freshness_ok) throw Error("STALE_CURRENT_1MIN");
  const current = live.series["1min"].values[0];
  if (!current || current.datetime !== live.latest_confirmed_1min_bar) {
    throw Error("LIVE_1MIN_MISMATCH");
  }
  const hours = merge(live.series["1h"].values, archiveHours.values).slice(0, MAX);
  const days = confirmedDaily(merge(live.series["1day"].values, archiveDays.values)).slice(0, MAX);
  const fullMinutes = merge(live.series["1min"].values, archiveMinutes)
    .slice(0, 6500);
  if (!fullMinutes.length || fullMinutes[0].datetime !== current.datetime) {
    throw Error("ARCHIVE_MINUTE_MERGE_MISMATCH");
  }
  const input = {
    symbol: "USD/JPY",
    source: "USDJPY_MCP_FRESH_BUNDLE",
    fetched_at: live.fetched_at,
    latest_confirmed_1min_bar: current.datetime,
    "1min": fullMinutes,
    "1h": hours,
    "1day": days
  };
  const request = new Request("https://usdjpy-python-engine.internal/analyze", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(input)
  });
  const response = await env.PYTHON_ANALYZER.fetch(request);
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 900);
    throw Error("PYTHON_INLINE_ANALYSIS_FAILED_" + response.status + ": " + detail);
  }
  const python = await response.json();
  if (python.execution !== "CLOUDFLARE_PYTHON_WORKER_INLINE" ||
      python.full_python_engine_recomputed !== true ||
      python.analysis_input_latest_confirmed_1min_start_jst !== current.datetime ||
      python.analysis?.data_quality?.last_confirmed_1min_start_jst !== current.datetime ||
      Number(python.analysis.data_quality.last_close) !== Number(current.close)) {
    throw Error("PYTHON_INLINE_RESULT_VALIDATION_FAILED");
  }
  const historyNotes = {
    daily_archive_error: archiveDays.error ?? null,
    daily_input_oldest: days.at(-1)?.datetime ?? null,
    daily_input_latest: days[0]?.datetime ?? null,
    daily_saved_bars: archiveDays.total_saved_bars ?? null,
    daily_saved_oldest: archiveDays.available_oldest ?? null,
    daily_saved_latest: archiveDays.available_newest ?? null
  };
  const bars = {
    "1min": ["minute_bars", fullMinutes],
    "5min": ["five_minute_bars", aggregate(fullMinutes, 5)],
    "15min": ["fifteen_minute_bars", aggregate(fullMinutes, 15)],
    "1h": ["hourly_bars", hours],
    "4h": ["four_hour_bars", aggregate(hours, 240, 60)],
    "1day": ["daily_bars", days]
  };
  const series = Object.fromEntries(Object.entries(bars).map(([key, [arg, rows]]) =>
    [key, pack(key, rows, count(args[arg], args.detail === "legacy" ? live.series[key].returned_bars : 1),
      live.series[key].source)]));
  return {
    symbol: "USD/JPY",
    fetched_at: live.fetched_at,
    latest_confirmed_1min_bar: current.datetime,
    freshness_ok: true,
    twelve_data_requests: live.twelve_data_requests,
    twelve_cache_hit: live.twelve_cache_hit,
    archive_write_performed: false,
    analysis_integration: {
      python_engine: "analyze_usdjpy.py (Cloudflare Python Worker)",
      python_snapshot_source: "PYTHON_WORKER_INLINE",
      full_python_engine_recomputed_for_current_1min: true,
      python_engine_was_run_on_live_request: true,
      python_snapshot_matches_current_1min: true,
      python_snapshot_lag_in_candles: 0,
      requires_on_demand_python_refresh: false,
      no_current_signal_from_stale_python_decision: false,
      analysis_input_latest_confirmed_1min_start_jst: current.datetime,
      python_computed_at_utc: python.analysis.generated_at_utc,
      python_input_bar_counts: python.input_bar_counts,
      history: historyNotes,
      single_mcp_call_completed: true
    },
    live_overlay: {
      last_confirmed_1min_start_jst: current.datetime,
      latest_open: Number(current.open),
      latest_high: Number(current.high),
      latest_low: Number(current.low),
      latest_close: Number(current.close)
    },
    series,
    analysis: python.analysis,
    report_minutes: fullMinutes
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

    const countIsEnough = native === "1day" ? estimated >= Math.ceil(required * 7 / 5) + 7 : estimated >= required;
    if (!args.start_date && !args.end_date && countIsEnough) break;
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
  if (native === "1day") rows = confirmedDaily(rows);

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
      "MANDATORY FIRST tool for current USD/JPY market analysis. In ONE synchronous MCP " +
      "call fetch current confirmed Twelve Data 1-minute OHLC, complete six timeframes, " +
      "retrieve GitHub historical 1h/1day data, and RUN canonical analyze_usdjpy.py " +
      "inside private Cloudflare Python Worker on the very same minute candle. " +
      "Returns a compact report with market structure, S/R, freshness, audit and macro data. " +
      "Each requested OHLC count controls only returned bars (default one per timeframe). " +
      "Use detail=legacy for the old full structured shape. GitHub Actions stores history; " +
      "the live request never starts a workflow or reuses a stale Python analysis.",
    inputSchema: {
      type: "object",
      properties: {
        detail: { type:"string",enum:["report","legacy"],default:"report" },
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

// Report-first text projection; retain full raw data in structuredContent.
const RELEASE_SOURCES = [
  { name: "GMO外貨", url: "https://www.gaikaex.com/gaikaex/mark/calendar/", format: "gmo" },
  { name: "米商務省BEA", url: "https://www.bea.gov/news/current-releases", format: "bea" }
];

function calendarDate(month, day, printedAt, offsetMinutes) {
  const local = printedAt + offsetMinutes * 60000;
  const year = new Date(local).getUTCFullYear();
  const dates = [year - 1, year, year + 1].map(y => Date.UTC(y, month - 1, day))
    .filter(t => new Date(t).getUTCMonth() === month - 1);
  return dates.sort((a, b) => Math.abs(a - local) - Math.abs(b - local))[0];
}

function vendorNumber(value) {
  return /^[+\-−]?[\d,.]+(?:\s*[%KMBT]|[兆億万千\d,.]*[件人戸円ドルポンドユーロ]+)?$/iu.test(value)
    && /\d/.test(value) ? value : null;
}

async function parseGmoReleases(response, observedAt, through) {
  const rows = [];
  let row, cell;
  await new HTMLRewriter()
    .on("table tr", {
      element(element) {
        row = [];
        rows.push(row);
        element.onEndTag(() => { row = null; });
      }
    })
    .on("table tr > td", {
      element(element) {
        if (!row) return;
        cell = { isDate: (element.getAttribute("class") || "").split(/\s+/).includes("date"), text: "" };
        row.push(cell);
        element.onEndTag(() => { cell = null; });
      },
      text(chunk) { if (cell) cell.text += chunk.text; }
    }).transform(response).arrayBuffer();

  const events = [];
  let date;
  let parsed = 0;
  for (const cells of rows) {
    const values = cells.map(x => x.text.replace(/&nbsp;|&#160;/gi, " ").replace(/\s+/g, " ").trim());
    if (cells[0]?.isDate) {
      const parts = values.shift().match(/^(\d{1,2})\/(\d{1,2})/);
      date = parts ? calendarDate(Number(parts[1]), Number(parts[2]), observedAt, 540) : undefined;
    }
    if (values.length !== 7) continue;
    const time = values[0].match(/^(\d{1,2}):([0-5]\d)$/);
    const at = time ? date + (Number(time[1]) * 60 + Number(time[2]) - 540) * 60000 : NaN;
    const currency = ({ "米国": "USD", "日本": "JPY" })[values[1]];
    if (!currency || !values[2] || !Number.isFinite(at) || Math.abs(at - observedAt) > 8 * 86400000) continue;
    parsed++;
    const actual = vendorNumber(values[5]);
    if (!actual || at > Math.min(through, observedAt) || at < through - 36 * 3600000) continue;
    events.push({
      currency, title: values[2], at_utc: new Date(at).toISOString(),
      vendor_actual: actual, vendor_forecast: vendorNumber(values[4]), vendor_previous: values[6] || null
    });
  }
  if (!parsed) throw Error("No current USD/JPY calendar rows parsed");
  return events.sort((a, b) => b.at_utc.localeCompare(a.at_utc)).slice(0, 25);
}

async function releasePage(url) {
  const response = await fetch(url, { cf: { cacheTtl: 0 }, signal: AbortSignal.timeout(4500) });
  if (!response.ok) throw Error("HTTP " + response.status);
  return response;
}

async function beaReleases(index, through) {
  const links = new Set();
  await new HTMLRewriter().on("a[href]", {
    element(element) {
      const href = element.getAttribute("href");
      if (/^\/news\/20\d{2}\/(?:personal-income-and-outlays-|(?:gdp|gross-domestic-product)-)[a-z0-9-]+$/.test(href)) {
        links.add(new URL(href, "https://www.bea.gov").href);
      }
    }
  }).transform(index).arrayBuffer();
  if (!links.size) throw Error("No BEA release links parsed");
  const settled = await Promise.allSettled([...links].slice(0, 4).map(async url => {
    const response = await releasePage(url);
    let title = "";
    let body = "";
    let releaseDate = "";
    await new HTMLRewriter()
      .on("#home h1", { text(chunk) { title += chunk.text; } })
      .on(".field--name-field-release-date", { text(chunk) { releaseDate += chunk.text; } })
      .on(".release-body", { text(chunk) { body += chunk.text; } })
      .transform(response).arrayBuffer();
    body = body.replace(/&nbsp;|&#160;/gi, " ").replace(/\s+/g, " ");
    const stamp = releaseDate.replace(/&nbsp;|&#160;/gi, " ").replace(/\s+/g, " ").match(/EMBARGOED UNTIL RELEASE AT (\d{1,2}):(\d{2}) ([ap])\.?m\.? (EST|EDT),? \w+, ([A-Za-z]+ \d{1,2}, 20\d{2})/i);
    if (!stamp) throw Error("BEA release timestamp not found");
    const hour = Number(stamp[1]) % 12 + (stamp[3].toLowerCase() === "p" ? 12 : 0);
    const offset = stamp[4].toUpperCase() === "EST" ? 5 : 4;
    const at = Date.parse(stamp[5] + " 00:00:00 UTC") + ((hour + offset) * 60 + Number(stamp[2])) * 60000;
    if (!Number.isFinite(at)) throw Error("Invalid BEA timestamp");
    if (at > Math.min(through, Date.now()) || at < through - 36 * 3600000) return [];
    const metrics = url.includes("/personal-income-and-outlays-") ? [
      ["PCE価格指数 前月比", /From the preceding month, the PCE price index[^.]*? (increased|decreased) (\d+(?:\.\d+)?) percent/i],
      ["コアPCE価格指数 前月比", /From the preceding month, the PCE price index[^.]*? (?:increased|decreased) \d+(?:\.\d+)? percent\.\s+Excluding food and energy, the PCE price index (?:also )?(increased|decreased) (\d+(?:\.\d+)?) percent/i],
      ["PCE価格指数 前年比", /From the same month one year ago, the PCE price index[^.]*? (increased|decreased) (\d+(?:\.\d+)?) percent/i],
      ["コアPCE価格指数 前年比", /From the same month one year ago, the PCE price index[^.]*? (?:increased|decreased) \d+(?:\.\d+)? percent\.\s+Excluding food and energy, the PCE price index (?:also )?(increased|decreased) (\d+(?:\.\d+)?) percent/i]
    ] : [
      ["実質GDP 前期比年率", /Real gross domestic product \(GDP\) (increased|decreased) at an annual rate of (\d+(?:\.\d+)?) percent/i]
    ];
    const events = metrics.flatMap(([name, pattern]) => {
      const match = body.match(pattern);
      return match ? [{
        currency: "USD", title: name + " (" + title.trim() + ")", at_utc: new Date(at).toISOString(),
        official_actual: (match[1].toLowerCase() === "decreased" ? "-" : "") + match[2] + "%", source_url: url
      }] : [];
    });
    if (!events.length) throw Error("No BEA release figures parsed");
    return events;
  }));
  const events = settled.flatMap(item => item.status === "fulfilled" ? item.value : []);
  const errors = settled.flatMap(item => item.status === "rejected" ? [String(item.reason.message).slice(0, 160)] : []);
  if (!events.length && errors.length) throw Error(errors.join("; "));
  return { events, errors };
}

function releaseEvidence(live, saved, cutoff, now) {
  const sources = live.map(source => {
    if (source.status !== "UNAVAILABLE") return source;
    const previous = saved.find(item => item.url === source.url);
    const events = (previous?.events || []).filter(event => {
      const at = Date.parse(event.at_utc);
      return at <= cutoff && at >= cutoff - 36 * 3600000;
    });
    return events.length ? {...previous, events, status:"SCHEDULED_FALLBACK", live_error:source.error} : source;
  });
  const liveCount = sources.filter(s => ["VENDOR_ACTUALS","OFFICIAL_ACTUALS","NO_RECENT_ACTUALS"].includes(s.status)).length;
  const fallbackCount = sources.filter(s => s.status === "SCHEDULED_FALLBACK").length;
  const status = liveCount === sources.length && liveCount ? "LIVE" : liveCount ? "PARTIAL"
    : fallbackCount ? "SCHEDULED_FALLBACK" : "UNAVAILABLE";
  return {status, checked_at_utc:new Date(now).toISOString(), live_sources:liveCount,
    fallback_sources:fallbackCount, sources};
}

async function liveEconomicReleases(through) {
  return Promise.all(RELEASE_SOURCES.map(async ({name, url, format}) => {
    const started = Date.now();
    try {
      const response = await releasePage(url);
      const observedAt = Date.now();
      const { events, errors } = format === "bea" ? await beaReleases(response, through)
        : { events: await parseGmoReleases(response, observedAt, through), errors: [] };
      const status = events.length ? (format === "bea" ? "OFFICIAL_ACTUALS" : "VENDOR_ACTUALS") : "NO_RECENT_ACTUALS";
      const finished = Date.now();
      if (errors.length) console.warn("economic_release_partial", {name, errors});
      return { name, url, status, ...(errors.length ? { errors } : {}),
        observed_at_utc: new Date(finished).toISOString(), upstream_age_seconds: response.headers.get("Age"),
        fetch_duration_ms: finished - started,
        events: events.map(event => ({...event, observed_after_release_seconds:
          Math.floor((finished - Date.parse(event.at_utc)) / 1000)})) };
    } catch (error) {
      const message = String(error.message).slice(0, 160);
      console.warn("economic_release_unavailable", {name, error:message});
      return { name, url, status: "UNAVAILABLE", error: message, events: [] };
    }
  }));
}

async function savedMarketEvidence(cutoff) {
  const files = ["fx_intraday.json", "fx_flow_sources.json", "mof_securities_weekly.json"];
  const entries = await Promise.all(files.map(async file => {
    try {
      const data = await github(file);
      const retrieved = data.retrieved_at_utc ?? data.checked_at_utc;
      if (!Number.isFinite(Date.parse(retrieved))) throw Error("INVALID_RETRIEVAL_TIME");
      const ageMinutes = Math.round((Date.now() - Date.parse(retrieved)) / 60000);
      if (file === "fx_intraday.json") {
        if (!data.series || !Array.isArray(data.events)) throw Error("INVALID_INTRADAY_SCHEMA");
        const series = Object.fromEntries(Object.entries(data.series).map(([key, item]) => {
          const start = Math.floor(cutoff / 300000) * 300;
          const epoch = item.latest_epoch < start ? item.latest_epoch :
            Object.keys(item.bars || {}).map(Number).filter(key => key < start).reduce(
              (max, key) => Math.max(max, key), 0);
          const latest = item.bars?.[epoch];
          const prior = item.bars?.[epoch - 1800];
          const contiguous = Array.from({length: 7}, (_, index) =>
            item.bars?.[epoch - index * 300]).every(Boolean);
          if (!latest) return [key, {status: "NO_CONFIRMED_VALUE", source: item.url}];
          const lag = Math.round((cutoff / 1000 - epoch - 300) / 60);
          return [key, {status: lag <= 60 ? "OBSERVED_DELAYED" : "OLD_OBSERVATION",
            source: item.url, symbol: item.symbol, bar_start_utc: new Date(epoch * 1000).toISOString(),
            age_minutes: lag, close: latest.close, volume: latest.volume,
            change_30m: contiguous ? latest.close - prior.close : null}];
        }));
        return [file, {retrieved_at_utc: retrieved, age_minutes: ageMinutes, provider: data.provider,
          series, errors: data.errors, events: data.events.filter(event => {
            const end = Date.parse(event.end_bar_start_utc) + 300000;
            return end <= cutoff && end > cutoff - 24 * 3600000;
          }).slice(-5)}];
      }
      if (file === "mof_securities_weekly.json") {
        const {normalized} = data;
        if (data.schema_status !== "validated_v1" || !Array.isArray(normalized?.history))
          throw Error("INVALID_MOF_SCHEMA");
        return [file, {retrieved_at_utc: retrieved, age_minutes: ageMinutes, source: data.source,
          unit: normalized.unit, latest: normalized.latest, recent_weeks: normalized.history.slice(-4)}];
      }
      if (!data.bny || !data.ebs || !data.options) throw Error("INVALID_FLOW_SCHEMA");
      return [file, {checked_at_utc: retrieved, age_minutes: ageMinutes,
        bny: {...data.bny, reports: (data.bny.reports || []).slice(0, 3)},
        ebs: data.ebs, options: data.options, tfx: data.tfx}];
    } catch (error) {
      return [file, {status: "UNAVAILABLE", error: String(error.message).slice(0, 160)}];
    }
  }));
  return Object.fromEntries(entries);
}

function attachReleaseReactions(sources, rows) {
  const candles = new Map(rows.map(bar => [timestamp(bar.datetime), Number(bar.close)]));
  for (const source of sources) {
    for (const release of source.events) {
      const minute = Math.floor(Date.parse(release.at_utc) / 60000) * 60000;
      const base = candles.get(minute - 60000);
      if (!Number.isFinite(base)) continue;
      const latest = Math.min(60, Math.max(0, Math.floor((timestamp(rows[0].datetime) - minute) / 60000) + 1));
      const changes = {};
      for (const minutes of [5, 60]) {
        if (latest < minutes) continue;
        const complete = Array.from({length: minutes}, (_, index) =>
          candles.has(minute + index * 60000)).every(Boolean);
        if (complete) changes[minutes + "m"] = Number(((candles.get(minute + (minutes-1)*60000) - base)*100).toFixed(3));
      }
      release.usdjpy_reaction = {pre_event_close_jst: label(minute - 60000), change_pips: changes};
    }
  }
}

function reportProjection(data) {
  const a=data.analysis, m=a.market_structure;
  const rows=data.report_minutes;
  const latest=timestamp(data.latest_confirmed_1min_bar)+60000;
  const day=new Date(latest+32400000).toISOString().slice(0,10);
  const six=timestamp(day+" 06:00:00"), nine=timestamp(day+" 09:00:00");
  function stats(start,end) {
    const bars=rows.filter(b=>timestamp(b.datetime)>=start&&timestamp(b.datetime)<end).sort((x,y)=>timestamp(x.datetime)-timestamp(y.datetime));
    const base=rows.find(b=>timestamp(b.datetime)===start-60000);
    if(!base||bars.length!==(end-start)/60000||bars.some((b,i)=>timestamp(b.datetime)!==start+i*60000)) return {status:"INCOMPLETE_WINDOW"};
    const hi=bars.reduce((x,y)=>Number(x.high)>=Number(y.high)?x:y);
    const lo=bars.reduce((x,y)=>Number(x.low)<=Number(y.low)?x:y);
    const last=bars[bars.length-1];
    return {status:"COMPLETE",base_bar:base.datetime,end_bar:last.datetime,bars:bars.length,start_close:Number(base.close),end_close:Number(last.close),change_pips:Number(((Number(last.close)-Number(base.close))*100).toFixed(3)),high:Number(hi.high),high_at:hi.datetime,low:Number(lo.low),low_at:lo.datetime,range_pips:Number(((Number(hi.high)-Number(lo.low))*100).toFixed(3))};
  }
  const macro = data.macro_evidence;
  const projection = {
    symbol: data.symbol, fetched_at: data.fetched_at,
    latest_confirmed_1min_bar: data.latest_confirmed_1min_bar,
    freshness_ok: data.freshness_ok, twelve_data_requests: data.twelve_data_requests,
    analysis_integration: data.analysis_integration, live_overlay: data.live_overlay,
    data_quality: a.data_quality, timeframes: a.timeframes,
    timeframe_assessments: Object.fromEntries(Object.entries(m.timeframe_assessments || {}).map(
      ([key, frame]) => [key, {state: frame.state, atr14: frame.atr14,
        last_complete_bar: frame.last_complete_bar, swing_structure: frame.swing_structure,
        recent_progress_fraction: frame.recent_progress_fraction,
        recent_observed_width: frame.recent_observed_width}])),
    parent_direction: m.parent_direction, phase: m.phase, directional_bias: m.directional_bias,
    support_resistance: m.support_resistance, upper_stall: m.upper_stall,
    sharp_decline_recovery: m.sharp_decline_recovery, five_minute_rebound: m.five_minute_rebound,
    overnight: latest >= six ? stats(six - 6 * 3600000, six) : {status: "NOT_YET_06_JST"},
    six_am_reconstruction: m.six_am_reconstruction,
    six_to_nine: latest >= nine ? stats(six, nine) : {
      status: "NOT_YET_09_JST", ...(latest > six ? {partial: stats(six, latest)} : {})
    },
    fundamentals: a.fundamentals,
    macro_evidence: {
      status: macro.status, news: macro.news, news_sources: macro.news_sources,
      calendar: macro.calendar, economic_calendar: macro.economic_calendar,
      economic_releases: macro.economic_releases,
      economic_release_freshness: macro.economic_release_freshness,
      cross_market: macro.cross_market, saved_markets: macro.saved_markets,
      report_windows: Object.fromEntries(Object.entries(macro.report_windows || {}).map(
        ([key, articles]) => [key, articles.slice(0, 3).map(article => ({
          title: article.title, url: article.url, source_timestamp_utc: article.source_timestamp_utc,
          source_timestamp_kind: article.source_timestamp_kind
        }))]))
    },
    decision: a.decision,
    series_audit: Object.fromEntries(Object.entries(data.series).map(([key, series]) =>
      [key, {returned_bars: series.returned_bars, latest_confirmed_bar: series.latest_confirmed_bar,
        ...(series.returned_bars === 1 ? {latest: series.values[0]} : {})}])),
    full_analysis_location: "structuredContent.analysis"
  };
  return projection;
}

async function handleMCP(request, env) {
  if (request.method !== "POST") {
    return new Response(null, {status:405});
  }

  let rpc;

  try {
    rpc = await checkedRPC(request);
  } catch(e) {
    const tooLarge=String(e.message)==="MCP_BODY_TOO_LARGE";
    return respond({jsonrpc:"2.0",id:null,error:{code:tooLarge?-32000:-32700,
      message:tooLarge?"Request too large":"Parse error"}},tooLarge?413:400);
  }
  if(!rpc || typeof rpc!=="object" || Array.isArray(rpc) || rpc.jsonrpc!=="2.0") {
    return respond({jsonrpc:"2.0",id:rpc?.id??null,error:{code:-32600,message:"Invalid Request"}},400);
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
        const args = validateToolArgs(name, rpc.params?.arguments);
        let data;

        if (name === "get_usdjpy_analysis") {
          data = await analysisWithFreshPython(env,args);
          const releaseCutoff = timestamp(data.latest_confirmed_1min_bar) + 60000;
          const releaseLookup = liveEconomicReleases(releaseCutoff);
          const marketLookup = savedMarketEvidence(releaseCutoff);
          let scheduledReleases = [];
          try {
            const macro=await github("macro_brief_latest.json");
            scheduledReleases = Array.isArray(macro.economic_release_snapshot?.sources)
              ? macro.economic_release_snapshot.sources : [];
            if (!Array.isArray(macro.candidates) || macro.candidate_count !== macro.candidates.length ||
                !Number.isFinite(Date.parse(macro.generated_at_utc))) throw Error("Invalid macro snapshot");
            const age=(Date.now()-Date.parse(macro.generated_at_utc))/60000;
            const end=timestamp(data.latest_confirmed_1min_bar)+60000;
            const start=end-24*3600000;
            const newsStatus = age >= 0 && age <= 45 ? macro.status : "STALE";
            if (newsStatus === "STALE") console.warn("macro_news_stale", {age_minutes:age, generated_at_utc:macro.generated_at_utc});
            data.macro_evidence={status:"AVAILABLE", news:{status:newsStatus, age_minutes:age,
              generated_at_utc:macro.generated_at_utc},
              generated_at_utc:macro.generated_at_utc,age_minutes:age,news_sources:macro.news_sources,
              calendar:macro.calendar,economic_calendar:macro.economic_calendar ? {
                status:macro.economic_calendar.status,source:macro.economic_calendar.source,
                checked_at_utc:macro.economic_calendar.checked_at_utc,
                error:macro.economic_calendar.error,
                events:(macro.economic_calendar.events || []).filter(event => {
                  const at = Date.parse(event.at_utc);
                  return at >= end - 72 * 3600000 && at <= end + 72 * 3600000;
                }).map(event => ({title:event.title,currency:event.currency,at_utc:event.at_utc,
                  impact:event.impact,actual:event.vendor_actual,forecast:event.pre_release_forecast ?? event.vendor_forecast,
                  previous:event.pre_release_previous ?? event.vendor_previous,result_status:event.result_status}))
              } : null,cross_market:macro.cross_market,intraday_evidence:macro.intraday_evidence,
              report_windows:(()=>{const day=new Date(end+32400000).toISOString().slice(0,10);
                const six=timestamp(day+" 06:00:00"),nine=timestamp(day+" 09:00:00");
                const select=(from,to)=>(macro.candidates||[]).filter(x=>Date.parse(x.source_timestamp_utc)>=from&&Date.parse(x.source_timestamp_utc)<to).sort((a,b)=>b.priority_score_not_causal_confidence-a.priority_score_not_causal_confidence).slice(0,8);
                return {overnight:select(six-12*3600000,Math.min(six,end)),six_to_nine:end>=six?select(six,Math.min(nine,end)):[]};})(),
              candidates:(macro.candidates||[]).filter(x=>Date.parse(x.source_timestamp_utc)>=start&&Date.parse(x.source_timestamp_utc)<=end)
                .sort((a,b)=>b.priority_score_not_causal_confidence-a.priority_score_not_causal_confidence).slice(0,12),
              confirmed_catalyst:null};
          } catch(e) {
            const error=String(e.message).slice(0,180);
            console.warn("macro_snapshot_unavailable", {error});
            data.macro_evidence={status:"UNAVAILABLE",news:{status:"UNAVAILABLE",error}};
          }
          const {sources, ...freshness} = releaseEvidence(await releaseLookup, scheduledReleases, releaseCutoff, Date.now());
          data.macro_evidence.saved_markets = await marketLookup;
          attachReleaseReactions(sources, data.report_minutes);
          data.macro_evidence.economic_releases = sources;
          data.macro_evidence.economic_release_freshness = freshness;
          data.macro_evidence.economic_calendar = {
            ...(data.macro_evidence.economic_calendar || {}),
            schedule_feed_status:data.macro_evidence.economic_calendar?.status || "UNAVAILABLE",
            status:freshness.status,
            actuals_connected:freshness.status !== "UNAVAILABLE"
          };
          if (freshness.status !== "UNAVAILABLE") data.macro_evidence.status = "AVAILABLE";
          data.analysis.fundamentals = {...data.analysis.fundamentals,event_calendar:freshness.status};
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

        let output = data;
        let structured = data;
        if (name === "get_usdjpy_analysis") {
          output = reportProjection(data);
          const {report_minutes, ...full} = data;
          structured = args.detail === "legacy"
            ? {...full, python_analysis_snapshot: full.analysis}
            : {...full, macro_evidence: output.macro_evidence, six_to_nine: output.six_to_nine};
        }
        result = {content: [{type: "text", text: JSON.stringify(output)}],
          structuredContent: structured, isError: false};
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


// Security regression tests run before every production deployment.
const MAX_RPC_BYTES = 16384;
function validateMCPEnvelope(request, url) {
  if (url.pathname !== "/mcp") return null;
  if (request.method !== "POST") {
    return new Response(null,{status:405,headers:{"Allow":"POST","Cache-Control":"no-store"}});
  }
  const origin = request.headers.get("Origin");
  if (origin) {
    let parsed;
    try { parsed=new URL(origin); } catch (_) {
      return respond({error:"INVALID_ORIGIN"},403);
    }
    if (parsed.origin !== url.origin) return respond({error:"CROSS_ORIGIN_MCP_FORBIDDEN"},403);
  }
  const ct=(request.headers.get("Content-Type")||"").toLowerCase();
  if (ct.split(";")[0].trim() !== "application/json") {
    return respond({error:"JSON_CONTENT_TYPE_REQUIRED"},415);
  }
  const len=request.headers.get("Content-Length");
  if (len !== null && (!Number.isSafeInteger(Number(len)) ||
      Number(len)<0 || Number(len)>MAX_RPC_BYTES)) {
    return respond({error:"MCP_BODY_TOO_LARGE"},413);
  }
  return null;
}
async function checkedRPC(request) {
  const stream=request.body;
  if (!stream) throw Error("EMPTY_MCP_BODY");
  const reader=stream.getReader();
  const chunks=[];
  let total=0;
  try {
    while (true) {
      const {done,value}=await reader.read();
      if (done) break;
      total+=value.byteLength;
      if(total>MAX_RPC_BYTES) throw Error("MCP_BODY_TOO_LARGE");
      chunks.push(value);
    }
  } finally {try{await reader.cancel()}catch(_){}}
  const bytes=new Uint8Array(total);
  let index=0;
  for(const c of chunks){bytes.set(c,index);index+=c.byteLength}
  return JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes));
}
function validateToolArgs(name, args) {
  if (args===undefined) args={};
  if (args===null || Array.isArray(args) || typeof args!=="object") {
    throw Error("INVALID_TOOL_ARGUMENTS");
  }
  const fields=name==="get_usdjpy_history"
    ? ["interval","size","start_date","end_date"]
    : name==="get_latest_usdjpy" ? ["interval","size"]
    : name==="get_usdjpy_analysis" || name==="get_usdjpy_bundle"
      ? ["minute_bars","five_minute_bars","fifteen_minute_bars","hourly_bars","four_hour_bars","daily_bars",
         ...(name === "get_usdjpy_analysis" ? ["detail"] : [])]
      : [];
  if(!fields.length || Object.keys(args).some(k=>!fields.includes(k))) {
    throw Error("UNKNOWN_TOOL_OR_ARGUMENT");
  }
  for(const [k,v] of Object.entries(args)){
    if(k==="detail"){
      if (!["report", "legacy"].includes(v)) throw Error("INVALID_DETAIL");
    }else if(k==="interval"){
      const intervals=name==="get_usdjpy_history" ?
        ["1h","4h","1day"] : ["1min","5min","15min","1h","4h","1day"];
      if(!intervals.includes(v)) throw Error("INVALID_INTERVAL");
    }else if(k==="start_date" || k==="end_date"){
      if(typeof v!=="string" || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(v) ||
        Number.isNaN(Date.parse(v+"T00:00:00Z"))) throw Error("INVALID_DATE");
    }else if(!Number.isInteger(v) || v<1 || v>2000) {
      throw Error("INVALID_BAR_COUNT");
    }
  }
  if(name==="get_usdjpy_history" && !args.interval) throw Error("HISTORY_INTERVAL_REQUIRED");
  if(args.start_date && args.end_date && args.start_date>args.end_date) {
    throw Error("INVALID_DATE_RANGE");
  }
  return args;
}

// Anonymous MCP access remains read-only. Bound rate limits reduce the cost
// exposure while a proper OAuth login/connection is provisioned. Do not put
// Cloudflare API or GitHub write tokens in this public resource server.
async function protectExpensiveRequest(request, env, url) {
  const isBundle = url.pathname === "/bundle";
  const isCalendar = url.pathname === "/economic-releases" && request.method === "GET";
  if (!isBundle && !isCalendar && url.pathname !== "/mcp") return null;
  let isExpensive = isBundle || isCalendar;
  if (url.pathname === "/mcp" && request.method === "POST") {
    try {
      const rpc = await checkedRPC(request.clone());
      isExpensive = rpc?.method === "tools/call";
    } catch (_) { return null; } // Existing MCP parser returns parse error.
  }
  if (!isExpensive) return null;
  if (!env.USDJPY_IP_LIMIT?.limit || !env.USDJPY_POP_LIMIT?.limit) {
    return respond({error:"SERVER_RATE_LIMIT_CONFIGURATION_MISSING"},503);
  }
  const ip = request.headers.get("CF-Connecting-IP") || "unidentified";
  const [perIp, pop] = await Promise.all([
    env.USDJPY_IP_LIMIT.limit({key:ip}),
    env.USDJPY_POP_LIMIT.limit({key:"usd-jpy-analysis"})
  ]);
  if (!perIp.success || !pop.success) {
    return new Response(JSON.stringify({error:"RATE_LIMITED",retry_after_seconds:60}),{
      status:429,
      headers:{"Content-Type":"application/json","Retry-After":"60","Cache-Control":"no-store"}
    });
  }
  return null;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if(url.pathname!=="/mcp" && url.pathname!=="/" && url.pathname!=="/economic-releases") {
      return respond({error:"NOT_FOUND"},404);
    }
    if (url.pathname === "/economic-releases" && request.method !== "GET") {
      return new Response(null,{status:405,headers:{Allow:"GET","Cache-Control":"no-store"}});
    }
    const invalid=validateMCPEnvelope(request,url);
    if(invalid) return invalid;
    const denied = await protectExpensiveRequest(request,env,url);
    if (denied) return denied;

    if (url.pathname === "/mcp") {
      return handleMCP(request,env);
    }

    if (url.pathname === "/economic-releases") {
      const now = Date.now();
      return respond({ checked_at_utc: new Date(now).toISOString(), sources: await liveEconomicReleases(now) });
    }

    return respond({
      service:"USDJPY Market Data",
      version:"4.1.0",
      mcp:"/mcp",
      status:"read-only"
    });
  }
};
