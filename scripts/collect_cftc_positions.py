"""Fetch CFTC TFF Futures Only Japanese yen positions."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from position_statistics import position, weekly_statistics

API = "https://publicreportinghub.cftc.gov/resource/gpe5-46if.json"
OUT = Path("data/cftc_usdjpy.json")
SUMMARY = Path("data/cftc_positions_latest.json")
GROUPS = ("dealer", "asset_mgr", "lev_money", "other_rept", "nonrept")
JST = ZoneInfo("Asia/Tokyo")


def normalize(raw):
    day = raw["report_date_as_yyyy_mm_dd"][:10]
    oi = int(raw["open_interest_all"])
    if oi <= 0 or raw["cftc_contract_market_code"] != "097741":
        raise ValueError("Invalid contract or market open interest")
    groups = {}
    for group in GROUPS:
        suffix = "_all" if group in ("dealer", "nonrept") else ""
        yen_long = int(raw[f"{group}_positions_long{suffix}"])
        yen_short = int(raw[f"{group}_positions_short{suffix}"])
        spread = None if group == "nonrept" else int(raw[f"{group}_positions_spread{suffix}"])
        if spread is not None and not 0 <= spread <= oi:
            raise ValueError("Invalid spreading position")
        groups[group] = {**position(day, yen_short, yen_long, oi),
                         "yen_long": yen_long, "yen_short": yen_short,
                         "yen_net": yen_long - yen_short, "spread": spread,
                         "long_share": yen_short / oi, "short_share": yen_long / oi}
    spread_total = sum(item["spread"] or 0 for item in groups.values())
    if any(sum(item[field] for item in groups.values()) + spread_total != oi
           for field in ("long", "short")):
        raise ValueError("Official open interest does not reconcile")
    return {"date": day, "open_interest": oi, "groups": groups}


def summary(rows, as_of):
    eligible = {}
    for row in rows:
        if datetime.fromisoformat(row["first_observed_at_utc"]) <= as_of:
            eligible[row["date"]] = row
    if not eligible:
        return {"status": "NOT_OBSERVED_AS_OF"}
    latest = eligible[max(eligible)]
    return {"latest": latest, "groups": {
        group: weekly_statistics([row["groups"][group] for row in eligible.values()])
        for group in GROUPS}}


def write(path, value):
    staged = path.with_suffix(".tmp")
    staged.write_text(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")
    staged.replace(path)


def main():
    now = datetime.now(timezone.utc)
    previous = json.loads(OUT.read_text())["history"] if OUT.exists() else []
    last_by_date = {row["date"]: row for row in previous}
    rows = list(previous)
    params = urlencode({"cftc_contract_market_code": "097741", "$limit": 1000,
                        "$order": "report_date_as_yyyy_mm_dd ASC"})
    error = None
    try:
        with urlopen(f"{API}?{params}", timeout=30) as response:
            received = [normalize(raw) for raw in json.load(response)]
        days = {row["date"] for row in received}
        if len(received) < 157 or len(days) != len(received):
            raise ValueError("CFTC response is incomplete or contains duplicate dates")
        if not set(last_by_date).issubset(days):
            raise ValueError("CFTC response omitted previously verified dates")
        observed = datetime.now(timezone.utc).isoformat()
        for row in received:
            prior = last_by_date.get(row["date"])
            if prior is None or any(row[key] != prior[key] for key in row):
                rows.append({**row, "first_observed_at_utc": observed,
                             "official_published_at": None})
        write(OUT, {"source": API, "history": rows})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        error = str(exc)
        rows = previous
    now = datetime.now(timezone.utc)
    local = now.astimezone(JST)
    windows = {}
    for offset in (0, 1):
        for hour in (6, 9):
            cutoff = (local - timedelta(days=offset)).replace(hour=hour, minute=0, second=0, microsecond=0)
            if cutoff <= now:
                windows[cutoff.isoformat()] = summary(rows, cutoff)
    status = "SOURCE_VERIFIED" if error is None else "SCHEDULED_FALLBACK" if rows else "UNAVAILABLE"
    write(SUMMARY, {"source": API, "status": status, "checked_at_utc": now.isoformat(),
                    "error": error, "current": summary(rows, now), "windows": windows})
    print(status, len({row["date"] for row in rows}), error or "")
    return int(error is not None)


if __name__ == "__main__":
    raise SystemExit(main())
