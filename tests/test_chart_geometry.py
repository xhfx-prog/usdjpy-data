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
