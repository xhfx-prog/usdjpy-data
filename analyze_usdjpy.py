#!/usr/bin/env python3
"""Deterministic USD/JPY market-structure analysis; Python 3.11, stdlib only.

Do not infer order-flow participant identities or event causation from OHLC.
Do not present stale prices as executable quotes. Source bars are provider OHLC,
not explicitly bid, ask, or mid.
"""
import argparse
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NY = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
UTC = timezone.utc
DATA = Path("data")
VERSION = "1.0.0"


def stamp(s):
    # ISO-formatted provider timestamps: fromisoformat avoids the expensive
    # _strptime parser thousands of times inside the Cloudflare Python Worker.
    return datetime.fromisoformat(s).replace(tzinfo=JST)


def iso(d):
    return d.isoformat(timespec="seconds")


def n(x):
    return round(float(x), 5)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def safe_read(path):
    try:
        return read(path)
    except (OSError, ValueError):
        return {}


def validate_source(obj, interval):
    if obj.get("source") != "Twelve Data" or obj.get("interval") != interval:
        raise ValueError("source_or_interval_mismatch:" + interval)
    if obj.get("data", {}).get("status") != "ok":
        raise ValueError("source_data_not_ok:" + interval)
    if interval != "1day" and obj.get("timezone") != "Asia/Tokyo":
        raise ValueError("timezone_mismatch:" + interval)
    if not obj.get("fetched_at") or not obj.get("data", {}).get("values"):
        raise ValueError("missing_source_time_or_values:" + interval)


def normalized(rows, asof, interval, max_invalid_fraction=0.015):
    out = {}
    rejected = 0
    for b in rows:
        try:
            op, hi, lo, cl = (float(b[k]) for k in ("open", "high", "low", "close"))
            if not all(math.isfinite(z) for z in (op, hi, lo, cl)):
                raise ValueError("nonfinite")
            if not (0 < lo <= op <= hi and lo <= cl <= hi):
                raise ValueError("invalid_ohlc")
            dt = stamp(b["datetime"]) if interval != "1day" else None
            duration = {"1min": 1, "1h": 60}.get(interval)
            if interval == "1day":
                if b["datetime"][:10] >= asof.date().isoformat():
                    continue
            elif dt + timedelta(minutes=duration) > asof:
                continue
            out[b["datetime"]] = {"t": b["datetime"], "o": op, "h": hi, "l": lo, "c": cl}
        except (KeyError, ValueError, TypeError):
            rejected += 1
    if rows and rejected / len(rows) > max_invalid_fraction:
        raise ValueError("excess_invalid_candles")
    return [out[k] for k in sorted(out)]


def load_market(data_dir=DATA, asof=None):
    asof = asof or datetime.now(JST)
    if asof.tzinfo is None:
        raise ValueError("asof_requires_timezone")
    asof = asof.astimezone(JST)
    objs = {i: read(Path(data_dir) / ("usdjpy_" + i + ".json"))
            for i in ("1min", "1h", "1day")}
    for k, v in objs.items():
        validate_source(v, k)
    minrows = objs["1min"]["data"]["values"][:]
    ix = safe_read(Path(data_dir) / "history_1min_index.json")
    if ix:
        for item in ix.get("sessions", [])[:3]:
            p = Path(item["path"])
            if not p.is_relative_to(Path(data_dir)) or ".." in p.parts:
                raise ValueError("unsafe_minute_archive_path")
            ar = safe_read(p)
            if ar:
                validate_source(ar, "1min")
                minrows += ar["data"]["values"]
    hourrows = objs["1h"]["data"]["values"][:]
    hix = safe_read(Path(data_dir) / "history_1h_index.json")
    if hix:
        for item in hix.get("partitions", [])[:3]:
            p = Path(item["path"])
            if not p.is_relative_to(Path(data_dir)) or ".." in p.parts:
                raise ValueError("unsafe_hour_archive_path")
            ar = safe_read(p)
            if ar:
                validate_source(ar, "1h")
                hourrows += ar["data"]["values"]
    minute = normalized(minrows, asof, "1min")
    hourly = normalized(hourrows, asof, "1h")
    daily = [b for b in normalized(objs["1day"]["data"]["values"], asof, "1day")
             if datetime.fromisoformat(b["t"]).weekday() < 5]  # exclude weekend pseudo-bars
    return minute, hourly, daily, objs, ix, hix


