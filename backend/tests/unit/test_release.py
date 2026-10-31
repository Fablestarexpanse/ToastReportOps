"""Channel-selected, scheduled release.

The invariants: only selected channels get publications; a scheduled
publication does not move until its time; the enqueued flag makes the sweep
idempotent; a queued/sent channel is never doubled; and retry works for
'failed' only — 'unknown' stays a human decision, here as in the job layer.
"""

import pytest

from toast.core.ids import ulid
from toast.pipeline import release

NOW = "2026-07-19T00:00:00.000Z"


class FakePub:
    def __init__(self, channel):
        self.channel = channel


@pytest.fixture(autouse=True)
def three_channels(monkeypatch):
    monkeypatch.setattr(
        release, "release_draft", release.release_draft
    )  # anchor for clarity; real patch below
    import toast.publishers as pubs

    fakes = [FakePub("bluesky"), FakePub("x"), FakePub("facebook")]
    monkeypatch.setattr(pubs, "configured_publishers", lambda s=None: fakes)


@pytest.fixture
def approved_draft(seeded):
    draft_id = ulid()
    seeded.execute(
        "INSERT INTO drafts (id, kind, body_md, status, approved_by, approved_at,"
        " created_at, updated_at)"
        " VALUES (?, 'social', 'Fire update.', 'draft', 'brian', ?, ?, ?)",
        (draft_id, NOW, NOW, NOW),
    )
    for state in ("needs_review", "approved"):
        seeded.execute("UPDATE drafts SET status = ? WHERE id = ?", (state, draft_id))
    return draft_id


def _jobs(conn):
    return conn.execute(
        "SELECT payload FROM jobs WHERE stage = 'publish' AND state = 'queued'"
    ).fetchall()


def test_only_selected_channels_release(seeded, approved_draft):
    out = release.release_draft(seeded, approved_draft, ["bluesky", "x"])
    assert {c["channel"] for c in out["created"]} == {"bluesky", "x"}
    assert out["enqueued_now"] == 2
    pubs = seeded.execute("SELECT channel FROM publications").fetchall()
    assert {p["channel"] for p in pubs} == {"bluesky", "x"}  # facebook untouched
    assert (
        seeded.execute("SELECT status FROM drafts WHERE id = ?", (approved_draft,)).fetchone()[0]
        == "published"
    )


def test_scheduled_release_waits_for_its_time(seeded, approved_draft):
    out = release.release_draft(seeded, approved_draft, ["bluesky"], at="2099-01-01T08:00:00.000Z")
    assert out["enqueued_now"] == 0
    assert not _jobs(seeded)  # nothing goes out early
    row = seeded.execute("SELECT * FROM publications").fetchone()
    assert row["status"] == "queued" and row["enqueued"] == 0
    assert row["not_before"] == "2099-01-01T08:00:00.000Z"

    # the sweep is idempotent and time-gated
    assert release.release_due(seeded) == 0
    seeded.execute("UPDATE publications SET not_before = '2020-01-01T00:00:00.000Z'")
    seeded.commit()
    assert release.release_due(seeded) == 1
    assert release.release_due(seeded) == 0  # exactly one job, ever
    assert len(_jobs(seeded)) == 1


def test_an_active_channel_is_never_doubled(seeded, approved_draft):
    release.release_draft(seeded, approved_draft, ["bluesky"])
    out = release.release_draft(seeded, approved_draft, ["bluesky", "facebook"])
    assert [c["channel"] for c in out["created"]] == ["facebook"]
    assert out["skipped_already_active"] == ["bluesky"]


def test_unconfigured_channel_refused(seeded, approved_draft):
    with pytest.raises(ValueError, match="not configured"):
        release.release_draft(seeded, approved_draft, ["telegram"])


def test_unapproved_draft_refused(seeded):
    draft_id = ulid()
    seeded.execute(
        "INSERT INTO drafts (id, kind, body_md, status, created_at, updated_at)"
        " VALUES (?, 'social', 'x', 'draft', ?, ?)",
        (draft_id, NOW, NOW),
    )
    with pytest.raises(ValueError, match="approve it first"):
        release.release_draft(seeded, draft_id, ["bluesky"])


class TestRetry:
    def _pub(self, seeded, approved_draft, status):
        release.release_draft(seeded, approved_draft, ["bluesky"])
        pub_id = seeded.execute("SELECT id FROM publications").fetchone()["id"]
        seeded.execute("UPDATE publications SET status = ? WHERE id = ?", (status, pub_id))
        seeded.execute("DELETE FROM jobs")
        seeded.commit()
        return pub_id

    def test_failed_retries_cleanly(self, seeded, approved_draft):
        pub_id = self._pub(seeded, approved_draft, "failed")
        out = release.retry_publication(seeded, pub_id)
        assert out["state"] == "queued"
        assert len(_jobs(seeded)) == 1

    def test_unknown_is_refused_the_duplicate_post_guard(self, seeded, approved_draft):
        pub_id = self._pub(seeded, approved_draft, "unknown")
        with pytest.raises(ValueError, match="check the account"):
            release.retry_publication(seeded, pub_id)
        assert not _jobs(seeded)


class Req:
    def __init__(self, conn):
        self.app = type("A", (), {"state": type("S", (), {"conn": conn})()})()


class TestUpcoming:
    def _req(self, conn):
        return Req(conn)

    def test_upcoming_lists_only_future_scheduled(self, seeded, approved_draft):
        from toast.api import routes

        release.release_draft(seeded, approved_draft, ["bluesky"], at="2099-01-01T08:00:00.000Z")
        out = routes.upcoming_publications(self._req(seeded))
        assert out["count"] == 1
        assert out["upcoming"][0]["channel"] == "bluesky"
        pub_id = out["upcoming"][0]["id"]

        routes.cancel_scheduled(self._req(seeded), pub_id)
        assert routes.upcoming_publications(self._req(seeded))["count"] == 0

    def test_a_dispatched_release_cannot_be_cancelled(self, seeded, approved_draft):
        import pytest as _pytest
        from fastapi import HTTPException

        from toast.api import routes

        release.release_draft(seeded, approved_draft, ["bluesky"])  # immediate: enqueued
        pub_id = seeded.execute("SELECT id FROM publications").fetchone()["id"]
        with _pytest.raises(HTTPException):
            routes.cancel_scheduled(self._req(seeded), pub_id)
