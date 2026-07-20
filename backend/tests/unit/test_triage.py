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


def test_reattribution_adopts_facts_whose_fire_appeared_later(seeded):
    """Media names a fire before CAL FIRE lists it. The fact arrives with no
    incident to match, and before this sweep it stayed orphaned forever."""
    from toast.pipeline.handlers import reattribute_handler

    fid = _unattributed(
        seeded,
        content="The Sawmill Fire has burned 400 acres in Butte County.",
        structured='{"incident_name": "Sawmill Fire"}',
    )
    assert (
        seeded.execute("SELECT incident_id FROM facts WHERE id = ?", (fid,)).fetchone()[
            "incident_id"
        ]
        is None
    )

    # the incident is listed an hour later
    seeded.execute(
        "INSERT INTO incidents (id, canonical_name, status, county, created_at, updated_at)"
        " VALUES ('i-saw', 'Sawmill Fire', 'active', 'Butte', ?, ?)",
        (NOW, NOW),
    )
    reattribute_handler(seeded, {})

    assert (
        seeded.execute("SELECT incident_id FROM facts WHERE id = ?", (fid,)).fetchone()[
            "incident_id"
        ]
        == "i-saw"
    )


def test_reattribution_skips_triaged_facts(seeded):
    """A fact the operator dismissed must not come back through the sweep."""
    from toast.pipeline.handlers import reattribute_handler

    fid = _unattributed(seeded, content="A structure fire damaged a wreck yard.")
    seeded.execute(
        "INSERT INTO fact_triage (fact_id, state, at) VALUES (?, 'not_incident', ?)", (fid, NOW)
    )
    reattribute_handler(seeded, {})
    assert (
        seeded.execute("SELECT incident_id FROM facts WHERE id = ?", (fid,)).fetchone()[
            "incident_id"
        ]
        is None
    )


def test_satellite_pixels_stay_out_of_the_adjudication_queue(seeded):
    """One FIRMS poll adds hundreds of unattached hotspots. A VIIRS pixel with no
    nearby known fire carries no name, no text and no context — there is nothing
    for a human to match it against, and 126 identical rows bury the news claims
    that DO need a decision. They are new-start signals; the WATCH board reads
    unattributed facts and clusters them there."""
    req = Req(seeded)
    seeded.execute(
        "INSERT INTO sources (id, name, type, adapter, tier, created_at, updated_at)"
        " VALUES ('s-sat', 'FIRMS', 'satellite', 'firms', 2, ?, ?)",
        (NOW, NOW),
    )
    seeded.execute(
        "INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,"
        " fetched_at) VALUES ('e-sat', 's-sat', 'h1', 'hotspot', '{}', 'hh', ?)",
        (NOW,),
    )
    hotspot = ulid()
    seeded.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " lat, lon, geo_method, confidence, observed_at, ingested_at)"
        " VALUES (?, NULL, 'e-sat', 's-sat', 2, 'Hotspot detected at 40.7, -120.1', 'start',"
        " 40.7, -120.1, 'source_fixed', 0.7, ?, ?)",
        (hotspot, NOW, NOW),
    )
    news = _unattributed(seeded, content="The Sawmill Fire has burned 400 acres.")

    queued = {i["fact"]["id"] for i in _queue(req)}
    assert hotspot not in queued, "a satellite pixel is not adjudication work"
    assert news in queued, "news claims must still reach the operator"


def test_defer_all_then_restore_round_trips(seeded):
    """DEFER ALL sets the whole queue aside; RESTORE brings it back. 'Deferred'
    must never be a one-way trip — that is the only thing separating it from
    'not this fire'."""
    req = Req(seeded)
    ids = [_unattributed(seeded, content=f"claim {i}") for i in range(4)]
    assert routes.triage_bulk(req, {"state": "deferred", "fact_ids": ids})["triaged"] == 4
    assert _queue(req) == []  # all set aside
    assert routes.deferred_count(req)["deferred"] == 4

    restored = routes.restore_deferred(req)["restored"]
    assert restored == 4
    assert routes.deferred_count(req)["deferred"] == 0
    assert len(_queue(req)) == 4  # every one is back, none destroyed


def test_restore_leaves_other_triage_states_alone(seeded):
    """RESTORE brings back deferred facts only — a fact the operator judged
    'not an incident' stays cleared."""
    req = Req(seeded)
    a = _unattributed(seeded, content="a deferred one")
    b = _unattributed(seeded, content="a dismissed one")
    routes.triage_bulk(req, {"state": "deferred", "fact_ids": [a]})
    routes.triage_bulk(req, {"state": "not_incident", "fact_ids": [b]})
    routes.restore_deferred(req)
    queued = {i["fact"]["id"] for i in _queue(req)}
    assert a in queued and b not in queued
