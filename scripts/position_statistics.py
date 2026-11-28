"""Position statistics in USD/JPY direction, with calendar-week comparisons."""
from datetime import date, timedelta


PATTERNS = {
    (1, -1): "USD_BUY_SHIFT",
    (-1, 1): "USD_SELL_SHIFT",
    (1, 1): "BOTH_EXPANDING",
    (-1, -1): "BOTH_CONTRACTING",
    (1, 0): "USD_LONG_INCREASE",
    (0, -1): "USD_SHORT_DECREASE",
    (-1, 0): "USD_LONG_DECREASE",
    (0, 1): "USD_SHORT_INCREASE",
    (0, 0): "UNCHANGED",
}


def sign(value):
    return (value > 0) - (value < 0)


def position(day, long, short, denominator):
    if any(type(value) is not int or value < 0 for value in (long, short, denominator)):
        raise ValueError("Invalid position quantity")
    if max(long, short) > denominator:
        raise ValueError("Position exceeds denominator")
    net = long - short
    return {"date": day, "long": long, "short": short, "net": net,
            "net_ratio": net / denominator if denominator else None}


def distribution(current, values):
    if current is None or not values:
        return {"samples": len(values), "percentile": None, "min": None, "max": None}
    below = sum(value < current for value in values)
    equal = sum(value == current for value in values)
    return {"samples": len(values), "percentile": 100 * (below + equal / 2) / len(values),
            "min": min(values), "max": max(values)}


def weekly_statistics(rows):
    observations = {}
    for row in rows:
        day = date.fromisoformat(row["date"])
        monday = day - timedelta(days=day.weekday())
        if monday in observations:
            raise ValueError(f"Duplicate observation week: {monday}")
        observations[monday] = row
    if not observations:
        return {"status": "UNAVAILABLE"}
    week = max(observations)
    current = observations[week]
    changes = {}
    for weeks in (1, 4, 13, 52):
        target = week - timedelta(weeks=weeks)
        previous = observations.get(target)
        if previous is None:
            changes[weeks] = {"target_week": target.isoformat(), "status": "MISSING"}
            continue
        long = current["long"] - previous["long"]
        short = current["short"] - previous["short"]
        changes[weeks] = {
            "date": previous["date"], "long": long, "short": short, "net": long - short,
            "long_pct": 100 * long / previous["long"] if previous["long"] else None,
            "short_pct": 100 * short / previous["short"] if previous["short"] else None,
            "pattern": PATTERNS[sign(long), sign(short)],
        }
    latest = date.fromisoformat(current["date"])
    try:
        start = latest.replace(year=latest.year - 3)
    except ValueError:
        start = latest.replace(year=latest.year - 3, day=28)
    sample = [row for row in observations.values() if start <= date.fromisoformat(row["date"]) <= latest]
    streaks = {}
    for field in ("long", "short", "net"):
        step = timedelta(weeks=1)
        cursor = week
        direction = 0
        count = 0
        while cursor - step in observations:
            delta = sign(observations[cursor][field] - observations[cursor - step][field])
            if not delta or (direction and delta != direction):
                break
            direction = delta
            count += 1
            cursor -= step
        streaks[field] = count * direction
    return {"latest": current, "changes": changes, "streaks": streaks,
            "three_year_start": start.isoformat(),
            "three_year": {field: distribution(current[field], [
                row[field] for row in sample if row[field] is not None
            ]) for field in ("net", "net_ratio")}}


def tfx_statistics(snapshot, as_of):
    """Use the first verified observation as the availability timestamp."""
    from datetime import datetime

    def available(name):
        return sorted(
            (row for row in snapshot[name]["history"]
             if datetime.fromisoformat(row["first_observed_at_utc"]) <= as_of),
            key=lambda row: row["trade_date"],
        )

    weekly = weekly_statistics([
        position(row["trade_date"], row["buy_open_interest"], row["sell_open_interest"],
                 row["buy_open_interest"] + row["sell_open_interest"])
        for row in available("weekly")
    ])
    rows = available("daily")
    daily = {}
    for days in (1, 5, 20):
        preceding = rows[-days-1:-1]
        if len(preceding) < days:
            daily[days] = {"status": "INSUFFICIENT_HISTORY", "samples": len(preceding)}
            continue
        latest = rows[-1]
        prior = preceding[0]
        average = sum(row["volume"] for row in preceding) / days
        daily[days] = {
            "date": prior["trade_date"], "latest_date": latest["trade_date"],
            "volume_change": latest["volume"] - prior["volume"],
            "open_interest_change": latest["open_interest"] - prior["open_interest"],
            "preceding_average_volume": average,
            "volume_multiple": latest["volume"] / average if average else None,
            "samples": days,
        }
    return {"weekly": weekly, "daily": daily}
