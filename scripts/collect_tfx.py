"""Collect public TFX Click 365 USD/JPY daily and weekly statistics."""
import csv
import io
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DAILY = "https://www.tfx.co.jp/historical/fx/"
WEEKLY = "https://www.click365.jp/resorces/doc/weekly_sellbuy.xls"
OUT = Path("data/tfx_usdjpy.json")
SERIES = {"daily": ("volume", "open_interest"), "weekly": ("sell_open_interest", "buy_open_interest")}
DAILY_REPORT = r"/kawase/document/PRT-010-CSV-003-(\d{8})\.CSV"


def load_json(path):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"Duplicate JSON key {key!r}")
            obj[key] = value
        return obj

    def invalid_constant(value):
        raise ValueError(f"Invalid JSON constant {value}")

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=invalid_constant)


def fetch(url):
    request = Request(url, headers={"User-Agent": "USDJPY market data collector"})
    for attempt in range(2):
        try:
            with urlopen(request, timeout=12) as response:
                body = response.read(4_000_001)
                if not body or len(body) > 4_000_000:
                    raise ValueError("Empty or oversized TFX response")
                return body, response.headers.get("Last-Modified")
        except OSError as error:
            if attempt or getattr(error, "code", None) in (401, 403, 404):
                raise
            time.sleep(1)


def now_utc():
    return datetime.now(timezone.utc)


def parse_daily_index(body):
    text = body.decode("utf-8")
    reports = {}
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, re.S | re.I):
        link = re.search(rf'href="({DAILY_REPORT})"', row, re.I)
        if not link:
            continue
        published = re.search(r"更新日[：:]\s*(\d{4}/\d{2}/\d{2})", row)
        if not published:
            raise ValueError("TFX CSV link missing its publication date")
        trade = datetime.strptime(link[2], "%Y%m%d").date()
        day = datetime.strptime(published[1], "%Y/%m/%d").date()
        if trade in reports or day <= trade:
            raise ValueError("Invalid or duplicate TFX daily index entry")
        reports[trade] = (urljoin(DAILY, link[1]), day)
    if not reports:
        raise ValueError("No TFX daily CSV reports in index")
    return reports


def daily_catalogue(body):
    match = re.search(r"historicalPullDownDownload\((\{[^\n]+?\}),\s*'#csv_download_year'",
                      body.decode("utf-8"))
    if not match:
        raise ValueError("Missing TFX historical CSV catalogue")
    dates = {}
    for year, months in json.loads(match[1]).items():
        for month, days in months.items():
            for day, filename in days.items():
                trade = date(int(year), int(month), int(day))
                if filename != f"PRT-010-CSV-003-{trade:%Y%m%d}.CSV":
                    raise ValueError(f"Invalid TFX catalogue report for {trade}")
                dates[trade] = urljoin(DAILY, "/kawase/document/" + filename)
    if not dates:
        raise ValueError("Empty TFX historical CSV catalogue")
    return dates


def parse_daily(body, trade, source, publication_date):
    trade_label = trade.strftime("%Y%m%d")
    raw = list(csv.reader(io.StringIO(body.decode("cp932"))))
    if not raw or len(raw[0]) != 6 or raw[0][:2] != ["H00", "PRT-010-CSV-003"] or raw[0][3] != trade_label:
        raise ValueError("Invalid TFX daily header or trade date")
    matches = [row for row in raw if row[:4] == ["D01", trade_label, "1", "USD/JPY"]]
    if len(matches) != 1 or len(matches[0]) != 19:
        raise ValueError("Invalid or duplicate standard USD/JPY daily row")
    values = list(map(int, matches[0][-2:]))
    if min(values) < 0:
        raise ValueError("Negative TFX daily quantity")
    created = datetime.strptime(raw[0][2] + raw[0][5], "%Y%m%d%H%M%S").replace(tzinfo=JST)
    if not trade < created.date() or (publication_date and created.date() > publication_date):
        raise ValueError("Invalid TFX daily report creation date")
    return {"trade_date": trade.isoformat(), "source": source,
            "report_created_at_jst": created.isoformat(),
            "publication_date_jst": publication_date.isoformat() if publication_date else None,
            "volume": values[0], "open_interest": values[1]}


