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

    # The original trigger only checked id/content/observed_at/ingested_at, so
    # any other column could ride along with a legal change. These are the
    # statements that used to pass silently — an audit found them by running
    # them against the real schema, not by reading it.
    @pytest.mark.parametrize(
        "smuggled",
        [
            "acreage=99999",
            "tier=5",  # rewrites a T1 agency number as an unvetted claim
            "confidence=0.01",
            "evac_status='lifted'",  # tells the map an evacuation ended
            "containment_pct=100",
            "lat=0, lon=0",
            "geo_method='exif'",
            "structured='{\"injected\":true}'",
            "kind='other'",
            "envelope_id='e1', content='tampered'",
        ],
    )
    def test_no_column_rides_along_with_adjudication(self, seeded, smuggled):
        insert_fact(seeded, "f2", incident_id=None)
        with pytest.raises(sqlite3.IntegrityError, match="supersession"):
            seeded.execute(f"UPDATE facts SET incident_id='i1', {smuggled} WHERE id='f2'")
        assert (
            seeded.execute("SELECT incident_id FROM facts WHERE id='f2'").fetchone()["incident_id"]
            is None
        )

    def test_no_column_rides_along_with_a_star(self, seeded):
        insert_fact(seeded, "f1")
        with pytest.raises(sqlite3.IntegrityError, match="supersession"):
            seeded.execute("UPDATE facts SET starred=1, tier=5 WHERE id='f1'")
        row = seeded.execute("SELECT tier, starred FROM facts WHERE id='f1'").fetchone()
        assert (row["tier"], row["starred"]) == (1, 0)  # neither half landed

    def test_attributed_fact_cannot_be_repointed_at_another_incident(self, seeded):
        """Attribution is one-way. Moving a claim between fires would rewrite
        two incidents' histories at once, with nothing recording that it moved."""
        seeded.execute(
            "INSERT INTO incidents (id, canonical_name, status, created_at, updated_at)"
            " VALUES ('i2', 'OTHER FIRE', 'active', ?, ?)",
            (NOW, NOW),
        )
        insert_fact(seeded, "f1")  # already attributed to i1
        with pytest.raises(sqlite3.IntegrityError, match="supersession"):
            seeded.execute("UPDATE facts SET incident_id='i2' WHERE id='f1'")

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

    def test_adjudication_cannot_attach_a_fact_to_a_frozen_incident(self, seeded):
        """The frozen guard was BEFORE INSERT only, which left the adjudication
        UPDATE as an unguarded way into a closed record."""
        insert_fact(seeded, "f2", incident_id=None)
        self._freeze(seeded)
        with pytest.raises(sqlite3.IntegrityError, match="frozen"):
            seeded.execute("UPDATE facts SET incident_id='i1' WHERE id='f2'")


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
        seeded.execute(
            "UPDATE drafts SET approved_by='operator', approved_at=? WHERE id='d1'", (NOW,)
        )
        seeded.execute("UPDATE drafts SET status='published' WHERE id='d1'")

    def test_published_is_terminal(self, seeded):
        self.test_full_legal_path(seeded)
        for target in ("draft", "needs_review", "approved", "rejected"):
            with pytest.raises(sqlite3.IntegrityError):
                seeded.execute("UPDATE drafts SET status=? WHERE id='d1'", (target,))


class TestEnumConstraints:
    """`CHECK (x IN (NULL, ...))` is NULL for a non-matching value, and CHECK
    passes on NULL — so the original enums accepted anything at all. These
    assert the constraints actually constrain.

    The evac case is the one that matters: a stored 'Order — Zone BUT-1043'
    counts as `unclassified` in intel.py, so the panel reports zero evacuation
    orders while an order is live."""

    def _insert(self, conn, col, value):
        conn.execute(
            f"INSERT INTO facts (id, envelope_id, source_id, tier, content, kind, {col},"
            " confidence, observed_at, ingested_at)"
            " VALUES ('probe', 'e1', 's1', 1, 'claim', 'evac', ?, 0.9, ?, ?)",
            (value, NOW, NOW),
        )

    @pytest.mark.parametrize(
        "col,value",
        [
            ("evac_status", "Order — Zone BUT-1043"),  # verbatim county wording
            ("evac_status", "Order"),  # case drift
            ("evac_status", "evacuation order"),  # a paraphrase
            ("geo_method", "hunch"),
            ("geo_method", "GPS"),
        ],
    )
    def test_out_of_enum_value_rejected(self, seeded, col, value):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            self._insert(seeded, col, value)

    @pytest.mark.parametrize("value", ["order", "warning", "shelter_in_place", "lifted", None])
    def test_legal_evac_values_still_accepted(self, seeded, value):
        self._insert(seeded, "evac_status", value)

    def test_named_feature_is_legal_geo_method(self, seeded):
        """geocode.py has written this since the GNIS work and geoverify.py
        reads it — the enum simply never learned about it."""
        self._insert(seeded, "geo_method", "named_feature")

    def test_fact_kind_is_constrained_to_the_extractor_vocabulary(self, seeded):
        conn_kinds = (
            "start",
            "acreage",
            "containment",
            "evac",
            "road",
            "resource",
            "damage",
            "cause",
            "weather",
            "other",
        )
        from typing import get_args

        from toast.llm.extract import ClaimKind

        assert set(get_args(ClaimKind)) == set(conn_kinds), "DB enum drifted from ClaimKind"
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            seeded.execute(
                "INSERT INTO facts (id, envelope_id, source_id, tier, content, kind,"
                " confidence, observed_at, ingested_at)"
                " VALUES ('probe2', 'e1', 's1', 1, 'claim', 'speculation', 0.9, ?, ?)",
                (NOW, NOW),
            )
