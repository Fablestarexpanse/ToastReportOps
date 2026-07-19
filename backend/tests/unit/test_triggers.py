"""The DB-level guarantees ARE the product: append-only ledger, frozen-incident
immutability, and the draft approval state machine live in triggers, not app code."""

import sqlite3

import pytest

from tests.conftest import NOW, insert_fact


class TestFactsAppendOnly:
    def test_delete_rejected(self, seeded):
        insert_fact(seeded, "f1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            seeded.execute("DELETE FROM facts WHERE id='f1'")

    def test_content_edit_rejected(self, seeded):
        insert_fact(seeded, "f1")
        with pytest.raises(sqlite3.IntegrityError, match="supersession"):
            seeded.execute("UPDATE facts SET content='tampered' WHERE id='f1'")

    def test_reassigning_attributed_fact_rejected(self, seeded):
        insert_fact(seeded, "f1")
        with pytest.raises(sqlite3.IntegrityError):
            seeded.execute("UPDATE facts SET incident_id=NULL WHERE id='f1'")

    def test_timestamps_immutable(self, seeded):
        insert_fact(seeded, "f1")
        with pytest.raises(sqlite3.IntegrityError):
            seeded.execute("UPDATE facts SET observed_at='1999-01-01T00:00:00Z' WHERE id='f1'")

    def test_adjudication_null_to_incident_allowed(self, seeded):
        insert_fact(seeded, "f2", incident_id=None)
        seeded.execute("UPDATE facts SET incident_id='i1' WHERE id='f2'")
        row = seeded.execute("SELECT incident_id FROM facts WHERE id='f2'").fetchone()
        assert row["incident_id"] == "i1"

    def test_star_toggle_allowed(self, seeded):
        insert_fact(seeded, "f1")
        seeded.execute("UPDATE facts SET starred=1 WHERE id='f1'")
        seeded.execute("UPDATE facts SET starred=0 WHERE id='f1'")

    def test_supersession_chain(self, seeded):
        insert_fact(seeded, "f1")
        insert_fact(seeded, "f2", supersedes="f1")
        row = seeded.execute("SELECT supersedes FROM facts WHERE id='f2'").fetchone()
        assert row["supersedes"] == "f1"


class TestFrozenIncidents:
    def _freeze(self, conn):
        conn.execute("UPDATE incidents SET status='frozen' WHERE id='i1'")

    def test_new_fact_rejected(self, seeded):
        self._freeze(seeded)
        with pytest.raises(sqlite3.IntegrityError, match="frozen"):
            insert_fact(seeded, "f1")

    def test_supersession_correction_allowed(self, seeded):
        insert_fact(seeded, "f1")
        self._freeze(seeded)
        insert_fact(seeded, "f2", supersedes="f1")


class TestDraftStateMachine:
    def _draft(self, conn, draft_id="d1"):
        conn.execute(
            "INSERT INTO drafts (id, body_md, created_at, updated_at) VALUES (?, 'x', ?, ?)",
            (draft_id, NOW, NOW),
        )

    def test_draft_cannot_jump_to_published(self, seeded):
        self._draft(seeded)
        with pytest.raises(sqlite3.IntegrityError, match="illegal"):
            seeded.execute("UPDATE drafts SET status='published' WHERE id='d1'")

    def test_publish_requires_approval_fields(self, seeded):
        self._draft(seeded)
        seeded.execute("UPDATE drafts SET status='needs_review' WHERE id='d1'")
        seeded.execute("UPDATE drafts SET status='approved' WHERE id='d1'")
        with pytest.raises(sqlite3.IntegrityError, match="illegal"):
            seeded.execute("UPDATE drafts SET status='published' WHERE id='d1'")

    def test_full_legal_path(self, seeded):
        self._draft(seeded)
        seeded.execute("UPDATE drafts SET status='needs_review' WHERE id='d1'")
        seeded.execute("UPDATE drafts SET status='approved' WHERE id='d1'")
        seeded.execute("UPDATE drafts SET approved_by='brian', approved_at=? WHERE id='d1'", (NOW,))
        seeded.execute("UPDATE drafts SET status='published' WHERE id='d1'")

    def test_published_is_terminal(self, seeded):
        self.test_full_legal_path(seeded)
        for target in ("draft", "needs_review", "approved", "rejected"):
            with pytest.raises(sqlite3.IntegrityError):
                seeded.execute("UPDATE drafts SET status=? WHERE id='d1'", (target,))
