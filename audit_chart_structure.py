#!/usr/bin/env python3
"""Independent OHLC chart audit. Never use candles after each cutoff.

This is a diagnostic, not a trained or validated trading signal. Draws the
actual 4-hour OHLC and enumerates swing points and repeated boundary tests.
"""
import json
import statistics
from datetime import datetime
from pathlib import Path

import analyze_usdjpy as engine

BASE = Path("data")
CUTOFFS = ["2026-11-05T06:00:00+09:00",
           "2026-11-05T14:00:00+09:00",
           "2026-11-06T00:00:00+09:00"]
CHART_START = "2026-10-26 00:00:00"
PIVOT_START = "2026-10-27 00:00:00"

def source_rows(filename, interval, cutoff):
    p = BASE / filename
    o = json.loads(p.read_text(encoding="utf-8"))
    engine.validate_source(o, interval)
    return engine.normalized(o["data"]["values"], cutoff, interval)

def load_hourly(cutoff):
    bars = []
    for p in ("history_1h/2026-10.json", "history_1h/2026-11.json", "usdjpy_1h.json"):
        bars += source_rows(p, "1h", cutoff)
    d = {b["t"]:b for b in bars}
    return [d[k] for k in sorted(d)]

def load_minutes(cutoff):
    bars=[]
    for name in ("history_1min/2026-11-04.json",
                 "history_1min/2026-11-05.json","usdjpy_1min.json"):
        bars += source_rows(name, "1min", cutoff)
    d={b["t"]:b for b in bars}
    return [d[k] for k in sorted(d)]

def swing_points(bars, radius, from_time):
    ans={}
    for field, name in (("h","highs"),("l","lows")):
        pts=engine.pivots(bars,field,radius)
        ans[name]=[{"time":p["t"],"price":round(p["price"],5),
                    "bar_index":next(i for i,v in enumerate(bars) if v["t"]==p["t"])}
                    for p in pts if p["t"]>=from_time]
    return ans

def trendline_candidates(swings, asof, bars, volatility):
    """Only report a validated geometric triangle, not a diagram chosen by eye.

    Fit each pivot family on the same window; at least 3 independent confirmed
    swing points per side, converging slopes, reasonable line residuals and
    latest candle must not decisively breach the extrapolated lower line.
    """
    xs=swings["highs"]; ys=swings["lows"]
    result={"status":"UNCONFIRMED","reason":None,"upper":None,"lower":None}
    if len(xs)<3 or len(ys)<3:
        result["reason"]="FEWER_THAN_THREE_CONFIRMED_PIVOTS_PER_SIDE"
        return result
    # Use the most recent 3-6 pivots for each boundary, but not make a
    # valid claim if the computed straight lines fit the observed points poorly.
    def fit(pts):
        xx=[p["bar_index"] for p in pts]
        yy=[p["price"] for p in pts]
        avgx=statistics.mean(xx); avgy=statistics.mean(yy)
        ss=sum((x-avgx)**2 for x in xx)
        slope=sum((x-avgx)*(y-avgy) for x,y in zip(xx,yy))/ss if ss else 0
        intercept=avgy-slope*avgx
        residuals=[round(y-(intercept+slope*x),5) for x,y in zip(xx,yy)]
        return {"slope_per_4h_bar":round(slope,6),
                "intercept":round(intercept,6),
                "max_abs_residual":round(max(abs(z) for z in residuals),5),
                "points":[{"time":p["time"],"price":p["price"]} for p in pts],
                "projected_at_last_completed_4h":round(intercept+slope*(len(bars)-1),5)}
    # A fixed latest-pivot window is explicit, but should not be taken
    # as a guarantee of correct scale across all volatility regimes.
    top=fit(xs[-min(5,len(xs)):])
    bottom=fit(ys[-min(5,len(ys)):])
    result.update(upper=top,lower=bottom)
    if top["slope_per_4h_bar"]>=0 or bottom["slope_per_4h_bar"]<=0:
        result["reason"]="PIVOT_FAMILIES_NOT_CONVERGING"
    elif max(top["max_abs_residual"],bottom["max_abs_residual"])>max(volatility*0.65,0.08):
        result["reason"]="TOO_MUCH_DISPERSION_FOR_STRAIGHT_BOUNDARY"
    elif top["projected_at_last_completed_4h"]<=bottom["projected_at_last_completed_4h"]:
        result["reason"]="FITTED_LINES_HAVE_CROSSED"
    else:
        result["status"]="TRIANGLE_CANDIDATE"
        result["reason"]="CONVERGING_PIVOTS_PASSED_GEOMETRIC_TEST"
    return result