def parse_weekly(body, modified):
    import xlrd
    book = xlrd.open_workbook(file_contents=body)
    sheet = book.sheet_by_name("w_sellbuy")
    columns = [i for i, cell in enumerate(sheet.row_values(0)) if cell == "USD/JPY"]
    if len(columns) != 1:
        raise ValueError("Invalid standard USD/JPY column in TFX weekly XLS")
    col = columns[0]
    if sheet.row_values(2)[col:col + 2] != ["sell", "buy"]:
        raise ValueError("Unexpected sell/buy column ordering")
    modified_at = parsedate_to_datetime(modified) if modified else None
    publication_date = modified_at.astimezone(JST).date() if modified_at else None
    rows = {}
    for index in range(3, sheet.nrows):
        day = sheet.cell(index, 0)
        cells = sheet.row_values(index)[col:col + 2]
        if day.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK) and cells == ["", ""]:
            continue
        trade = (xlrd.xldate_as_datetime(day.value, book.datemode).date()
                 if day.ctype == xlrd.XL_CELL_DATE else datetime.strptime(day.value, "%m/%d/%Y").date())
        if (trade in rows or (publication_date and trade >= publication_date)
                or not all(isinstance(n, (int, float)) and n >= 0 and n % 1 == 0 for n in cells)):
            raise ValueError(f"Invalid or duplicate TFX weekly row {trade}")
        rows[trade] = {"trade_date": trade.isoformat(), "source": WEEKLY,
                       "sell_open_interest": int(cells[0]), "buy_open_interest": int(cells[1])}
    dates = sorted(rows)
    if len(dates) < 4 or any(not 4 <= (b - a).days <= 10 for a, b in zip(dates, dates[1:])):
        raise ValueError("Incomplete or irregular TFX weekly USD/JPY history")
    rows[dates[-1]]["publication_date_jst"] = publication_date.isoformat() if publication_date else None
    if modified_at:
        rows[dates[-1]]["source_last_modified_at_utc"] = modified_at.isoformat()
    return rows


def available_snapshot(old, rows, expected, checked_at, errors, coverage=None):
    history = {r["trade_date"]: r for r in old.get("history", [])}
    for row, observed in rows:
        key = row["trade_date"]
        previous = history.get(key, {})
        first_seen = previous.get("first_observed_at_utc", observed)
        if any(previous.get(field) != row[field] for fields in SERIES.values()
               for field in fields if field in row):
            first_seen = observed
        history[key] = {**previous, **row, "first_observed_at_utc": first_seen}
    items = [history[key] for key in sorted(history)]
    latest = items[-1] if items else None
    missing = sorted(set(expected) - history.keys())
    # A cached older source response must never displace the newest verified row.
    if expected and latest and latest["trade_date"] > max(expected):
        errors.append("TFX source returned a date older than the saved latest row")
    status = "SOURCE_VERIFIED" if not errors and not missing else (
        "SCHEDULED_FALLBACK" if latest else "UNAVAILABLE")
    return {"status": status, "checked_at_utc": checked_at,
            "last_success_at_utc": checked_at if status == "SOURCE_VERIFIED" else old.get("last_success_at_utc"),
            "source_trade_dates": expected, "missing_source_trade_dates": missing,
            "errors": errors, "latest": latest, "history": items,
            "actual_oldest": items[0]["trade_date"] if items else None,
            "actual_newest": latest["trade_date"] if latest else None, "total_records": len(items),
            "official_catalogue": coverage or old.get("official_catalogue")}


def collect(previous):
    # Independent feeds so a corrupt XLS cannot suppress daily data.
    from concurrent.futures import ThreadPoolExecutor

    def daily():
        index = fetch(DAILY)[0]
        reports = parse_daily_index(index)
        catalogue = {**daily_catalogue(index), **{day: entry[0] for day, entry in reports.items()}}
        published_days = sorted(catalogue)
        oldest_to_keep = published_days[-min(20, len(published_days))]
        saved = {date.fromisoformat(row["trade_date"]) for row in previous.get("daily", {}).get("history", [])}
        if saved:
            oldest_to_keep = min(oldest_to_keep, min(saved))
        expected = [day for day in published_days if day >= oldest_to_keep]
        expected = sorted(set(expected) | {date.fromisoformat(day) for day in
                          previous.get("daily", {}).get("missing_source_trade_dates", [])})

        def retrieve(trade):
            source, publication = reports.get(trade, (catalogue.get(trade), None))
            if not source:
                raise ValueError("Previously advertised report missing from catalogue")
            body, modified = fetch(source)
            row = parse_daily(body, trade, source, publication)
            if modified:
                row["source_last_modified_at_utc"] = parsedate_to_datetime(modified).isoformat()
            return row, now_utc().isoformat()

        rows, errors = [], []
        # Fill older advertised holes as well as rechecking the five latest
        # exchange listings. Limit catch-up work per run after a long outage.
        missing = [day for day in expected if day not in saved and day not in reports]
        with ThreadPoolExecutor(max_workers=3) as downloads:
            pending = {trade: downloads.submit(retrieve, trade) for trade in
                       sorted(set(reports) | set(missing[:20]))}
            for trade, future in pending.items():
                try:
                    rows.append(future.result())
                except (OSError, ValueError, csv.Error) as error:
                    errors.append(f"{trade}: {error}")
        coverage = {"oldest": published_days[0].isoformat(), "newest": published_days[-1].isoformat(),
                    "reports": len(published_days)}
        return rows, [day.isoformat() for day in expected], errors, coverage

    def weekly():
        from xlrd.biffh import XLRDError
        body, modified = fetch(WEEKLY)
        try:
            rows = parse_weekly(body, modified)
        except (XLRDError, TypeError, OverflowError, re.error, IndexError) as error:
            raise ValueError(f"Invalid TFX workbook: {error}") from error
        observed = now_utc().isoformat()
        dates = [day.isoformat() for day in sorted(rows)]
        return [(rows[day], observed) for day in sorted(rows)], dates, [], {
            "oldest": dates[0], "newest": dates[-1], "reports": len(dates)}

    data = {"schema_version": 1, "market": "TFX Click 365", "symbol": "USD/JPY",
            "unit": "contracts", "contract_size_usd": 10000}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {name: executor.submit(handler) for name, handler in (("daily", daily), ("weekly", weekly))}
        for name, future in futures.items():
            old = previous.get(name, {})
            try:
                rows, dates, errors, coverage = future.result()
            except (OSError, ValueError) as error:
                rows, dates, errors, coverage = [], old.get("source_trade_dates", []), [str(error)], None
            data[name] = available_snapshot(old, rows, dates, now_utc().isoformat(), errors, coverage)
    return data


