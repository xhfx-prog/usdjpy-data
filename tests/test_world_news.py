"""Deterministic, offline tests for news-first catalyst discovery."""
import json
import unittest
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import investigate_world_news as s

RSS=b'''<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>US approves Qatar draft for Iran talks</title>
<link>https://example.org/qatar</link>
<pubDate>Tue, 03 Nov 2026 13:24:00 GMT</pubDate><source>News agency</source></item>
<item><title>Israel military aid policy changed</title>
<link>https://example.org/israel</link>
<pubDate>Tue, 03 Nov 2026 13:42:00 GMT</pubDate><source>News agency</source></item>
<item><title>Dollar yen falls amid developments</title>
<link>https://example.org/fx-recap</link>
<pubDate>Tue, 03 Nov 2026 13:42:00 GMT</pubDate><source>Market recap</source></item>
</channel></rss>'''

class WorldNewsTests(unittest.TestCase):
    def setUp(self):
        self.move=s.parse_time("2026-11-03T22:30:00+09:00")
        self.now=datetime.fromisoformat("2026-11-03T14:00:00+00:00")

    def test_timezone_conversion(self):
        self.assertEqual(s.gdelt_time(self.move),"20261103133000")

    def test_broad_news_query_not_fx_recap(self):
        url=s.gdelt_url("geopolitics",self.move,self.now)
        p=parse_qs(urlparse(url).query)
        self.assertIn("Iran",p["query"][0])
        self.assertNotIn("USD",p["query"][0])
        self.assertEqual(p["startdatetime"][0],"20261103133000")
        self.assertEqual(p["enddatetime"][0],"20261103140000")

    def test_gdelt_timestamp_is_indexed_not_event_time(self):
        raw=json.dumps({"articles":[{"title":"Qatar Iran peace proposal","url":"https://example.com/q",
                               "seendate":"20261103T132600Z"}]}).encode()
        row=s.parse_gdelt(raw,"geopolitics",self.now)[0]
        self.assertEqual(row["source_timestamp_utc"],"2026-11-03T13:26:00+00:00")
        self.assertEqual(row["source_timestamp_kind"],"GDELT_INDEXED_AT_NOT_PUBLICATION")

    def test_early_publication_precedes_post_event_commentary(self):
        def fake(url):
            return (json.dumps({"articles":[]}).encode() if "gdeltproject" in url
                    else RSS if "news.google.com" in url else b"<rss><channel /></rss>")
        r=s.collect(self.move,45,20,fetcher=fake,now=self.now)
        self.assertEqual(r["confirmed_catalyst"],None)
        self.assertEqual(r["candidates"][0]["title"],"US approves Qatar draft for Iran talks")
        self.assertTrue(r["candidates"][0]["chronology"].startswith("SOURCE_TIMESTAMP_BEFORE"))
        self.assertTrue(any(c["chronology"].startswith("POST_MOVE") for c in r["candidates"]))
        self.assertTrue(all(not c["trigger_confirmed"] for c in r["candidates"]))

    def test_post_move_publication_not_initial_trigger(self):
        a=s.parse_rss(RSS,"geopolitics",self.now,"GOOGLE_RSS")[1]
        label=s.classify_chronology(a,self.move.astimezone(timezone.utc),
                                    self.move.astimezone(timezone.utc),self.now)
        self.assertEqual(label,"POST_MOVE_CANNOT_BE_TRIGGER_FROM_THIS_TIMESTAMP")

    def test_exact_move_minute_is_ambiguous_not_early(self):
        article=s.make_article("Headline at 22:30",
            "https://example.org/at-move","source",
            self.move.astimezone(timezone.utc),"RSS_PUBDATE_NOT_EVENT_TIME",
            "geopolitics","GOOGLE_RSS",self.now,"en")
        self.assertEqual(s.classify_chronology(
            article,self.move.astimezone(timezone.utc),self.move.astimezone(timezone.utc),
            self.now),"SAME_MINUTE_ORDER_UNKNOWN")

    def test_unknown_time_not_pretend_pre_event(self):
        a=s.make_article("unknown","https://example.com/x","x",None,"UNKNOWN",
                         "geopolitics","GDELT_DOC",self.now,"en")
        self.assertEqual(s.classify_chronology(a,self.move,self.move,self.now),
                         "TIME_UNVERIFIED")

    def test_statement_is_not_an_implemented_policy(self):
        a=s.make_article("Treasury secretary warns on currency volatility",
                         "https://example.org/remarks","Official",
                         self.move.astimezone(timezone.utc),
                         "RSS_PUBDATE_NOT_EVENT_TIME","us_government",
                         "GOOGLE_RSS",self.now,"en")
        self.assertEqual(s.novelty_check(a),"STATEMENT_ONLY_CHECK_NOVELTY")
        self.assertFalse(a["implementation_verified"])
        self.assertEqual(a["novelty_vs_prior_statement"],"NOT_CHECKED")

    def test_actual_action_and_reversal_are_distinct(self):
        self.assertEqual(s.classify_event_stage("Tariffs took effect today"),
                         "REPORTED_ACTION_VERIFY_PRIMARY_SOURCE")
        self.assertEqual(s.classify_event_stage("Tariffs postponed"),
                         "REPORTED_REVERSAL_VERIFY")

    def test_recap_is_explicitly_after_world_news_scan(self):
        def fake(url):
            return (json.dumps({"articles":[]}).encode() if "gdeltproject" in url
                    else RSS if "news.google.com" in url else b"<rss><channel /></rss>")
        result=s.collect(self.move,45,20,fetcher=fake,now=self.now)
        order=result["investigation_order"]
        self.assertEqual(order[0],"VERIFY_MOVE_ONSET_FROM_TIMESTAMPED_OHLC")
        self.assertEqual(order[-1],"ONLY_THEN_CROSSCHECK_FX_RECAP_EXPLANATIONS")
        self.assertEqual(len(result["recap_followup_queries_not_executed"]),2)
        self.assertTrue(all("event_stage_headline_only" in x
                            for x in result["candidates"]))

    def test_unavailable_feeds_cannot_generate_a_cause(self):
        def bad(url):
            raise OSError("source unavailable")
        r=s.collect(self.move,45,0,fetcher=bad,now=self.now)
        self.assertEqual(r["discovery_status"],"NO_VERIFIED_NEWS_CANDIDATES")
        self.assertEqual(r["totals"]["in_window_unique"],0)
        self.assertTrue(all(x["status"]=="error" for x in r["news_sources"]))
        self.assertIsNone(r["confirmed_catalyst"])

if __name__=="__main__":
    unittest.main()
