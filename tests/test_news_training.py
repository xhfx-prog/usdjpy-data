"""Tests for 1y retrospective USD/JPY news priority learner (offline)."""
import unittest
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import train_news_priorities as t

JST=ZoneInfo("Asia/Tokyo")

class TrainTests(unittest.TestCase):
    def test_google_historical_query_explicit_dates(self):
        dt=datetime.fromisoformat("2026-04-06T23:00:00+09:00")
        q=t.google_url('"USD/JPY"',dt-timedelta(hours=2),dt+timedelta(hours=9))
        self.assertIn("after%3A2026-04-06",q)
        self.assertIn("before%3A2026-04-08",q)

    def test_category_specific_not_merely_trump(self):
        self.assertIn("geopolitics_war_peace",t.cats("Qatar and Iran discuss ceasefire"))
        self.assertIn("us_politics_tariffs",t.cats("Trump tariffs on imports"))
        self.assertNotIn("us_inflation",t.cats("Trump tariffs on imports"))
        self.assertEqual(t.cats("Company announces new product and earnings call"),[])

    def test_rss_articles_parsed_by_real_utc_time(self):
        xml=b"""<rss><channel><item><title>Fed holds rates</title>
          <link>https://news.example/1</link>
          <pubDate>Wed, 04 Nov 2026 00:30:00 GMT</pubDate>
          </item></channel></rss>"""
        rows=t.parse_rss(xml)
        self.assertEqual(rows[0]["pub_utc"],"2026-11-04T00:30:00+00:00")

    def test_shock_and_control_selection_is_balanced(self):
        start=datetime.fromisoformat("2026-03-02T00:00:00+09:00")
        bars=[]
        for i in range(125):
            dt=start+timedelta(hours=i)
            bars.append({"t":dt,"range_pips":5+(i%11), "open":158,
                         "close":158.02 if i%2 else 157.98})
        s=t.pick_samples(bars,per_month=2)
        self.assertEqual(len([x for x in s if x["kind"]=="large_move"]),2)
        self.assertEqual(len([x for x in s if x["kind"]=="quiet_control"]),2)
        self.assertTrue(all(x["month"]=="2026-03" for x in s))

    def test_sparse_article_coverage_cannot_enable_model(self):
        rows=[]
        for month in ["2026-01","2026-02","2026-03","2026-04",
                      "2026-05","2026-06","2026-07","2026-08",
                      "2026-09","2026-10"]:
            for kind in ("large_move","quiet_control"):
                rows.append({"month":month,"kind":kind,"news":{
                    "prestart_categories":[],"recap_explanations":[],
                    "sources":[{"status":"ok","articles":0}]}})
        r=t.fit(rows)
        self.assertFalse(r["usable"])
        self.assertEqual(r["reason"],"INSUFFICIENT_HISTORICAL_NEWS_OR_HOLDOUT_COVERAGE")

    def test_news_published_after_move_not_predictive_input(self):
        dt=datetime.fromisoformat("2026-04-06T22:00:00+09:00")
        later=dt+timedelta(hours=2)
        xml=("<rss><channel><item><title>Fed rate cut drives dollar yen</title>"
             "<link>https://news.example/item</link>"
             "<pubDate>"+later.strftime("%a, %d %b %Y %H:%M:%S %z")+
             "</pubDate></item></channel></rss>").encode()
        def fetcher(url):
            return xml
        result=t.analyze_articles({"bar_start_jst":dt.isoformat()},dt+timedelta(hours=12),
                                  fetcher=fetcher,sleep_seconds=0)
        self.assertFalse(result["prestart_categories"])
        self.assertTrue(result["recap_explanations"])

if __name__=="__main__":
    unittest.main()