def verify_snapshot(data):
    if not (data["schema_version"] == 1 and data["symbol"] == "USD/JPY"
            and data["market"] == "TFX Click 365" and data["unit"] == "contracts"
            and data["contract_size_usd"] == 10000):
        raise ValueError("Invalid TFX snapshot metadata")
    for name, fields in SERIES.items():
        feed = data[name]
        checked = datetime.fromisoformat(feed["checked_at_utc"])
        history = feed["history"]
        if not isinstance(history, list):
            raise ValueError(f"Invalid {name} history")
        dates = [row["trade_date"] for row in history]
        advertised = feed["source_trade_dates"]
        latest = feed["latest"]
        if (dates != sorted(set(dates)) or advertised != sorted(set(advertised))
                or feed["total_records"] != len(history)
                or latest != (history[-1] if history else None)
                or feed["actual_oldest"] != (dates[0] if dates else None)
                or feed["actual_newest"] != (dates[-1] if dates else None)
                or feed["missing_source_trade_dates"] != sorted(set(advertised) - set(dates))
                or feed["status"] not in ("SOURCE_VERIFIED", "SCHEDULED_FALLBACK", "UNAVAILABLE")
                or (feed["status"] == "UNAVAILABLE") != (not history)
                or (feed["status"] == "SOURCE_VERIFIED" and (
                    not advertised or feed["errors"] or feed["missing_source_trade_dates"]
                    or advertised[-1] != dates[-1] or feed["last_success_at_utc"] != feed["checked_at_utc"]))):
            raise ValueError(f"Inconsistent {name} TFX history, source dates, or status")
        if feed["last_success_at_utc"] and datetime.fromisoformat(feed["last_success_at_utc"]) > checked:
            raise ValueError(f"Inconsistent {name} last success time")
        for day in advertised:
            date.fromisoformat(day)
        for row in history:
            trade = date.fromisoformat(row["trade_date"])
            observed = datetime.fromisoformat(row["first_observed_at_utc"])
            published = row.get("publication_date_jst")
            expected_url = (urljoin(DAILY, f"/kawase/document/PRT-010-CSV-003-{trade:%Y%m%d}.CSV")
                            if name == "daily" else WEEKLY)
            if (observed > checked or trade >= observed.astimezone(JST).date() or row["source"] != expected_url
                    or any(type(row[field]) is not int or row[field] < 0 for field in fields)
                    or (published and not trade < date.fromisoformat(published) <= observed.astimezone(JST).date())):
                raise ValueError(f"Invalid {name} row {trade}")
            if name == "daily":
                created = datetime.fromisoformat(row["report_created_at_jst"])
                if not trade < created.date() or (published and created.date() > date.fromisoformat(published)):
                    raise ValueError(f"Invalid daily creation date for {trade}")


def main():
    old = load_json(OUT) if OUT.exists() else {}
    if old:
        verify_snapshot(old)
    result = collect(old)
    verify_snapshot(result)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=OUT.parent, delete=False) as stream:
        staged = Path(stream.name)
        try:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(staged, OUT)
        finally:
            staged.unlink(missing_ok=True)
    for name in SERIES:
        feed = result[name]
        print(name, feed["status"], feed["actual_oldest"], feed["actual_newest"],
              feed["total_records"], feed["latest"], feed["errors"])


if __name__ == "__main__":
    main()
