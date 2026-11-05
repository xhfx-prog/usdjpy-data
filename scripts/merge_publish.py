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
         "last_run_utc", "generated_at_utc", "recorded_at_utc")
        if obj.get(k)), ""))


def combine_bars(old, new):
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
    limit = max(len(old_rows), len(new_rows))
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
            result = combine_bars(old, new)
            if result is None:
                # For manifests and reports, keep the newest complete snapshot.
                # If neither has a comparable timestamp, leave remote untouched.
                result = new if freshness(new) and freshness(new) > freshness(old) else old
        dest.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
        if not dest.exists() or dest.read_text(encoding="utf-8") != encoded:
            dest.write_text(encoded, encoding="utf-8")


if __name__ == "__main__":
    publish(Path(sys.argv[1]), Path(sys.argv[2]))
