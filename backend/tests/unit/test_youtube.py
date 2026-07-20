"""YouTube channel adapter.

Two things matter here beyond parsing: we do not fetch a path the site's
robots.txt forbids without the operator saying so, and a video never becomes a
claim on its own — extraction reads only the text the uploader published.
"""

import pytest

from toast.sources.youtube import RobotsDisallowed, YoutubeAdapter, feed_url_for

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>CAL FIRE Butte Unit</title>
  <entry>
    <id>yt:video:AAAAAAAAAAA</id>
    <yt:videoId>AAAAAAAAAAA</yt:videoId>
    <title>Sawmill Fire evacuation briefing</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=AAAAAAAAAAA"/>
    <author><name>CAL FIRE Butte Unit</name></author>
    <published>2026-07-19T18:30:00+00:00</published>
    <media:group>
      <media:description>The Sawmill Fire has burned 400 acres near Paradise.</media:description>
      <media:thumbnail url="https://i.ytimg.com/vi/AAAAAAAAAAA/hqdefault.jpg"/>
    </media:group>
  </entry>
  <entry>
    <id>yt:video:BBBBBBBBBBB</id>
    <yt:videoId>BBBBBBBBBBB</yt:videoId>
    <title>Station open house this Saturday</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=BBBBBBBBBBB"/>
    <published>2026-07-18T12:00:00+00:00</published>
    <media:group>
      <media:description>Come meet the crew and tour the engine bay.</media:description>
    </media:group>
  </entry>
</feed>
"""


class FakeAdapter(YoutubeAdapter):
    def fetch_captured(self, url, **kw):
        self.asked = url
        return FEED, "application/atom+xml"


def _poll(**config):
    adapter = FakeAdapter()
    cfg = {"source_id": "s1", "robots_override": True, **config}
    return adapter, adapter.poll(cfg, None)[0]


def test_channel_id_builds_the_public_feed_url():
    assert feed_url_for({"channel_id": "UC123"}).endswith("videos.xml?channel_id=UC123")
    with pytest.raises(ValueError):
        feed_url_for({})


def test_robots_disallow_blocks_the_poll_without_an_operator_override(monkeypatch):
    """youtube.com forbids /feeds/videos.xml. That decision is the operator's."""
    monkeypatch.setattr("toast.sources.youtube.robots_allows", lambda url: False)
    with pytest.raises(RobotsDisallowed):
        FakeAdapter().poll({"source_id": "s1", "channel_id": "UC123"}, None)


def test_override_lets_the_poll_through(monkeypatch):
    monkeypatch.setattr("toast.sources.youtube.robots_allows", lambda url: False)
    _adapter, envelopes = _poll(channel_id="UC123")
    assert envelopes


def test_video_envelope_carries_the_uploader_text_and_never_asks_for_the_page():
    _adapter, envelopes = _poll(channel_id="UC123")
    # the open-house video has no fire keyword and is filtered out
    assert len(envelopes) == 1
    env = envelopes[0]
    assert env.kind == "video"
    assert env.payload["video_id"] == "AAAAAAAAAAA"
    assert env.payload["url"] == "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    assert "400 acres" in env.payload["summary"]
    assert env.payload["channel"] == "CAL FIRE Butte Unit"
    assert env.payload["thumbnail"].endswith("hqdefault.jpg")
    assert env.observed_hint == "2026-07-19T18:30:00.000Z"
    # the contract with the extract stage: title + description only
    assert env.payload["text_only"] is True


def test_a_fire_only_channel_can_switch_the_keyword_filter_off():
    _adapter, envelopes = _poll(channel_id="UC123", keywords=[])
    assert len(envelopes) == 2


def test_extract_stage_does_not_fetch_a_text_only_video(seeded, monkeypatch):
    """The guard that keeps us off the video page — asserted, not assumed."""
    import json

    from toast.core.ids import ulid
    from toast.pipeline import handlers

    def explode(*a, **kw):  # pragma: no cover - must never run
        raise AssertionError("text_only sources must not fetch the page")

    monkeypatch.setattr("toast.pipeline.analyze.fetch_article_text", explode)
    captured = {}

    def fake_extract(conn, **kw):
        captured.update(kw)
        return {"facts": []}

    monkeypatch.setattr("toast.pipeline.analyze.extract_article", fake_extract)

    env_id = ulid()
    seeded.execute(
        "INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,"
        " fetched_at) VALUES (?, 's1', 'yt:video:A', 'video', ?, 'h1', ?)",
        (
            env_id,
            json.dumps(
                {
                    "url": "https://www.youtube.com/watch?v=A",
                    "title": "Sawmill Fire briefing",
                    "summary": "The Sawmill Fire has burned 400 acres near Paradise.",
                    "text_only": True,
                }
            ),
            "2026-07-19T18:30:00.000Z",
        ),
    )
    handlers.extract_handler(seeded, {"envelope_id": env_id})
    assert "400 acres" in captured["text"]
    assert captured["raw_ref"] is None  # no page snapshot, and we claim none
