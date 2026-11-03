"""Regression and safety-gate tests for USDJPY deterministic analysis."""
import json
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import analyze_usdjpy as engine

JST = ZoneInfo("Asia/Tokyo")
FIXTURE = Path("tests/fixtures/nov03_2026.json")


class StructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def calculate(self, when):
        asof = datetime.fromisoformat(when).astimezone(JST)
        obj = self.fixture
        m = engine.normalized(obj["minute"], asof, "1min")
        h = engine.normalized(obj["hourly"], asof, "1h")
        d = engine.normalized(obj["daily"], asof, "1day")
        return engine.evaluate(m, h, d, asof), m

    def test_nov_03_15830_stall_detected_before_shock(self):
        report, m = self.calculate("2026-11-03T21:30:30+09:00")
        plateau = report["market_structure"]["upper_stall"]
        self.assertIsNotNone(plateau)
        self.assertGreaterEqual(plateau["near_ceiling_bars"], 12)
        self.assertGreaterEqual(plateau["duration_minutes"], 18)
        self.assertAlmostEqual(plateau["ceiling"], 158.307, delta=.015)
        self.assertFalse(plateau["breakout_confirmed_after"])
        self.assertNotEqual(report["market_structure"]["directional_bias"], "UP_CANDIDATE")
        self.assertTrue(report["data_quality"]["usable_for_current_trading"])

    def test_nov_03_shock_and_recovery_not_forced_bear(self):
        report, m = self.calculate("2026-11-03T23:04:00+09:00")
        r = report["market_structure"]["sharp_decline_recovery"]
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r["trough"], 157.96416, delta=.03)
        self.assertGreater(r["drop_pips"], 20)
        self.assertGreater(r["recovery_fraction"], .55)
        self.assertNotEqual(report["market_structure"]["directional_bias"], "UP_CANDIDATE")
        self.assertIn("UNVALIDATED", report["session_calendar"]["session_effect_probabilities"])

    def test_sr_zones_never_chain_into_broad_unusable_bands(self):
        report, _ = self.calculate("2026-11-03T23:04:00+09:00")
        both = report["market_structure"]["support_resistance"]
        for direction in ("support", "resistance"):
            for item in both[direction]:
                self.assertLessEqual(item["high"]-item["low"], 0.0651,
                                     "S/R band incorrectly merged unrelated price structures")

    def test_older_than_ten_minutes_blocks_current_decision(self):
        report, _ = self.calculate("2026-11-03T23:20:00+09:00")
        self.assertFalse(report["data_quality"]["usable_for_current_trading"])
        self.assertEqual(report["decision"]["status"], "BLOCKED_STALE")
        self.assertIsNone(report["decision"]["first_verified_support"])
        self.assertEqual(report["market_structure"]["directional_bias"], "NO_CURRENT_DIRECTION")

    def test_ny_0830_initial_drop_rejected_by_2303(self):
        report, _ = self.calculate("2026-11-03T23:04:00+09:00")
        signals = report["session_opening_reversals"]
        ny = next(x for x in signals if x["window"] == "NEW_YORK_0830")
        self.assertEqual(ny["first_direction"], "down")
        self.assertEqual(ny["interpretation"], "INITIAL_DOWN_REJECTED")
        self.assertEqual(ny["causality"], "NOT_ESTABLISHED_FROM_OHLC")

    def test_5minute_aggregation_discards_partial_bars(self):
        obj=self.fixture
        asof=datetime.fromisoformat("2026-11-03T23:03:20+09:00")
        m=engine.normalized(obj["minute"],asof,"1min")
        agg=engine.aggregate(m,5)
        self.assertEqual(agg[-1]["t"], "2026-11-03 22:55:00")

    def test_invalid_ohlc_rejected(self):
        a=datetime.fromisoformat("2026-11-03T23:05:00+09:00")
        rows=[{"datetime":"2026-11-03 22:55:00","open":"158.2","high":"158.1",
               "low":"158.3","close":"158.2"}]
        with self.assertRaises(ValueError):
            engine.normalized(rows,a,"1min")

    def test_session_timezone_is_dst_sensitive(self):
        date=datetime.fromisoformat("2026-11-03T22:30:00+09:00")
        obj=self.fixture
        m=engine.normalized(obj["minute"],datetime.fromisoformat("2026-11-03T23:04:00+09:00"),"1min")
        s=engine.session_info(date,m)
        self.assertIn("08:30:00-05:00",s["observed_sessions"]["new_york"]["local_time"])
        self.assertIn("US_0830_WINDOW_VERIFY_OFFICIAL_RELEASE",s["time_flags"])
        spring=datetime.fromisoformat("2026-07-03T21:30:00+09:00")
        s2=engine.session_info(spring,[])
        self.assertIn("-04:00",s2["observed_sessions"]["new_york"]["local_time"])


if __name__=="__main__":
    unittest.main()
