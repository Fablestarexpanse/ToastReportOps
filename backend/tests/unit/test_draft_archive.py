"""Draft archival + guarded deletion.

Archive is orthogonal to status (the state machine never notices); delete is
allowed only for drafts with no public footprint — a published draft or one
with delivery records archives instead, because the record of what went out is
not deletable.
"""

import pytest
from fastapi import HTTPException

from toast.api import routes
from toast.core.ids import ulid

NOW = "2026-07-19T00:00:00.000Z"


class Req:
    def __init__(self, conn):
        self.app = type("A", (), {"state": type("S", (), {"conn": conn})()})()


def _draft(db, status="draft"):
    did = ulid()
    db.execute(
        "INSERT INTO drafts (id, kind, body_md, status, approved_by, approved_at,"
        " created_at, updated_at)"
        " VALUES (?, 'update', 'x', 'draft', 'brian', ?, ?, ?)",
        (did, NOW, NOW, NOW),
    )
    order = ["needs_review", "approved", "published"]
    for s in order[: order.index(status) + 1] if status in order else []:
        db.execute("UPDATE drafts SET status = ? WHERE id = ?", (s, did))
    return did


def _ids(req, **kw):
    return {d["id"] for d in routes.list_drafts(req, **kw)["drafts"]}


def test_archived_leaves_the_queue_and_shows_in_the_archive_view(seeded):
    req = Req(seeded)
    did = _draft(seeded)
    assert did in _ids(req)
    routes.archive_draft(req, did, {"archived": True})
    assert did not in _ids(req)
    assert did in _ids(req, archived=True)
    # and back
    routes.archive_draft(req, did, {"archived": False})
    assert did in _ids(req)


def test_a_published_draft_archives_but_never_deletes(seeded):
    req = Req(seeded)
    did = _draft(seeded, status="published")
    with pytest.raises(HTTPException) as e:
        routes.delete_draft(req, did)
    assert "archive instead" in str(e.value.detail)
    routes.archive_draft(req, did, {"archived": True})  # this path is fine
    assert did in _ids(req, archived=True)


def test_a_draft_with_delivery_records_cannot_be_deleted(seeded):
    req = Req(seeded)
    did = _draft(seeded, status="draft")
    seeded.execute(
        "INSERT INTO publications (id, draft_id, channel, status) VALUES (?, ?, 'x', 'failed')",
        (ulid(), did),
    )
    with pytest.raises(HTTPException):
        routes.delete_draft(req, did)


def test_a_never_published_draft_deletes_cleanly(seeded):
    req = Req(seeded)
    did = _draft(seeded)
    routes.delete_draft(req, did)
    assert did not in _ids(req)
    assert (
        seeded.execute("SELECT COUNT(*) FROM drafts WHERE id = ?", (did,)).fetchone()[0] == 0
    )
