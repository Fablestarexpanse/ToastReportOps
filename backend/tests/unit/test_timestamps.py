"""Timestamps are the spine of a bi-temporal ledger.

An LLM writes `observed_at`. SQLite's unixepoch() returns NULL for anything it
cannot parse, and NULL propagates silently through every window and comparison
in the system. Four layers are asserted here: the normalizer, the extractor
boundary, the ledger boundary, and the DB CHECK.
"""

import sqlite3

import pytest

from toast.core.timestamps import normalize_iso, require_iso
from toast.pipeline.ledger import write_fact

NOW = "2026-07-19T00:00:00.000Z"


class TestNormalizer:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2026-07-19T18:30:00Z", "2026-07-19T18:30:00.000Z"),
            ("2026-07-19T18:30:00.250Z", "2026-07-19T18:30:00.250Z"),
            # the form CAL FIRE would emit if it ever switched off Z — this used
            # to become the nonsense "...-07:00Z" further downstream
            ("2026-07-19T11:33:41-07:00", "2026-07-19T18:33:41.000Z"),
            ("2026-07-19 18:30:00", "2026-07-19T18:30:00.000Z"),
            ("2026-07-19", "2026-07-19T00:00:00.000Z"),
        ],
    )
    def test_real_feed_forms_are_accepted(self, raw, expected):
        assert normalize_iso(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "July 19, 2026",
            "yesterday evening",
            "sometime Sunday",
            "",
            "   ",
            None,
            "2026-13-45T99:99:99Z",
        ],
    )
    def test_prose_and_junk_are_refused_not_guessed(self, raw):
        """Inventing an hour for a fire report is worse than admitting we don't
        know it."""
        assert normalize_iso(raw) is None

    def test_require_iso_raises_with_the_field_name(self):
        with pytest.raises(ValueError, match="observed_at"):
            require_iso("yesterday evening", field="observed_at")


class TestExtractorBoundary:
    def test_unparseable_model_date_becomes_none(self):
        from toast.llm.extract import ExtractedClaim

        claim = ExtractedClaim(
            content="The Sawmill Fire has burned 400 acres.",
            kind="acreage",
            observed_at="July 19, 2026",
            confidence=0.8,
        )
        assert claim.observed_at is None  # caller falls back to ingest time

    def test_offset_form_is_normalized_not_rejected(self):
        from toast.llm.extract import ExtractedClaim

        claim = ExtractedClaim(
            content="Crews held the line overnight.",
            kind="other",
            observed_at="2026-07-19T11:33:41-07:00",
            confidence=0.5,
        )
        assert claim.observed_at == "2026-07-19T18:33:41.000Z"


class TestLedgerBoundary:
    def _write(self, conn, observed_at):
        return write_fact(
            conn,
            incident_id="i1",
            envelope_id="e1",
            source_id="s1",
            tier=1,
            content="claim",
            kind="acreage",
            observed_at=observed_at,
        )

    def test_ledger_refuses_a_timestamp_the_db_cannot_use(self, seeded):
        with pytest.raises(ValueError, match="observed_at"):
            self._write(seeded, "July 19, 2026")

    def test_ledger_normalizes_on_the_way_in(self, seeded):
        fact_id = self._write(seeded, "2026-07-19T11:33:41-07:00")
        stored = seeded.execute(
            "SELECT observed_at FROM facts WHERE id = ?", (fact_id,)
        ).fetchone()["observed_at"]
        assert stored == "2026-07-19T18:33:41.000Z"
        # and the DB can actually do arithmetic on what we stored
        assert seeded.execute("SELECT unixepoch(?)", (stored,)).fetchone()[0] is not None


class TestDatabaseBackstop:
    def test_check_rejects_an_unusable_timestamp_from_any_writer(self, seeded):
        """The layer that holds even if a future writer forgets."""
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            seeded.execute(
                "INSERT INTO facts (id, envelope_id, source_id, tier, content, kind,"
                " confidence, observed_at, ingested_at)"
                " VALUES ('probe', 'e1', 's1', 1, 'claim', 'acreage', 0.9, 'July 19, 2026', ?)",
                (NOW,),
            )


class TestQueueSurvivesABadRow:
    def test_scoring_treats_an_unusable_time_as_no_evidence(self, seeded):
        """A legacy row must not 500 the entire adjudication queue: the route
        scores every queued fact, so one bad date used to darken all of them."""
        from toast.pipeline.attribution import score_candidates

        candidates = score_candidates(
            seeded, incident_name="Flume Fire", observed_at="July 19, 2026", lat=None, lon=None
        )
        assert all(c.time_score == 0.0 for c in candidates)