def aggregate(rows, minutes):
    """Only form bars with all expected constituent 1min or 1h candles."""
    if not rows:
        return []
    base = 1
    if minutes >= 60 and len(rows) > 1:
        delta = (stamp(rows[1]["t"]) - stamp(rows[0]["t"])).total_seconds() / 60
        if delta >= 60:
            base = 60
    expected = minutes // base
    grouped = defaultdict(list)
    for bar in rows:
        dt = stamp(bar["t"])
        if base == 1:
            start = dt.replace(minute=(dt.minute // minutes) * minutes, second=0)
        else:
            start = dt.replace(hour=(dt.hour // (minutes // 60)) * (minutes // 60),
                               minute=0, second=0)
        grouped[start].append(bar)
    answer = []
    for dt, group in sorted(grouped.items()):
        group.sort(key=lambda x: x["t"])
        if len(group) != expected:
            continue
        starts = [stamp(g["t"]) for g in group]
        if starts[0] != dt or any((starts[i] - starts[i-1]).total_seconds() != base * 60
                                  for i in range(1, len(starts))):
            continue
        answer.append({"t": dt.strftime("%Y-%m-%d %H:%M:%S"),
                       "o": group[0]["o"], "h": max(b["h"] for b in group),
                       "l": min(b["l"] for b in group), "c": group[-1]["c"]})
    return answer


def pivots(rows, field, radius):
    found = []
    for i in range(radius, len(rows)-radius):
        cur = rows[i][field]
        near = rows[i-radius:i] + rows[i+1:i+radius+1]
        if all((cur > z[field] if field == "h" else cur < z[field]) for z in near):
            found.append({"t": rows[i]["t"], "price": cur})
    return found


def levels(m5, m15, hour, fourh, daily, latest):
    """Group separated swing turning points; require repeated reactions."""
    points = []
    for label, arr, rad, width in (
        ("5min", m5[-240:], 3, 0.025),
        ("15min", m15[-240:], 2, 0.035),
        ("1h", hour[-240:], 2, 0.055),
        ("4h", fourh[-180:], 2, 0.075),
        ("1day", daily[-180:], 2, 0.095),
    ):
        for side in ("h", "l"):
            for p in pivots(arr, side, rad):
                points.append({"t": p["t"], "price": p["price"], "side":side,
                               "tf":label, "width": width})
    out = {"support": [], "resistance": []}
    for side, name in (("l", "support"), ("h", "resistance")):
        selected = sorted((x for x in points if x["side"] == side), key=lambda x:x["price"])
        clusters = []
        # Fixed maximum span prevents transitive chaining into unusable
        # 15-20 pip "zones" which obscure nearby distinct supports/resistances.
        max_span = 0.065
        for p in selected:
            matches = [c for c in clusters
                       if abs(p["price"] - c["center"]) <= 0.0325
                       and max(c["hi"],p["price"])-min(c["lo"],p["price"]) <= max_span]
            if matches:
                c = min(matches, key=lambda x: abs(x["center"]-p["price"]))
                c["points"].append(p)
                c["center"] = statistics.median(q["price"] for q in c["points"])
                c["lo"] = min(c["lo"],p["price"])
                c["hi"] = max(c["hi"],p["price"])
            else:
                clusters.append({"points":[p], "center":p["price"],
                                 "lo":p["price"],"hi":p["price"]})
        for c in clusters:
            z = sorted(c["points"], key=lambda x:x["t"])
            # A single event visible on multiple timeframes is not multiple reactions.
            independent = []
            for item in z:
                try:
                    dt = stamp(item["t"]) if len(item["t"]) > 10 else None
                except ValueError:
                    dt = None
                if dt is None:
                    # Daily events are independent only on different dates.
                    if not any(y["t"][:10] == item["t"][:10] for y in independent):
                        independent.append(item)
                elif not any(abs((dt-stamp(y["t"])).total_seconds()) < 30*60
                             for y in independent if len(y["t"]) > 10):
                    independent.append(item)
            if len(independent) < 2:
                continue
            lower, upper = min(zq["price"] for zq in z), max(zq["price"] for zq in z)
            mid = statistics.median(zq["price"] for zq in z)
            if name == "support" and mid > latest + 0.012:
                continue
            if name == "resistance" and mid < latest - 0.012:
                continue
            entry = {"low": n(lower), "high": n(upper), "center": n(mid),
                     "independent_reactions": len(independent),
                     "timeframes": sorted(set(q["tf"] for q in z)),
                     "last_reaction": max(q["t"] for q in z),
                     "distance_pips": n(abs(mid-latest)*100)}
            out[name].append(entry)
        out[name].sort(key=lambda q: abs(q["center"]-latest))
        out[name] = out[name][:3]
    return out


def upper_plateau(minutes, asof, lookback=480):
    """Find repeated near-ceiling tests in a 40-minute window.

    A 1-minute touch count is never labeled the number of independent failures.
    """
    series = minutes[-lookback:]
    candidates = []
    if len(series) < 50:
        return None
    for end in range(40, len(series)+1, 5):
        w = series[end-40:end]
        ceiling = max(b["h"] for b in w)
        touch = [b for b in w if b["h"] >= ceiling - 0.020]
        if len(touch) < 12:
            continue
        span = (stamp(touch[-1]["t"])-stamp(touch[0]["t"])).total_seconds()/60
        if span < 18 or ceiling-min(b["l"] for b in w) > 0.115:
            continue
        # Independent failed attempts require a 2-pip excursion away first.
        visits = 0
        away = True
        for b in w:
            # A completed close away from the top establishes a separate attempt.
            # OHLC lows alone cannot establish the intrabar order of events.
            if b["c"] < ceiling - 0.027:
                away = True
            elif b["h"] >= ceiling - 0.020 and away:
                visits += 1
                away = False
        if visits < 2:
            continue
        after = series[end:]
        drop = max(0, ceiling-min((b["c"] for b in after), default=ceiling))
        invalidated = any(b["c"] > ceiling + 0.018 for b in after)
        # Once price trades/settles materially above this ceiling it is not
        # a currently active failed resistance. An older 158.20 plateau must
        # not outrank the more recent 158.30 rejection.
        if invalidated:
            continue
        candidates.append({"start":touch[0]["t"], "end":touch[-1]["t"],
                           "ceiling":n(ceiling), "near_ceiling_bars":len(touch),
                           "independent_attempts":visits, "duration_minutes":int(span),
                           "subsequent_drop_pips":n(drop*100),
                           "breakout_confirmed_after":invalidated,
                           "_score":len(touch)+3*visits+min(drop*50,12)})
    if not candidates:
        return None
    # A more recent plateau can replace an older one if similarly well supported.
    best=max(candidates,key=lambda q:(q["_score"]-0.03*((asof-stamp(q["end"])).total_seconds()/60)))
    best.pop("_score")
    return best


def shock_and_recovery(minutes, lookback=150):
    """Identify a sharp downward impulse, then measure actual recovery."""
    xs=minutes[-lookback:]
    if len(xs) < 20:
        return None
    candidates=[]
    for i in range(15,len(xs)-2):
        pre=xs[max(0,i-20):i]
        peak=max(b["h"] for b in pre)
        fall=peak-xs[i]["l"]
        if fall < 0.14:
            continue
        # Focus on locally distinct troughs, not every candle of the descent.
        if any(z["l"] < xs[i]["l"] for z in xs[max(0,i-3):min(len(xs),i+4)]):
            continue
        recovered=xs[-1]["c"]-xs[i]["l"]
        if recovered < 0:
            recovered=0
        candidates.append({"trough_time":xs[i]["t"],"trough":n(xs[i]["l"]),
                           "pre_20min_high":n(peak),"drop_pips":n(fall*100),
                           "last_close":n(xs[-1]["c"]),
                           "recovery_fraction":n(min(1.0,recovered/fall)),
                           "recovered_above_pre_high":xs[-1]["c"]>=peak,
                           "seconds_since_trough":int((stamp(xs[-1]["t"])-stamp(xs[i]["t"])).total_seconds())})
    if not candidates:
        return None
    return max(candidates,key=lambda z:(z["drop_pips"],z["trough_time"]))


def atr(rows, count=14):
    if len(rows) < count+1:
        return None
    tr=[]
    for i in range(len(rows)-count,len(rows)):
        b,p=rows[i],rows[i-1]
        tr.append(max(b["h"]-b["l"],abs(b["h"]-p["c"]),abs(b["l"]-p["c"])))
    return n(statistics.mean(tr))


def session_info(asof, minute):
    local={"tokyo":asof.astimezone(JST),"london":asof.astimezone(LONDON),
           "new_york":asof.astimezone(NY)}
    windows={"tokyo":(9,17),"london":(8,17),"new_york":(8,17)}
    active={k:hours[0] <= d.hour < hours[1] and d.weekday()<5
            for (k,d),hours in zip(local.items(),windows.values())}
    # 08:30 NY is a common release/flow time, not necessarily a scheduled release.
    ny=local["new_york"]
    flags=[]
    if 8<=ny.hour<=9:
        flags.append("US_OPENING_FLOW_WINDOW_NO_CAUSAL_ATTRIBUTION")
    if 8==ny.hour and ny.minute>=25 and ny.minute<=40:
        flags.append("US_0830_WINDOW_VERIFY_OFFICIAL_RELEASE")
    if 9==local["tokyo"].hour and 45<=local["tokyo"].minute<=59:
        flags.append("TOKYO_0955_FIX_WINDOW")
    if local["tokyo"].day in (5,10,15,20,25,30):
        flags.append("GOTOBI_CALENDAR_DAY_NOT_HOLIDAY_ADJUSTED")
    if local["tokyo"].month in (3,6,9,12) and local["tokyo"].day>=26:
        flags.append("QUARTER_END_CANDIDATE_NOT_HOLIDAY_ADJUSTED")
    if local["tokyo"].weekday()==4:
        flags.append("FRIDAY_WEEKEND_POSITIONING_POSSIBLE")
    # Observe real session moves, not participant motives.
    observed={}
    for k,on in active.items():
        d=local[k]
        start=d.replace(hour=windows[k][0],minute=0,second=0,microsecond=0).astimezone(JST)
        bars=[b for b in minute if stamp(b["t"])>=start] if on else []
        observed[k]={"active":on,"local_time":iso(d),"bars":len(bars),
                     "session_high":n(max(b["h"] for b in bars)) if bars else None,
                     "session_low":n(min(b["l"] for b in bars)) if bars else None}
    return {"observed_sessions":observed,"time_flags":flags,
            "weekday_jst":asof.strftime("%A"),"session_effect_probabilities":"UNVALIDATED"}



def session_opening_behavior(minute, asof):
    """Observe initial moves and their reversal; never infer participant motives."""
    specs=(("TOKYO_0900",JST,9,0),("TOKYO_FIX_0955",JST,9,55),
           ("LONDON_0800",LONDON,8,0),("NEW_YORK_0830",NY,8,30),
           ("NEW_YORK_CASH_0930",NY,9,30))
    result=[]
    for label,zone,hh,mm in specs:
        local=asof.astimezone(zone)
        event=local.replace(hour=hh,minute=mm,second=0,microsecond=0)
        start=event.astimezone(JST)
        elapsed=(asof-start).total_seconds()/60
        if elapsed<15 or elapsed>90 or event.weekday()>=5:
            continue
        before=[b for b in minute if start-timedelta(minutes=20)<=stamp(b["t"])<start]
        first=[b for b in minute if start<=stamp(b["t"])<start+timedelta(minutes=15)]
        if len(before)<12 or len(first)<12:
            continue
        reference=before[-1]["c"]
        hi=max(b["h"] for b in first)
        lo=min(b["l"] for b in first)
        up=hi-reference
        down=reference-lo
        if max(up,down)<0.08:
            interpretation="NO_LARGE_INITIAL_IMPULSE"
            move="none"
        else:
            move="down" if down>up else "up"
            if move=="down":
                interpretation=("INITIAL_DOWN_REJECTED" if minute[-1]["c"]>=reference-0.025
                                else "INITIAL_DOWN_NOT_FULLY_REVERSED")
            else:
                interpretation=("INITIAL_UP_REJECTED" if minute[-1]["c"]<=reference+0.025
                                else "INITIAL_UP_NOT_FULLY_REVERSED")
        result.append({"window":label,"window_start_jst":iso(start),
                       "minutes_since_window":int(elapsed),
                       "pre_window_close":n(reference),"first_15min_high":n(hi),
                       "first_15min_low":n(lo),"first_direction":move,
                       "latest_close":n(minute[-1]["c"]),
                       "interpretation":interpretation,
                       "causality":"NOT_ESTABLISHED_FROM_OHLC"})
    return result



def ema_series(values, period):
    """Standard EMA, with a simple-average seed to avoid startup distortion."""
    if len(values) < period:
        return []
    seed = statistics.mean(values[:period])
    result = [None] * (period - 1) + [seed]
    for value in values[period:]:
        result.append(result[-1] + (value-result[-1]) * 2/(period+1))
    return result


def describe_timeframe(rows, name):
    """Classify the CURRENT structure, not the lagged 20/50-bar EMA trend.

    A prior rally may leave a bullish background, but ten sideways 1h
    candles must be called a range rather than an ongoing hourly uptrend.
    Every observation uses closed candles only.
    """
    window = {"1day": 8, "4h": 8, "1h": 10,
              "15min": 12, "5min": 12, "1min": 20}.get(name, 10)
    if len(rows) < max(16, window + 2):
        return {"state": "INSUFFICIENT_HISTORY", "bars": len(rows)}
    volatility = atr(rows)
    if not volatility or volatility <= 0:
        return {"state": "INSUFFICIENT_VOLATILITY", "bars": len(rows)}
    recent = rows[-window:]
    width = max(x["h"] for x in recent) - min(x["l"] for x in recent)
    if width <= 0:
        return {"state": "UNRESOLVED", "bars": len(rows)}
    closes = [x["c"] for x in recent]
    net = closes[-1] - closes[0]
    progress = net / width
    step_threshold = max(width * 0.012, volatility * 0.04)
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    signs = [1 if d > step_threshold else -1 if d < -step_threshold else 0
             for d in changes]
    active = [x for x in signs if x]
    reversals = sum(active[i] != active[i-1] for i in range(1, len(active)))
    prior = rows[-min(100, len(rows)):]
    highs = pivots(prior, "h", 2)
    lows = pivots(prior, "l", 2)
    swing = "UNRESOLVED"
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1]["price"] > highs[-2]["price"]
        hl = lows[-1]["price"] > lows[-2]["price"]
        lh = highs[-1]["price"] < highs[-2]["price"]
        ll = lows[-1]["price"] < lows[-2]["price"]
        swing = "UP" if hh and hl else "DOWN" if lh and ll else "MIXED"
    # Near-zero net progress despite substantial movement is an actual range
    # (not an EMA-delayed trend). Use reversals as supporting evidence.
    if abs(progress) <= 0.25 and (reversals >= 2 or abs(net) <= volatility * 0.10):
        state = "RANGE"
    elif progress >= 0.38 and net >= volatility * 0.5:
        state = "UP"
    elif progress <= -0.38 and -net >= volatility * 0.5:
        state = "DOWN"
    elif progress >= 0.25 and swing == "UP":
        state = "UP"
    elif progress <= -0.25 and swing == "DOWN":
        state = "DOWN"
    else:
        state = "UNRESOLVED"
    return {
        "state": state,
        "swing_structure": swing,
        "recent_net_change": n(net),
        "recent_net_change_pips": n(net * 100),
        "recent_progress_fraction": n(progress),
        "recent_direction_reversals": reversals,
        "recent_observed_width": n(width),
        "recent_width_in_atr": n(width / volatility),
        "recent_window_bars": len(recent),
        "atr14": n(volatility),
        "bars": len(rows),
        "last_complete_bar": rows[-1]["t"]
    }

def multi_timeframe_structure(daily, h4, h1, m15, m5, minute):
    # Output in descending timeframe order. Every classification stands
    # independently; the 1-minute result cannot replace the daily/1h view.
    ordered = (("1day", daily), ("4h", h4), ("1h", h1),
               ("15min", m15), ("5min", m5), ("1min", minute))
    frames = {name: describe_timeframe(rows, name) for name,rows in ordered}
    parent = next((frames[k]["state"] for k in ("4h","1h","1day")
                   if frames[k]["state"] in ("UP","DOWN")), "UNRESOLVED")
    return frames, parent

def evaluate(minute, hourly, daily, asof, sources=None, extra=None):
    asof=asof.astimezone(JST)
    if not minute:
        raise ValueError("no_confirmed_minute_candles")
    latest=minute[-1]
    age=(asof-stamp(latest["t"])).total_seconds()/60
    h5=aggregate(minute,5)
    h15=aggregate(minute,15)
    # Prefer the provider hour when available; fill gaps with complete 1-minute hours.
    h1=normalized_hourly_merge(hourly, aggregate(minute,60))
    h4=aggregate(h1,240)
    if len(h5)<10 or len(h1)<10 or len(daily)<50:
        raise ValueError("insufficient_confirmed_multitimeframe_history")
    pl=upper_plateau(minute,asof)
    shock=shock_and_recovery(minute)
    ls=levels(h5,h15,h1,h4,daily,latest["c"])
    timeframe_states, parent_direction = multi_timeframe_structure(daily,h4,h1,h15,h5,minute)
    hour6=h1[-6:]
    ups=sum(hour6[i]["h"]>hour6[i-1]["h"] and hour6[i]["l"]>hour6[i-1]["l"]
            for i in range(1,len(hour6)))
    downs=sum(hour6[i]["h"]<hour6[i-1]["h"] and hour6[i]["l"]<hour6[i-1]["l"]
              for i in range(1,len(hour6)))
    past_up=ups>=3
    recent_fade=bool(pl and pl["subsequent_drop_pips"]>=6 and
                     latest["c"]<pl["ceiling"]-0.06 and not pl["breakout_confirmed_after"])
    recovered=bool(shock and shock["recovery_fraction"]>=0.60)
    stale=age>10 or age<0
    if stale:
        phase="UNAVAILABLE_STALE"
        bias="NO_CURRENT_DIRECTION"
    elif parent_direction=="UP":
        if timeframe_states["1h"]["state"]=="RANGE":
            phase="PRIOR_UPTREND_WITH_1H_CONSOLIDATION"
            bias="RANGE_WAIT_BREAK"
        else:
            phase=("UPTREND_WITH_LOCAL_REJECTION" if recent_fade else
                   "UPTREND_WITH_LOCAL_CONSOLIDATION" if pl else "UPTREND")
            bias="UPTREND_PULLBACK_UNCONFIRMED" if recent_fade else "UP_CANDIDATE"
    elif parent_direction=="DOWN":
        if timeframe_states["1h"]["state"]=="RANGE":
            phase="PRIOR_DOWNTREND_WITH_1H_CONSOLIDATION"
            bias="RANGE_WAIT_BREAK"
        else:
            phase=("DOWNTREND_WITH_LOCAL_REBOUND" if recovered else
                   "DOWNTREND_WITH_LOCAL_CONSOLIDATION" if pl else "DOWNTREND")
            bias="DOWNTREND_REBOUND_UNCONFIRMED" if recovered else "DOWN_CANDIDATE"
    elif recent_fade and recovered:
        phase="FAILED_CEILING_WITH_SHOCK_REBOUND"
        bias="TRANSITION_NO_DIRECTIONAL_EDGE"
    elif recent_fade:
        phase="UPWARD_MOMENTUM_STALLED"
        bias="RANGE_OR_PULLBACK"
    elif recovered:
        phase="SHOCK_REBOUND_UNCONFIRMED"
        bias="TRANSITION_NO_DIRECTIONAL_EDGE"
    else:
        phase="RANGE_OR_UNRESOLVED"
        bias="NO_DIRECTIONAL_EDGE"
    # Unlike the former 6-candle rule, local pullbacks and plateaus cannot
    # override independently measured 1h/4h trend evidence.
    session=session_info(asof,minute)
    first_res=next((x for x in ls["resistance"] if x["center"]>latest["c"]+0.005),None)
    first_sup=next((x for x in ls["support"] if x["center"]<latest["c"]-0.005),None)
    # These are detected observations, never invented forecasts or guaranteed fills.
    price_usable=not stale
    report={
        "schema_version":VERSION,
        "generated_at_utc":iso(datetime.now(UTC)),
        "analysis_asof_jst":iso(asof),
        "instrument":"USD/JPY",
        "data_quality":{"usable_for_current_trading":price_usable,
            "last_confirmed_1min_start_jst":latest["t"],
            "last_close":n(latest["c"]),
            "price_type":"OHLC_UNSPECIFIED_BID_ASK_MID",
            "age_minutes":n(age),
            "stale_threshold_minutes":10,
            "source_fetched_at_utc":(sources or {}).get("fetched_at"),
            "quality_flags":([] if not stale else ["STALE_PRICE_NO_CURRENT_LEVELS"])
             + ([] if len(minute)>2500 else ["SHORT_INTRADAY_ARCHIVE"] )},
        "timeframes":{"complete_5min":len(h5),"complete_15min":len(h15),
             "complete_1h":len(h1),"complete_4h":len(h4),
             "day_candles":len(daily),"hourly_atr14":atr(h1),
             "last_complete_5min":h5[-1]["t"],"last_complete_1h":h1[-1]["t"]},
        "market_structure":{"timeframe_assessments":timeframe_states,
                            "parent_direction":parent_direction,
                            "hourly_higher_high_higher_low_pairs_last5":ups,
                            "hourly_lower_high_lower_low_pairs_last5":downs,
                            "prior_hours_rising":past_up,
                            "upper_stall":pl,
                            "sharp_decline_recovery":shock,
                            "support_resistance":ls,
                            "phase":phase,
                            "directional_bias":bias},
        "session_calendar":session,
        "session_opening_reversals":session_opening_behavior(minute,asof),
        "fundamentals":{"event_calendar":"UNAVAILABLE_UNLESS_VERIFIED_EVENT_FEED_CONNECTED",
                        "usd_rates":"NOT_CONNECTED",
                        "dxy":"NOT_CONNECTED",
                        "order_flow":"NOT_CONNECTED",
                        "cftc":"NOT_CONNECTED"},
        "decision":{"status":"EVALUATED" if price_usable else "BLOCKED_STALE",
           "new_short":"NOT_PROPOSED_NO_VERIFIED_MACRO_CONFIRMATION",
           "long":"NO_AUTOMATIC_ENTRY",
           "first_verified_resistance":first_res if price_usable else None,
           "first_verified_support":first_sup if price_usable else None,
           "prediction_24h":"NOT_CALIBRATED",
           "prediction_72h":"NOT_CALIBRATED",
           "note":"Observed price structure only. No probabilities or catalyst claims without validated inputs."}
    }
    return report


def normalized_hourly_merge(provider, aggregate_hour):
    union={b["t"]:b for b in provider}
    for b in aggregate_hour:
        if b["t"] not in union:
            union[b["t"]]=b
    return [union[k] for k in sorted(union)]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",default="data")
    parser.add_argument("--out",default="data/analysis_latest.json")
    parser.add_argument("--as-of",default=None,
                        help="ISO8601 with offset; for deterministic historical tests")
    parser.add_argument("--check-only",action="store_true")
    args=parser.parse_args()
    asof=datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(JST)
    asof=asof.astimezone(JST)
    minute,hourly,daily,sources,mix,hix=load_market(Path(args.data_dir),asof)
    report=evaluate(minute,hourly,daily,asof,
       sources={"fetched_at":sources["1min"]["fetched_at"]})
    if args.check_only:
        print(json.dumps({"data_quality":report["data_quality"],
                          "market_structure":report["market_structure"]},ensure_ascii=False))
    else:
        out=Path(args.out)
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print("Analysis published:",out,"phase=",report["market_structure"]["phase"],
              "latest=",report["data_quality"]["last_confirmed_1min_start_jst"])


if __name__=="__main__":
    main()
