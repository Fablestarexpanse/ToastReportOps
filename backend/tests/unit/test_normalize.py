"""Normalize pipeline: incident auto-creation, value-fact supersession chains,
hotspot geo-attachment, alert facts, envelope dedup."""

import json

from toast.core.envelope import Envelope, insert_envelope
from toast.pipeline.normalize import normalize_envelope

NOW = "2026-07-19T12:00:00.000Z"
LATER = "2026-07-19T13:00:00.000Z"


def _report_env(db, acreage, containment, observed, name="FLUME FIRE"):
    env = Envelope(
        source_id="s1",
        external_id="irwin-1",
        kind="report",
        payload={
            "irwin_id": "irwin-1",
            "name": name,
            "type": "WF",
            "acreage": acreage,
            "containment_pct": containment,
            "discovered_at": NOW,
            "modified_at": observed,
            "county": "Butte",
            "lat": 39.76,
            "lon": -121.6,
        },
    )
    return insert_envelope(db, env)


def test_report_creates_incident_with_provenance_and_facts(seeded):
    env_id = _report_env(seeded, 100.0, 0.0, NOW)
    facts = normalize_envelope(seeded, env_id)
    assert len(facts) == 3  # start + acreage + containment

    inc = seeded.execute("SELECT * FROM incidents WHERE irwin_id='irwin-1'").fetchone()
    assert inc is not None and inc["status"] == "active"
    assert inc["canonical_name"] == "FLUME FIRE"
    assert inc["first_detected_fact_id"] is not None  # first-detection provenance

    start = seeded.execute(
        "SELECT * FROM facts WHERE id=?", (inc["first_detected_fact_id"],)
    ).fetchone()
    assert start["kind"] == "start" and start["geo_method"] == "source_fixed"


def test_changed_value_supersedes_unchanged_skips(seeded):
    env1 = _report_env(seeded, 100.0, 0.0, NOW)
    normalize_envelope(seeded, env1)

    # same values later -> no new facts
    env2 = _report_env(seeded, 100.0, 0.0, LATER)
    assert normalize_envelope(seeded, env2) == []

    # grown fire -> acreage fact superseding the original
    env3 = _report_env(seeded, 350.0, 10.0, LATER)
    facts = normalize_envelope(seeded, env3)
    assert len(facts) == 2  # acreage + containment changed

    chain = seeded.execute(
        """SELECT f.acreage, f.supersedes, prev.acreage AS prev_acreage FROM facts f
           LEFT JOIN facts prev ON prev.id = f.supersedes
           WHERE f.kind='acreage' AND f.supersedes IS NOT NULL"""
    ).fetchone()
    assert chain["acreage"] == 350.0 and chain["prev_acreage"] == 100.0


def test_older_report_never_supersedes(seeded):
    env1 = _report_env(seeded, 350.0, None, LATER)
    normalize_envelope(seeded, env1)
    stale_env = _report_env(seeded, 100.0, None, NOW)  # older observed_at
    assert normalize_envelope(seeded, stale_env) == []


def test_envelope_dedup(seeded):
    assert _report_env(seeded, 100.0, 0.0, NOW) is not None
    assert _report_env(seeded, 100.0, 0.0, NOW) is None  # identical content dropped


def test_hotspot_attaches_to_nearby_incident(seeded):
    env1 = _report_env(seeded, 100.0, 0.0, NOW)
    normalize_envelope(seeded, env1)
    inc = seeded.execute("SELECT id FROM incidents WHERE irwin_id='irwin-1'").fetchone()

    near = Envelope(
        source_id="s1",
        external_id="h1",
        kind="hotspot",
        payload={"lat": 39.80, "lon": -121.58, "frp": 12.0, "satellite": "N", "observed_at": LATER},
    )
    fid = normalize_envelope(seeded, insert_envelope(seeded, near))[0]
    fact = seeded.execute("SELECT * FROM facts WHERE id=?", (fid,)).fetchone()
    assert fact["incident_id"] == inc["id"] and fact["kind"] == "other"

    far = Envelope(
        source_id="s1",
        external_id="h2",
        kind="hotspot",
        payload={"lat": 41.9, "lon": -114.2, "frp": 5.0, "satellite": "N", "observed_at": LATER},
    )
    fid2 = normalize_envelope(seeded, insert_envelope(seeded, far))[0]
    fact2 = seeded.execute("SELECT * FROM facts WHERE id=?", (fid2,)).fetchone()
    assert fact2["incident_id"] is None and fact2["kind"] == "start"


def test_alert_becomes_unattributed_weather_fact(seeded):
    env = Envelope(
        source_id="s1",
        external_id="a1",
        kind="alert",
        payload={
            "event": "Red Flag Warning",
            "headline": "Red Flag Warning until 8 PM",
            "area_desc": "Butte County",
            "sent": NOW,
            "severity": "Severe",
        },
    )
    fid = normalize_envelope(seeded, insert_envelope(seeded, env))[0]
    fact = seeded.execute("SELECT * FROM facts WHERE id=?", (fid,)).fetchone()
    assert fact["kind"] == "weather" and fact["incident_id"] is None
    assert "Red Flag Warning" in fact["content"]
    assert json.loads(fact["structured"])["area_desc"] == "Butte County"
