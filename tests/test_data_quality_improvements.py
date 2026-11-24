import json
import unittest
from datetime import datetime, timezone
from scripts.audit_data_quality import audit_bars
from scripts.merge_publish import freshness
from scripts.collect_fx_intraday import analyze
from economic_calendar import collect_calendar
from investigate_world_news import deduplicate

class QualityTests(unittest.TestCase):
    def test_invalid_and_duplicate_bars(self):
        row={"datetime":"2026-11-24 12:00:00","open":159,"high":160,"low":158,"close":159}
        obj={"data":{"values":[row,dict(row,close=161)]}}
        q=audit_bars(obj,"1min")
        self.assertEqual(q["duplicates"],1)
        self.assertEqual(q["invalid"],1)
        self.assertFalse(q["ok"])

    def test_new_timestamp_fields(self):
        for k in ("retrieved_at_utc","checked_at_utc","tested_at_utc","at_utc"):
            self.assertEqual(freshness({k:"2026-11-24T12:00:00+00:00"}),"2026-11-24T12:00:00+00:00")

    def test_distinct_news_ids_and_unicode(self):
        def article(title,url):
            return {"title":title,"url":url,"source_timestamp_utc":"2026-11-24T12:00:00+00:00","publisher":"test"}
        rows=[article("記事一","https://example.com/story?id=1&utm_source=x"),
              article("記事一","https://example.com/story?id=1&utm_source=y"),
              article("記事二","https://example.com/story?id=2"),
              article("円高","https://news.google.com/a"),
              article("円安","https://news.google.com/b")]
        self.assertEqual(len(deduplicate(rows)),4)

    def test_zero_calendar_actual(self):
        when="2026-11-24T12:00:00+00:00"
        row={"date":when,"country":"USD","title":"Test","forecast":0,"previous":0,"actual":0}
        fetch=lambda _:json.dumps([row]).encode()
        before=collect_calendar(datetime.fromisoformat("2026-11-24T11:00:00+00:00"),fetcher=fetch)
        after=collect_calendar(datetime.fromisoformat("2026-11-24T13:00:00+00:00"),previous=before,fetcher=fetch)
        e=after["events"][0]
        self.assertEqual(e["result_status"],"VENDOR_ACTUAL")
        self.assertEqual(e["vendor_actual"],0)
        self.assertEqual(e["surprise"]["difference"],"0")
        self.assertFalse(e["official_actual_verified"])

    def test_cross_market_alignment(self):
        t=1800000000
        def series(a,b):
            return {"bars":{str(t+i*300):{"close":a+(b-a)*i/6,"volume":10} for i in range(7)}}
        r=analyze({"usd_jpy":series(159,158.7),"dxy":series(100,99),"us10y":series(4,4.1),"yen_future":series(0.006,0.0061)})
        self.assertEqual(len(r),1)
        self.assertEqual(r[0]["directional_crosschecks_not_causality"],{"dxy":"ALIGNED","us10y":"OPPOSED","yen_future":"ALIGNED"})

if __name__=="__main__":
    unittest.main()
