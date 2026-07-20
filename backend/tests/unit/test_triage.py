"""Adjudication triage — the queue must be clearable without touching facts."""

import pytest
from fastapi import HTTPException

from toast.api import routes
from toast.core.ids import ulid

NOW = "2026-07-19T00:00:00.000Z"


class Req:
    def __init__(self, conn):
        self.app = type("A", (), {"state": type("S", (), {"conn": conn})()})()


def _unattributed(db, content="a fire happened", kind="start", structured="{}") -> str:
    fid = ulid()
    db.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " structured, confidence, observed_at, ingested_at)"
        " VALUES (?, NULL, 'e1', 's1', 4, ?, ?, ?, 0.7, ?, ?)",
        (fid, content, kind, structured, NOW, NOW),
    )
    return fid


def _queue(req):
    return routes.adjudication_queue(req, limit=50)["queue"]


def test_triage_removes_from_queue_without_touching_the_fact(seeded):
    req = Req(seeded)
    fid = _unattributed(seeded)
    assert any(i["fact"]["id"] == fid for i in _queue(req))

    routes.triage_fact(req, fid, {"state": "not_incident"})

    assert not any(i["fact"]["id"] == fid for i in _queue(req))
    # the claim itself is untouched — append-only holds
    row = seeded.execute("SELECT content, incident_id FROM facts WHERE id = ?", (fid,)).fetchone()
    assert row["content"] == "a fire happened"
    assert row["incident_id"] is None


def test_untriage_restores_to_queue(seeded):
    req = Req(seeded)
    fid = _unattributed(seeded)
    routes.triage_fact(req, fid, {"state": "not_incident"})
    routes.untriage_fact(req, fid)
    assert any(i["fact"]["id"] == fid for i in _queue(req))


def test_bulk_triage_clears_many(seeded):
    req = Req(seeded)
    ids = [_unattributed(seeded, content=f"claim {i}") for i in range(5)]
    assert routes.triage_bulk(req, {"state": "not_incident", "fact_ids": ids})["triaged"] == 5
    assert _queue(req) == []


def test_invalid_state_rejected(seeded):
    req = Req(seeded)
    fid = _unattributed(seeded)
    with pytest.raises(HTTPException) as exc:
        routes.triage_fact(req, fid, {"state": "deleted"})
    assert exc.value.status_code == 400


def test_attributed_fact_cannot_be_triaged(seeded):
    req = Req(seeded)
    fid = _unattributed(seeded)
    routes.resolve_adjudication(req, fid, {"incident_id": "i1"})
    with pytest.raises(HTTPException) as exc:
        routes.triage_fact(req, fid, {"state": "not_incident"})
    assert exc.value.status_code == 409


def test_time_only_candidates_are_suppressed_as_noise(seeded):
    """Every open incident is 'recent', so a time-only match ranks the same for
    every fact — offering it as a candidate is worse than offering nothing."""
    req = Req(seeded)
    _unattributed(seeded, content="A fire damaged a wreck yard in Modesto.", kind="damage")
    item = _queue(req)[0]
    assert item["candidates"] == []
    assert item["unknown_fire"] is False  # no name claimed at all


def test_fact_naming_an_untracked_fire_is_flagged_for_watch(seeded):
    req = Req(seeded)
    _unattributed(
        seeded,
        content="The Coast Fire was reported in Los Angeles County.",
        structured='{"incident_name": "Coast Fire"}',
    )
    item = _queue(req)[0]
    assert item["claimed_name"] == "Coast Fire"
    assert item["unknown_fire"] is True  # FLUME FIRE is the only seeded incident


def test_matching_name_is_not_flagged_unknown(seeded):
    req = Req(seeded)
    _unattributed(
        seeded,
        content="FLUME FIRE grew overnight.",
        structured='{"incident_name": "Flume Fire"}',
    )
    item = _queue(req)[0]
    assert item["unknown_fire"] is False
    assert item["candidates"][0]["incident_name"] == "FLUME FIRE"
