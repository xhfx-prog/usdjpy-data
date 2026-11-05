"""Exercise the actual existing analyzer with the same JSON shape as the private Worker."""
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"cloudflare-python"))
import analyze_usdjpy as engine
from analysis_core import analyze_payload

class PythonWorkerBridgeTest(unittest.TestCase):
    def prepare(self):
        sources={}
        for interval in ("1min","1h","1day"):
            data=json.loads((ROOT/"data"/f"usdjpy_{interval}.json").read_text(encoding="utf-8"))
            sources[interval]=data["data"]["values"]
        # Supply enough real historical data; newest candle must be fully confirmed.
        latest=sorted(sources["1min"],key=lambda r:r["datetime"])[-1]["datetime"]
        now=engine.stamp(latest)+timedelta(minutes=1, seconds=10)
        return {
          "symbol":"USD/JPY","source":"USDJPY_MCP_FRESH_BUNDLE",
          "fetched_at":now.isoformat(),
          "latest_confirmed_1min_bar":latest,
          **sources
        }, now

    def test_engine_recomputes_same_candle(self):
        payload,now=self.prepare()
        result=analyze_payload(payload,now=now)
        self.assertTrue(result["full_python_engine_recomputed"])
        self.assertEqual(result["analysis_input_latest_confirmed_1min_start_jst"],
                         result["analysis"]["data_quality"]["last_confirmed_1min_start_jst"])
        self.assertIn("timeframe_assessments",result["analysis"]["market_structure"])
        self.assertEqual(result["execution"],"CLOUDFLARE_PYTHON_WORKER_INLINE")

    def test_reject_wrong_source(self):
        p,now=self.prepare()
        p["source"]="untrusted"
        with self.assertRaisesRegex(ValueError,"INVALID_SOURCE"):
            analyze_payload(p,now=now)

    def test_reject_stale_input(self):
        p,now=self.prepare()
        with self.assertRaisesRegex(ValueError,"STALE_1MIN"):
            analyze_payload(p,now=now+timedelta(minutes=5))

if __name__=="__main__": unittest.main()
