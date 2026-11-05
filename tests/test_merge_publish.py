import json
import tempfile
import unittest
from pathlib import Path
from scripts.merge_publish import combine_bars, combine_status, publish


def bars(ts, stamps):
    return {
        "source": "Twelve Data", "interval": "1min",
        "fetched_at": ts,
        "data": {"status": "ok", "values": [
            {"datetime": t, "open": "1", "high": "2",
             "low": "1", "close": c} for t, c in stamps
        ]},
    }


class PublicationTests(unittest.TestCase):
    def test_newer_remote_candle_not_overwritten(self):
        remote = bars("2026-11-05T18:20:00+00:00",
                      [("2026-11-06 03:19:00", "1.9"),
                       ("2026-11-06 03:18:00", "1.8")])
        generated = bars("2026-11-05T18:15:00+00:00",
                         [("2026-11-06 03:18:00", "1.7"),
                          ("2026-11-06 03:17:00", "1.6")])
        merged = combine_bars(remote, generated)
        self.assertEqual(merged["fetched_at"], remote["fetched_at"])
        self.assertEqual(merged["data"]["values"][0]["datetime"],
                         "2026-11-06 03:19:00")
        self.assertEqual(merged["data"]["values"][1]["close"], "1.8")

    def test_new_generated_candle_wins_if_fresher(self):
        remote = bars("2026-11-05T18:15:00+00:00",
                      [("2026-11-06 03:18:00", "1.7")])
        generated = bars("2026-11-05T18:20:00+00:00",
                         [("2026-11-06 03:19:00", "1.9"),
                          ("2026-11-06 03:18:00", "1.8")])
        merged = combine_bars(remote, generated)
        self.assertEqual(merged["data"]["values"][1]["close"], "1.8")

    def test_workflow_history_preserves_both_runs(self):
        a = {"latest": {"run_id": "1", "run_attempt": "1",
                        "recorded_at_utc": "2026-11-05T18:15:00+00:00"},
             "recent_runs": []}
        b = {"latest": {"run_id": "2", "run_attempt": "1",
                        "recorded_at_utc": "2026-11-05T18:20:00+00:00"},
             "recent_runs": []}
        merged = combine_status(a, b)
        self.assertEqual(merged["latest"]["run_id"], "2")
        self.assertEqual(len(merged["recent_runs"]), 2)

    def test_publish_preserves_newer_remote_snapshot(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            snapshot, target = root / "snapshot", root / "target"
            snapshot.mkdir()
            target.mkdir()
            old = bars("2026-11-05T18:15:00+00:00",
                       [("2026-11-06 03:18:00", "1.7")])
            new = bars("2026-11-05T18:20:00+00:00",
                       [("2026-11-06 03:19:00", "1.9")])
            (snapshot / "usdjpy_1min.json").write_text(json.dumps(old))
            (target / "usdjpy_1min.json").write_text(json.dumps(new))
            publish(snapshot, target)
            out = json.loads((target / "usdjpy_1min.json").read_text())
            self.assertEqual(out["fetched_at"], new["fetched_at"])
            self.assertEqual(out["data"]["values"][0]["datetime"],
                             "2026-11-06 03:19:00")


if __name__ == "__main__":
    unittest.main()