def render_svg(bars,points,cutoff):
    """Portable candlestick chart; each candle's OHLC represented directly."""
    if not bars: return ""
    w,h=1500,680; lx=92; rx=35; ty=35; by=95
    bottom=h-by; plotw=w-lx-rx; ploth=bottom-ty
    lo=min(b["l"] for b in bars)-0.12
    hi=max(b["h"] for b in bars)+0.12
    y=lambda p:ty+(hi-p)/(hi-lo)*ploth
    dx=plotw/len(bars)
    e=['<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="680" viewBox="0 0 1500 680">',
       '<rect width="1500" height="680" fill="#fcfcfc"/>',
       '<text x="92" y="22" font-size="16" font-family="sans-serif" fill="#111">USD/JPY 4h OHLC, actual completed candles; cutoff '+cutoff[:16]+' JST</text>']
    for i in range(7):
        price=lo+(hi-lo)*i/6
        yy=y(price)
        e.append(f'<path d="M{lx} {yy:.1f}H{w-rx}" stroke="#eee"/>')
        e.append(f'<text x="5" y="{yy+4:.1f}" font-size="13" fill="#333">{price:.3f}</text>')
    for i,b in enumerate(bars):
        x=lx+(i+0.5)*dx
        up=b["c"]>=b["o"]
        color="#157f63" if up else "#d44b48"
        e.append(f'<line x1="{x:.2f}" y1="{y(b["h"]):.2f}" x2="{x:.2f}" y2="{y(b["l"]):.2f}" stroke="{color}" stroke-width="1.3"/>')
        top=min(y(b["c"]),y(b["o"]))
        bot=max(y(b["c"]),y(b["o"]))
        e.append(f'<rect x="{x-min(dx*.30,4):.2f}" y="{top:.2f}" width="{max(1,min(dx*.6,8)):.2f}" height="{max(1,bot-top):.2f}" fill="{color}"/>')
        if i%max(1,len(bars)//9)==0:
            e.append(f'<text x="{x-20:.2f}" y="{bottom+25}" font-size="12" fill="#333">{b["t"][5:13]}</text>')
    for kind,color,sign in (("highs","#bf4b22",-1),("lows","#2b54b4",1)):
        for p in points[kind]:
            if p["bar_index"]>=len(bars):continue
            b=bars[p["bar_index"]]
            x=lx+(p["bar_index"]+0.5)*dx
            yy=y(p["price"])
            e.append(f'<circle cx="{x:.2f}" cy="{yy:.2f}" r="4.2" fill="{color}"/>')
    e.append('<text x="92" y="655" font-size="13" fill="#333">Orange = independently confirmed 4h pivot highs; blue = independently confirmed 4h pivot lows. No prospective pivot at last 2 bars.</text>')
    e.append('</svg>')
    return "\n".join(e)

reports=[]
for timestr in CUTOFFS:
    cutoff=datetime.fromisoformat(timestr)
    h1=load_hourly(cutoff)
    h4=engine.aggregate(h1,240)
    m1=load_minutes(cutoff)
    m5=engine.aggregate(m1,5)
    if not h4:raise RuntimeError("no 4h candles for "+timestr)
    chart4=[b for b in h4 if b["t"]>=CHART_START]
    if len(chart4)<15:raise RuntimeError("4h chart history incomplete")
    p2=swing_points(chart4,2,PIVOT_START)
    p3=swing_points(chart4,3,PIVOT_START)
    atr=engine.atr(h4)
    line=trendline_candidates(p2,cutoff,chart4,atr)
    old=engine.describe_timeframe(h4,"4h")
    recent_1h=engine.describe_timeframe(h1,"1h")
    reports.append({"asof_jst":timestr,"last_4h_start_jst":h4[-1]["t"],
       "number_of_4h_candles":len(chart4),
       "4h_range":{"high":round(max(b["h"] for b in chart4),5),
                   "low":round(min(b["l"] for b in chart4),5)},
       "4h_atr14":round(atr,5),
       "old_engine_4h_state":old["state"],
       "old_engine_1h_state":recent_1h["state"],
       "independent_pivots_radius2":p2,
       "independent_pivots_radius3":p3,
       "triangle_geometry_test":line,
       "latest_5min_confirmed":m5[-1] if m5 else None})
    if timestr.startswith("2026-11-06"):
        Path("data/chart_audit_4h_20261106.svg").write_text(
           render_svg(chart4,p2,timestr),encoding="utf-8")
status={"audit":"STRUCTURE_GEOMETRY_DIAGNOSTIC_NOT_PRODUCTION_SIGNAL",
 "description":"No lookahead: confirmed 4h/hourly and 5min candles only, and pivots require right-hand confirmation.",
 "cutoffs":reports}
Path("data/chart_structure_audit.json").write_text(
 json.dumps(status,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"status":"COMPUTED", "rows":[{"asof":r["asof_jst"],
 "4h":r["old_engine_4h_state"],"1h":r["old_engine_1h_state"],
 "geometry":r["triangle_geometry_test"]["status"],
 "reason":r["triangle_geometry_test"]["reason"],
 "4h_pivots":(len(r["independent_pivots_radius2"]["highs"]),len(r["independent_pivots_radius2"]["lows"]))} for r in reports]},ensure_ascii=False))
