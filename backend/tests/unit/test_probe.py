"""Add-source URL probe detection (feed vs youtube vs article)."""

from unittest.mock import patch

from toast.sources.probe import probe_url

FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<title>CAL FIRE News</title><item><link>https://x/1</link></item></channel></rss>"""
HTML = b"<html><head><title>A news article</title></head><body>fire</body></html>"


def test_youtube_detected_without_fetch():
    r = probe_url("https://www.youtube.com/@NorCalFireWatch")
    assert r.detected == "youtube" and r.adapter == "youtube"
    assert r.supported is False  # adapter not built yet


def test_rss_feed_detected():
    with patch("toast.sources.probe.fetch", return_value=(FEED, "application/rss+xml")):
        r = probe_url("https://example.com/feed.xml")
    assert r.detected == "rss" and r.adapter == "rss"
    assert r.config == {"feed_url": "https://example.com/feed.xml"}
    assert r.title == "CAL FIRE News" and r.supported is True


def test_xml_body_sniffed_even_without_content_type():
    with patch("toast.sources.probe.fetch", return_value=(FEED, "text/plain")):
        r = probe_url("https://example.com/feed")
    assert r.detected == "rss"


def test_plain_article_page_flagged_unsupported():
    with patch("toast.sources.probe.fetch", return_value=(HTML, "text/html")):
        r = probe_url("https://example.com/story")
    assert r.detected == "article" and r.supported is False
    assert r.warnings  # tells operator to provide a feed URL
