"""Publishing is the one pipeline stage whose mistakes are visible to the public.

A retry after an ambiguous network error is a duplicate post on an account
people are meant to trust during an emergency. The handler must therefore never
raise — if it did, jobs.fail would requeue it — and must never treat "we did not
hear back" as "it did not go out".
"""

import json

import httpx
import pytest

from toast.core.ids import ulid
from toast.pipeline import handlers
from toast.publishers import PublishError

NOW = "2026-07-19T00:00:00.000Z"


@pytest.fixture
def published_draft(seeded):
    draft_id, pub_id = ulid(), ulid()
    seeded.execute(
        "INSERT INTO drafts (id, body_md, status, approved_by, approved_at, created_at, updated_at)"
        " VALUES (?, 'Evacuation order issued.', 'draft', 'operator', ?, ?, ?)",
        (draft_id, NOW, NOW, NOW),
    )
    for state in ("needs_review", "approved", "published"):
        seeded.execute("UPDATE drafts SET status = ? WHERE id = ?", (state, draft_id))
    seeded.execute(
        "INSERT INTO publications (id, draft_id, channel, status) VALUES (?, ?, 'x', 'queued')",
        (pub_id, draft_id),
    )
    return {"draft_id": draft_id, "channel": "x", "publication_id": pub_id}


def _status(conn, pub_id):
    row = conn.execute(
        "SELECT status, receipt FROM publications WHERE id = ?", (pub_id,)
    ).fetchone()
    return row["status"], json.loads(row["receipt"] or "{}")


def _publisher(monkeypatch, behaviour):
    class FakePublisher:
        def publish(self, text):
            return behaviour()

    monkeypatch.setattr(
        handlers_publishers := __import__("toast.publishers", fromlist=["get_publisher"]),
        "get_publisher",
        lambda channel, settings: FakePublisher(),
    )
    return handlers_publishers


class TestNoDuplicatePosts:
    def test_a_transport_timeout_never_raises(self, seeded, published_draft, monkeypatch):
        """If this raised, jobs.fail would requeue it and X — which may already
        have accepted the post — would receive it again."""
        _publisher(monkeypatch, lambda: (_ for _ in ()).throw(httpx.ReadTimeout("timed out")))
        handlers.publish_handler(seeded, published_draft)  # must not raise
        status, receipt = _status(seeded, published_draft["publication_id"])
        assert status == "unknown"
        assert "check the account" in receipt["note"]

    def test_a_definitive_rejection_is_marked_failed(self, seeded, published_draft, monkeypatch):
        """The publisher got a real answer, so a deliberate re-send is safe."""
        _publisher(monkeypatch, lambda: (_ for _ in ()).throw(PublishError("401 bad token")))
        handlers.publish_handler(seeded, published_draft)
        status, receipt = _status(seeded, published_draft["publication_id"])
        assert status == "failed"
        assert "401" in receipt["error"]

    def test_an_already_resolved_row_is_not_sent_again(self, seeded, published_draft, monkeypatch):
        """requeue_stale can hand the same job to a second worker."""
        sent = []
        _publisher(monkeypatch, lambda: sent.append(1) or {"url": "https://x.com/1"})
        handlers.publish_handler(seeded, published_draft)
        assert _status(seeded, published_draft["publication_id"])[0] == "sent"
        handlers.publish_handler(seeded, published_draft)  # duplicate delivery attempt
        assert len(sent) == 1, "publisher was invoked twice for one publication"

    def test_an_unknown_row_is_never_auto_resent(self, seeded, published_draft, monkeypatch):
        calls = []
        _publisher(monkeypatch, lambda: (_ for _ in ()).throw(httpx.ConnectError("reset")))
        handlers.publish_handler(seeded, published_draft)
        assert _status(seeded, published_draft["publication_id"])[0] == "unknown"

        _publisher(monkeypatch, lambda: calls.append(1) or {"url": "https://x.com/1"})
        handlers.publish_handler(seeded, published_draft)
        assert calls == [], "an indeterminate delivery must not be retried automatically"
        assert _status(seeded, published_draft["publication_id"])[0] == "unknown"

    def test_success_records_the_receipt(self, seeded, published_draft, monkeypatch):
        _publisher(monkeypatch, lambda: {"url": "https://x.com/status/1", "id": "1"})
        handlers.publish_handler(seeded, published_draft)
        status, receipt = _status(seeded, published_draft["publication_id"])
        assert status == "sent"
        assert receipt["url"] == "https://x.com/status/1"
