import copy
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts import collect_tfx as tfx


class TestTFXCollection(unittest.TestCase):
    def setUp(self):
        self.trade = date(2026, 11, 26)
        self.published = date(2026, 11, 27)
        self.checked = "2026-11-27T00:00:00+00:00"
        self.url = "https://www.tfx.co.jp/kawase/document/PRT-010-CSV-003-20261126.CSV"
        self.csv = ("H00,PRT-010-CSV-003,20261127,20261126,0,070508\n"
                    "D01,20261126,1,USD/JPY,米ドル・日本円,159.175,159.190,075500,159.345,010323,"
                    "158.940,100036,159.300,065440,159.280,0.105,142.000,9034,353770\n").encode("cp932")

    def test_daily_published_dates_are_taken_from_exchange_index(self):
        sample = (f'<tr><td><a href="{self.url.removeprefix("https://www.tfx.co.jp")}">11月26日</a></td>'
                  '<td>更新日：2026/11/27</td></tr>').encode()
        self.assertEqual(tfx.parse_daily_index(sample), {self.trade: (self.url, self.published)})
        row = tfx.parse_daily(self.csv, self.trade, self.url, self.published)
        self.assertEqual((row["volume"], row["open_interest"]), (9034, 353770))
        self.assertEqual(row["report_created_at_jst"], "2026-11-27T07:05:08+09:00")
        with self.assertRaises(ValueError):
            tfx.parse_daily(self.csv + self.csv.splitlines(keepends=True)[1], self.trade, self.url, self.published)

    def test_fallback_retains_last_good_row_and_recovers(self):
        row = tfx.parse_daily(self.csv, self.trade, self.url, self.published)
        expected = [self.trade.isoformat()]
        good = tfx.available_snapshot({}, [(row, self.checked)], expected, self.checked, [])
        failure_at = "2026-11-27T01:00:00+00:00"
        failed = tfx.available_snapshot(good, [], expected, failure_at, ["HTTP 503"])
        self.assertEqual(failed["latest"], good["latest"])
        self.assertEqual(failed["last_success_at_utc"], self.checked)
        self.assertEqual(failed["status"], "SCHEDULED_FALLBACK")
        missing = tfx.available_snapshot(good, [], expected + ["2026-11-27"], failure_at, [])
        self.assertEqual(missing["missing_source_trade_dates"], ["2026-11-27"])
        older = tfx.available_snapshot(good, [], ["2026-11-25"], failure_at, [])
        self.assertEqual(older["status"], "SCHEDULED_FALLBACK")
        recovered = tfx.available_snapshot(failed, [(row, failure_at)], expected, failure_at, [])
        self.assertEqual(recovered["status"], "SOURCE_VERIFIED")
        self.assertEqual(recovered["latest"]["first_observed_at_utc"], self.checked)

    def test_duplicate_json_keys_and_duplicate_history_dates_fail_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "broken.json"
            path.write_text('{"daily": {}, "daily": null}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
                tfx.load_json(path)
        row = tfx.parse_daily(self.csv, self.trade, self.url, self.published)
        good = tfx.available_snapshot({}, [(row, self.checked)], [self.trade.isoformat()], self.checked, [])
        weekly = tfx.available_snapshot({}, [], [], self.checked, ["publication unavailable"])
        snapshot = {"daily": good, "weekly": weekly, "schema_version": 1, "market": "TFX Click 365",
                    "symbol": "USD/JPY", "unit": "contracts", "contract_size_usd": 10000}
        tfx.verify_snapshot(snapshot)
        bad = copy.deepcopy(snapshot)
        bad["daily"]["history"].append(good["latest"])
        with self.assertRaisesRegex(ValueError, "Inconsistent daily"):
            tfx.verify_snapshot(bad)
