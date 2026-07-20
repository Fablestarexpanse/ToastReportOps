import json
import sqlite3

import httpx

from toast.core.config import Settings
from toast.pipeline.handlers import publish_handler
from toast.publishers import configured_publishers, split_social_body
from toast.publishers.bluesky import BlueskyPublisher
from toast.publishers.x import oauth1_header

NOW = "2026-07-19T00:00:00.000Z"

SOCIAL_BODY = (
    "The Miners Fire has burned 96 acres and is 50% contained.\n\n"
    "Sources: CAL FIRE, InciWeb\n\n---\n\n**Short version (X/Twitter, 42 chars)**\n\n"
    "Miners Fire: 96 acres, 50% contained."
)


def _bare_settings(**overrides) -> Settings:
    """Settings with all publisher creds empty (ignores host .env)."""
    fields = {
        "bluesky_handle": "",
        "bluesky_app_password": "",
        "x_api_key": "",
        "x_api_secret": "",
        "x_access_token": "",
        "x_access_secret": "",
        "fb_page_id": "",
        "fb_page_token": "",
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def test_split_social_body_with_marker():
    post, short = split_social_body(SOCIAL_BODY)
    assert post.startswith("The Miners Fire")
    assert post.endswith("Sources: CAL FIRE, InciWeb")
    assert short == "Miners Fire: 96 acres, 50% contained."


def test_split_social_body_without_marker_truncates():
    post, short = split_social_body("x" * 400)
    assert post == "x" * 400
    assert len(short) <= 280
    assert short.endswith("…")


def test_channels_gated_on_credentials():
    assert configured_publishers(_bare_settings()) == []
    pubs = configured_publishers(
        _bare_settings(bluesky_handle="toast.bsky.social", bluesky_app_password="pw")
    )
    assert [p.channel for p in pubs] == ["bluesky"]


def test_bluesky_publish_posts_and_builds_url():
    calls = []

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("createSession"):
            return httpx.Response(200, json={"accessJwt": "jwt", "did": "did:plc:abc"})
        body = json.loads(request.content)
        assert body["record"]["text"] == "hello fire watchers"
        return httpx.Response(200, json={"uri": "at://did:plc:abc/app.bsky.feed.post/rkey1"})

    s = _bare_settings(bluesky_handle="toast.bsky.social", bluesky_app_password="pw")
    p = BlueskyPublisher(s)
    p.http = httpx.Client(base_url="https://bsky.social", transport=httpx.MockTransport(responder))
    receipt = p.publish("hello fire watchers")
    assert receipt["url"] == "https://bsky.app/profile/toast.bsky.social/post/rkey1"
    assert calls == [
        "/xrpc/com.atproto.server.createSession",
        "/xrpc/com.atproto.repo.createRecord",
    ]


def test_oauth1_header_is_deterministic_and_complete():
    kwargs = dict(
        consumer_key="ck",
        consumer_secret="cs",
        token="tk",
        token_secret="ts",
        nonce="fixednonce",
        timestamp="1700000000",
    )
    h1 = oauth1_header("POST", "https://api.twitter.com/2/tweets", **kwargs)
    h2 = oauth1_header("POST", "https://api.twitter.com/2/tweets", **kwargs)
    assert h1 == h2
    assert h1.startswith("OAuth ")
    for part in ("oauth_consumer_key", "oauth_signature", "oauth_token", "oauth_nonce"):
        assert part in h1
    # a different secret must change the signature
    h3 = oauth1_header(
        "POST", "https://api.twitter.com/2/tweets", **{**kwargs, "token_secret": "other"}
    )
    assert h3 != h1


def _social_published_draft(db: sqlite3.Connection) -> str:
    db.execute(
        "INSERT INTO drafts (id, incident_id, kind, status, title, body_md, approved_by,"
        " approved_at, created_at, updated_at) VALUES ('d-soc', 'i1', 'social', 'published',"
        " 'SOCIAL — test', ?, 'operator', ?, ?, ?)",
        (SOCIAL_BODY, NOW, NOW, NOW),
    )
    db.execute(
        "INSERT INTO publications (id, draft_id, channel, status)"
        " VALUES ('pub1', 'd-soc', 'bluesky', 'queued')"
    )
    return "d-soc"


class _FakePublisher:
    channel = "bluesky"

    def __init__(self):
        self.texts = []

    def publish(self, text):
        self.texts.append(text)
        return {"url": "https://bsky.app/profile/t/post/1"}


def test_publish_handler_sends_short_text_and_records_receipt(seeded, monkeypatch):
    draft_id = _social_published_draft(seeded)
    fake = _FakePublisher()
    monkeypatch.setattr("toast.publishers.get_publisher", lambda c, settings=None: fake)
    publish_handler(seeded, {"draft_id": draft_id, "channel": "bluesky", "publication_id": "pub1"})
    assert fake.texts == ["Miners Fire: 96 acres, 50% contained."]
    row = seeded.execute("SELECT * FROM publications WHERE id = 'pub1'").fetchone()
    assert row["status"] == "sent"
    assert row["external_url"] == "https://bsky.app/profile/t/post/1"


def test_publish_handler_failure_marks_failed_without_retry(seeded, monkeypatch):
    from toast.publishers import PublishError

    draft_id = _social_published_draft(seeded)

    def boom(channel, settings=None):
        raise PublishError("credentials rejected")

    monkeypatch.setattr("toast.publishers.get_publisher", boom)
    publish_handler(
        seeded, {"draft_id": draft_id, "channel": "bluesky", "publication_id": "pub1"}
    )  # must not raise — no retry, no duplicate-post risk
    row = seeded.execute("SELECT * FROM publications WHERE id = 'pub1'").fetchone()
    assert row["status"] == "failed"
    assert "credentials rejected" in row["receipt"]
