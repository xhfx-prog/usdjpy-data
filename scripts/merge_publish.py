#!/usr/bin/env python3
"""Safely publish generated market-data snapshots onto the latest main branch.

Run after fetching origin/main and resetting the workspace to origin/main.
The first argument is a directory containing the job's generated data/ tree;
the second is the reset repository's data/ tree.
"""
import json
import shutil
import sys
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def freshness(obj):
    if not isinstance(obj, dict):
        return ""
    return str(next((obj[k] for k in
        ("fetched_at", "updated_at", "audited_at_utc",
         "last_run_utc", "generated_at_utc", "recorded_at_utc",
         "retrieved_at_utc", "checked_at_utc", "tested_at_utc", "at_utc")
        if obj.get(k)), ""))


def combine_bars(old, new, preserve_all=False):
    """Deduplicate OHLC by datetime, preferring the newer snapshot on overlap."""
    old_data = old.get("data", {})
    new_data = new.get("data", {})
    old_rows, new_rows = old_data.get("values"), new_data.get("values")
    if not isinstance(old_rows, list) or not isinstance(new_rows, list):
        return None
    if not all(isinstance(x, dict) and "datetime" in x for x in old_rows + new_rows):
        return None
    # An API that returned a stale quote must never replace a fresher bar.
    new_is_later = freshness(new) >= freshness(old)
    base, overlay = (old_rows, new_rows) if new_is_later else (new_rows, old_rows)
    merged = {x["datetime"]: x for x in base}
    merged.update({x["datetime"]: x for x in overlay})
    winner = new if new_is_later else old
    result = json.loads(json.dumps(winner))
    # Retain the larger source window without creating unlimited growth.
    limit = len(merged) if preserve_all else max(len(old_rows), len(new_rows))
    result["data"]["values"] = [
        merged[k] for k in sorted(merged, reverse=True)[:limit]
    ]
    return result


def combine_status(old, new):
    entries = {}
    for obj in (old, new):
        for entry in obj.get("recent_runs", []):
            key = (str(entry.get("run_id")), str(entry.get("run_attempt")))
            if key not in entries or str(entry.get("recorded_at_utc", "")) > str(
                entries[key].get("recorded_at_utc", "")):
                entries[key] = entry
        entry = obj.get("latest")
        if isinstance(entry, dict):
            key = (str(entry.get("run_id")), str(entry.get("run_attempt")))
            entries[key] = entry
    history = sorted(entries.values(), key=lambda x: str(x.get("recorded_at_utc", "")))
    return {"latest": history[-1], "recent_runs": history[-100:]} if history else old


def publish(snapshot, target):
    if not snapshot.is_dir():
        raise ValueError("Missing generated data snapshot")
    for source in sorted(snapshot.rglob("*.json")):
        rel = source.relative_to(snapshot)
        dest = target / rel
        new = load(source)
        if new is None:
            raise ValueError(f"Invalid JSON in generated file: {rel}")
        old = load(dest)
        if old is None:
            result = new
        elif rel.as_posix() == "workflow_status.json":
            result = combine_status(old, new)
        else:
            result = combine_bars(old, new, preserve_all=rel.parts[0] in ('history_1min','history_1h','history_1day'))
            if result is None:
                # For manifests and reports, keep the newest complete snapshot.
                # If neither has a comparable timestamp, leave remote untouched.
                result = new if freshness(new) and freshness(new) > freshness(old) else old
        dest.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
        if not dest.exists() or dest.read_text(encoding="utf-8") != encoded:
            dest.write_text(encoded, encoding="utf-8")



def reconcile_archives(target):
    """Rebuild indexes from merged partitions without resurrecting expired bars."""
    for interval, days, field in (("1min",10,"date_jst"),("1h",370,"month_jst"),("1day",3653,"year_exchange")):
        root=target/("history_"+interval)
        index=target/("history_"+interval+"_index.json")
        if not root.exists() or not index.exists():
            continue
        prior=load(index)
        if not isinstance(prior,dict) or not prior.get("updated_at"):
            raise ValueError("Missing archive index timestamp: "+str(index))
        asof=datetime.fromisoformat(prior["updated_at"].replace("Z","+00:00"))
        cutoff=(asof.astimezone(ZoneInfo("Asia/Tokyo")).date()-timedelta(days=days)).isoformat()
        entries=[]
        for path in sorted(root.glob("*.json"),reverse=True):
            obj=load(path)
            if not isinstance(obj,dict) or not isinstance(obj.get("data",{}).get("values"),list):
                raise ValueError("Invalid archive partition "+str(path))
            rows=obj["data"]["values"]
            kept=[b for b in rows if b["datetime"][:10]>=cutoff]
            if not kept:
                path.unlink()
                continue
            if kept!=rows:
                obj["data"]["values"]=kept
                path.write_text(json.dumps(obj,ensure_ascii=False,separators=(",",":"))+"\n")
            entries.append({field:path.stem,"path":"data/"+str(path.relative_to(target)),"count":len(kept),"first":kept[-1]["datetime"],"last":kept[0]["datetime"]})
        prior.update({"sessions" if interval=="1min" else "partitions":entries,
                      "actual_oldest":entries[-1]["first"] if entries else None,
                      "actual_newest":entries[0]["last"] if entries else None,
                      "total_bars":sum(e["count"] for e in entries)})
        index.write_text(json.dumps(prior,ensure_ascii=False,separators=(",",":"))+"\n")


if __name__ == "__main__":
    snapshot,target=Path(sys.argv[1]),Path(sys.argv[2])
    publish(snapshot,target)
    if any((snapshot/("history_"+i+"_index.json")).exists() for i in ("1min","1h","1day")):
        reconcile_archives(target)
