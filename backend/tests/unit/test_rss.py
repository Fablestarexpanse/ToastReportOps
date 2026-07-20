"""RSS adapter → article envelopes, and the normalize→extract routing."""

import json

from toast.core.envelope import insert_envelope
from toast.sources.rss import RssAdapter

SAMPLE_FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>NorCal Fire News</title>
  <item>
    <title>Flume Fire grows to 1,240 acres</title>
    <link>https://example.com/flume-1</link>
    <guid>flume-1</guid>
    <description>Evacuations expand near Paradise.</description>
    <pubDate>Sat, 19 Jul 2026 22:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Red Flag Warning issued</title>
    <link>https://example.com/redflag</link>
    <guid>redflag</guid>
    <pubDate>Sat, 19 Jul 2026 20:00:00 GMT</pubDate>
  </item>
</channel></rss>"""


class FakeCapture(RssAdapter):
    def fetch_captured(self, url, *, record_url=True, **kwargs):
        self.fetches.append((SAMPLE_FEED, "application/rss+xml", url))
        return SAMPLE_FEED, "application/rss+xml"


def test_rss_parses_entries_to_article_envelopes():
    adapter = FakeCapture()
    envs, _ = adapter.poll({"source_id": "s1", "feed_url": "https://example.com/feed"}, None)
    assert len(envs) == 2
    e = envs[0]
    assert e.kind == "article"
    assert e.external_id == "flume-1"
    assert e.payload["url"] == "https://example.com/flume-1"
    assert e.payload["title"] == "Flume Fire grows to 1,240 acres"
    assert e.payload["published_at"] == "2026-07-19T22:00:00.000Z"


def test_keyword_filter_drops_non_matching_entries():
    # a keyword that matches neither sample entry filters everything; the
    # default fire set (keywords="fire") keeps both fire-related items.
    adapter = FakeCapture()
    envs, _ = adapter.poll({"source_id": "s1", "feed_url": "u", "keywords": ["earthquake"]}, None)
    assert len(envs) == 0
    adapter = FakeCapture()
    envs, _ = adapter.poll({"source_id": "s1", "feed_url": "u", "keywords": "fire"}, None)
    assert len(envs) == 2


def test_article_envelope_routes_to_extract_stage(seeded):
    from toast.pipeline.handlers import normalize_handler
    from toast.queue import jobs

    env_id = insert_envelope(
        seeded,
        __import__("toast.core.envelope", fromlist=["Envelope"]).Envelope(
            source_id="s1",
            external_id="a1",
            kind="article",
            payload={"url": "https://example.com/flume-1", "title": "x"},
        ),
    )
    normalize_handler(seeded, {"envelope_id": env_id})
    # an 'extract' job was enqueued for the article (no facts written yet)
    row = seeded.execute("SELECT stage, payload FROM jobs WHERE stage='extract'").fetchone()
    assert row is not None
    assert json.loads(row["payload"])["envelope_id"] == env_id
    assert seeded.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"] == 0
    assert jobs.counts_by_state(seeded).get("queued", 0) >= 1
