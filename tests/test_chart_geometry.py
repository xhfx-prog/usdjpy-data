import unittest
from datetime import datetime,timedelta
import analyze_usdjpy as a

def bars_from(highs,lows,closes,mins=240):
    t=datetime(2026,10,20)
    z=[]
    for i,(h,l,c) in enumerate(zip(highs,lows,closes)):
        z.append({"t":(t+timedelta(minutes=mins*i)).strftime("%Y-%m-%d %H:%M:%S"),
                  "o":(h+l)/2,"h":h,"l":l,"c":c})
    return z

class ChartGeometryTests(unittest.TestCase):
    def test_converging_candle_family_overrides_local_direction(self):
        hi=[];lo=[];cl=[]
        # Repeated shrinking high-low oscillations in 4h OHLC.
        for j in range(6):
            for k in range(4):
                ceiling=160-.16*j
                floor=157+.16*j
                span=ceiling-floor
                center=floor+span*(.15 if k==0 else .85 if k==2 else .52)
                hi.append(center+.09 if k not in (2,) else ceiling)
                lo.append(center-.09 if k not in (0,) else floor)
                cl.append(center)
        data=bars_from(hi,lo,cl)
        shape=a.chart_geometry(data,"4h")
        self.assertIn(shape["pattern"],("CONVERGING_RANGE_CANDIDATE","BROAD_RANGE_CANDIDATE"))
        self.assertEqual(shape["structure"],"CONSOLIDATION")
        state=a.describe_timeframe(data,"4h")["state"]
        self.assertIsInstance(state,str)

    def test_lone_wick_not_confirmed_break(self):
        hi=[];lo=[];cl=[]
        for j in range(6):
            for k in range(4):
                ceiling=160-.16*j
                floor=157+.16*j
                center=floor+(ceiling-floor)*(.15 if k==0 else .85 if k==2 else .52)
                hi.append(center+.09 if k!=2 else ceiling)
                lo.append(center-.09 if k!=0 else floor)
                cl.append(center)
        data=bars_from(hi,lo,cl)
        data[-1]["l"]=data[-1]["l"]-.7
        self.assertNotEqual(a.chart_geometry(data,"4h")["structure"],"BREAKDOWN_CONFIRMED")

    def test_all_six_timeframes_have_auditable_windows(self):
        configs={"1day":60,"4h":60,"1h":144,"15min":192,
                 "5min":240,"1min":360}
        for name,span in configs.items():
            with self.subTest(frame=name):
                size=span+28
                highs=[157.5+0.1*((i//8)%3)+0.03 for i in range(size)]
                lows=[x-.1 for x in highs]
                closes=[(h+l)/2 for h,l in zip(highs,lows)]
                step={"1day":1440,"4h":240,"1h":60,"15min":15,
                      "5min":5,"1min":1}[name]
                rows=bars_from(highs,lows,closes,step)
                shape=a.chart_geometry(rows,name)
                w=shape["analysis_window"]
                self.assertEqual(w["configured_candles"],span)
                self.assertEqual(w["actual_completed_candles"],span)
                self.assertEqual(w["window_from_jst"],rows[-span]["t"])
                self.assertEqual(w["window_to_jst"],rows[-1]["t"])
                self.assertTrue(w["strict_future_data_exclusion"])
                self.assertIn("judgement_evidence",shape)
                self.assertIn("chart_observations",shape)
                self.assertTrue(len(shape["chart_observations"]["confirmed_swing_highs"])>0)

    def test_each_frame_state_retains_separate_recent_direction(self):
        names={"1day":60,"4h":60,"1h":144,"15min":192,
               "5min":240,"1min":360}
        inputs={}
        for name,span in names.items():
            size=span+32
            hi=[157.6+.08*((i//6)%3)+.03 for i in range(size)]
            lo=[x-.12 for x in hi]
            cl=[(h+l)/2 for h,l in zip(hi,lo)]
            mins={"1day":1440,"4h":240,"1h":60,
                  "15min":15,"5min":5,"1min":1}[name]
            inputs[name]=bars_from(hi,lo,cl,mins)
        frames,parent=a.multi_timeframe_structure(
            inputs["1day"],inputs["4h"],inputs["1h"],
            inputs["15min"],inputs["5min"],inputs["1min"])
        self.assertEqual(set(frames),set(names))
        for name in names:
            self.assertIn("recent_candle_direction",frames[name])
            self.assertIn("analysis_window",frames[name]["chart_geometry"])
            self.assertIn("state",frames[name])
        self.assertIn(parent,("UP","DOWN","RANGE","UNRESOLVED"))

    def test_five_minute_rebound_detected(self):
        highs=[158.4-0.01*i for i in range(14)]
        lows=[x-.06 for x in highs]
        closes=[x-.03 for x in highs]
        lows[13]=157.71
        closes[13]=157.76
        highs.extend([157.89,157.94,157.93])
        lows.extend([157.75,157.83,157.84])
        closes.extend([157.87,157.90,157.89])
        out=a.lower_timeframe_recovery(bars_from(highs,lows,closes,5))
        self.assertEqual(out["status"],"REBOUND_IN_PROGRESS")
        self.assertGreater(out["recovery_fraction"],0.25)
if __name__=="__main__":
    unittest.main()
