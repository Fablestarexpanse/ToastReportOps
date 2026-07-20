"""Draft approval workflow via the API layer (transition guard + approver fields)."""

import pytest
from fastapi import HTTPException

from toast.api import routes
from toast.core.db import utcnow


def _draft(conn, draft_id="d1", status="draft"):
    conn.execute(
        "INSERT INTO drafts (id, incident_id, title, body_md, status, created_at, updated_at)"
        " VALUES (?, 'i1', 't', 'body', ?, ?, ?)",
        (draft_id, status, utcnow(), utcnow()),
    )


class Req:
    def __init__(self, conn):
        self.app = type("A", (), {"state": type("S", (), {"conn": conn})()})()


def test_full_approval_workflow(seeded):
    _draft(seeded)
    req = Req(seeded)
    assert (
        routes.transition_draft(req, "d1", {"status": "needs_review"})["status"] == "needs_review"
    )
    assert routes.transition_draft(req, "d1", {"status": "approved"})["status"] == "approved"
    row = seeded.execute(
        "SELECT status, approved_by, approved_at FROM drafts WHERE id='d1'"
    ).fetchone()
    assert row["status"] == "approved" and row["approved_by"] == "operator"
    assert row["approved_at"] is not None


def test_illegal_transition_rejected(seeded):
    _draft(seeded)
    req = Req(seeded)
    with pytest.raises(HTTPException) as exc:
        routes.transition_draft(req, "d1", {"status": "approved"})  # skips needs_review
    assert exc.value.status_code == 409


def test_publish_blocked_until_channels_configured(seeded):
    _draft(seeded, status="approved")
    seeded.execute(
        "UPDATE drafts SET approved_by='operator', approved_at=? WHERE id='d1'", (utcnow(),)
    )
    req = Req(seeded)
    with pytest.raises(HTTPException) as exc:
        routes.transition_draft(req, "d1", {"status": "published"})
    assert exc.value.status_code == 501  # publishing deferred


def test_edit_blocked_after_publish(seeded):
    _draft(seeded, status="published")
    req = Req(seeded)
    with pytest.raises(HTTPException) as exc:
        routes.edit_draft(req, "d1", {"body_md": "tampered"})
    assert exc.value.status_code == 409
