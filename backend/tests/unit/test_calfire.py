"""CAL FIRE adapter + active-status derivation through the normalize pipeline."""

from toast.core.envelope import insert_envelope
from toast.pipeline.normalize import normalize_envelope
from toast.sources.calfire import incident_to_payload

ACTIVE = {
    "Name": "Flume Fire",
    "UniqueId": "cf-flume-1",
    "Type": "Wildfire",
    "AcresBurned": 1240.0,
    "PercentContained": 0.0,
    "County": "Butte",
    "Latitude": 39.76,
    "Longitude": -121.6,
    "IsActive": True,
    "Started": "2026-07-19T20:35:02Z",
    "Updated": "2026-07-19T22:00:00Z",
    "ExtinguishedDate": "",
    "Url": "https://www.fire.ca.gov/incidents/2026/7/19/flume-fire/",
}
CLOSED = {
    **ACTIVE,
    "Name": "Old Fire",
    "UniqueId": "cf-old-1",
    "IsActive": False,
    "PercentContained": 100.0,
    "ExtinguishedDate": "2026-06-01T00:00:00Z",
}


def test_incident_to_payload_maps_fields():
    env = incident_to_payload(ACTIVE, "s1")
    assert env.kind == "report" and env.external_id == "cf-flume-1"
    p = env.payload
    assert p["ref_type"] == "calfire" and p["ref_id"] == "cf-flume-1"
    assert p["acreage"] == 1240.0 and p["containment_pct"] == 0.0
    assert p["is_active"] is True and p["county"] == "Butte"
    assert p["discovered_at"] == "2026-07-19T20:35:02Z"


def _ingest(conn, item):
    env = incident_to_payload(item, "s1")
    env_id = insert_envelope(conn, env)
    return normalize_envelope(conn, env_id)


def test_active_fire_adopts_the_seeded_same_name_incident(seeded):
    """CAL FIRE's Flume Fire is the fixture's FLUME FIRE — one incident,
    now carrying the official id, not a duplicate."""
    facts = _ingest(seeded, ACTIVE)
    assert len(facts) >= 1
    inc = seeded.execute("SELECT * FROM incidents WHERE calfire_id='cf-flume-1'").fetchone()
    assert inc is not None and inc["id"] == "i1" and inc["status"] == "active"
    only = seeded.execute(
        "SELECT COUNT(*) FROM incidents WHERE canonical_name LIKE '%lume%'"
    ).fetchone()[0]
    assert only == 1


def test_inactive_fire_creates_contained_incident(seeded):
    _ingest(seeded, CLOSED)
    inc = seeded.execute("SELECT status FROM incidents WHERE calfire_id='cf-old-1'").fetchone()
    assert inc["status"] == "contained"  # is_active False -> contained, not active


def test_status_advances_but_never_regresses(seeded):
    _ingest(seeded, ACTIVE)
    # fire becomes contained -> advances
    _ingest(
        seeded,
        {**ACTIVE, "IsActive": False, "PercentContained": 100.0, "Updated": "2026-07-20T00:00:00Z"},
    )
    inc = seeded.execute("SELECT status FROM incidents WHERE calfire_id='cf-flume-1'").fetchone()
    assert inc["status"] == "contained"
    # a stale poll still calling it active must NOT flip it back
    _ingest(
        seeded,
        {**ACTIVE, "IsActive": True, "Updated": "2026-07-20T01:00:00Z", "PercentContained": 50.0},
    )
    inc = seeded.execute("SELECT status FROM incidents WHERE calfire_id='cf-flume-1'").fetchone()
    assert inc["status"] == "contained"


def test_dedup_identical_snapshot(seeded):
    env = incident_to_payload(ACTIVE, "s1")
    assert insert_envelope(seeded, env) is not None
    assert insert_envelope(seeded, incident_to_payload(ACTIVE, "s1")) is None
