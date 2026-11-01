"""Auto-attachment can't be undone, so strong-looking evidence must not be enough.

A dry run of the CAL FIRE backfill attached 31 facts and got two wrong. "Aspen Fire in
Mono County" went to the Lassen Fire in Tehama on a look-alike name and an inferred
pin. "Railroad Fire in Contra Costa County" went to a Butte fire of the same name from
a month earlier. These cases now go to the operator instead.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from toast.core.db import utcnow
from toast.pipeline.attribution import attribute_fact


@pytest.fixture
def db_seeded_sources(seeded):
    """The shared fixture's incident would compete as a candidate, so it goes dormant."""
    seeded.execute("UPDATE incidents SET status = 'frozen' WHERE id = 'i1'")
    return seeded


def _ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _incident(conn, iid, name, county, lat=None, lon=None, last_activity=None):
    now = utcnow()
    conn.execute(
        "INSERT INTO incidents (id, canonical_name, status, county, lat, lon, created_at,"
        " updated_at) VALUES (?, ?, 'contained', ?, ?, ?, ?, ?)",
        (iid, name, county, lat, lon, now, last_activity or now),
    )


def _fact(conn, fid, name, content, observed=None, lat=None, lon=None, location=None):
    conn.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " structured, confidence, observed_at, ingested_at, lat, lon)"
        " VALUES (?, NULL, 'e1', 's1', 4, ?, 'start', ?, 0.6, ?, ?, ?, ?)",
        (
            fid,
            content,
            json.dumps({"incident_name": name, "location_text": location}),
            observed or utcnow(),
            utcnow(),
            lat,
            lon,
        ),
    )


def _outcome(conn, fid):
    outcome, _ = attribute_fact(conn, fid)
    attached = conn.execute("SELECT incident_id FROM facts WHERE id = ?", (fid,)).fetchone()[0]
    return outcome, attached


def test_same_name_in_another_stated_county_goes_to_review(db_seeded_sources):
    conn = db_seeded_sources
    _incident(conn, "butte", "Railroad Fire", "Butte")
    _fact(conn, "f1", "Railroad Fire", "Railroad Fire was reported in Contra Costa County.")
    assert _outcome(conn, "f1") == ("review", None)


def test_county_in_location_text_counts_as_stated(db_seeded_sources):
    conn = db_seeded_sources
    _incident(conn, "butte", "Railroad Fire", "Butte")
    _fact(conn, "f1", "Railroad Fire", "The fire grew.", location="Contra Costa County")
    assert _outcome(conn, "f1") == ("review", None)


def test_same_name_in_the_same_county_still_attaches(db_seeded_sources):
    conn = db_seeded_sources
    _incident(conn, "butte", "Railroad Fire", "Butte")
    _fact(conn, "f1", "Railroad Fire", "Railroad Fire was reported in Butte County.")
    assert _outcome(conn, "f1") == ("attributed", "butte")


def test_a_multi_county_incident_matches_any_of_its_counties(db_seeded_sources):
    conn = db_seeded_sources
    _incident(conn, "two", "Ridge Fire", "Los Angeles, Ventura")
    _fact(conn, "f1", "Ridge Fire", "Ridge Fire spread in Ventura County.")
    assert _outcome(conn, "f1") == ("attributed", "two")


def test_a_lake_in_the_text_is_not_a_county_conflict(db_seeded_sources):
    conn = db_seeded_sources
    _incident(conn, "shasta", "Ridge Fire", "Shasta")
    _fact(conn, "f1", "Ridge Fire", "Ridge Fire burns near Shasta Lake and Orange Road.")
    assert _outcome(conn, "f1") == ("attributed", "shasta")


def test_no_stated_county_is_not_a_conflict(db_seeded_sources):
    conn = db_seeded_sources
    _incident(conn, "peak", "Peak Fire", "Los Angeles")
    _fact(conn, "f1", "Peak Fire", "The Peak Fire is 100% contained.")
    assert _outcome(conn, "f1") == ("attributed", "peak")


def test_a_look_alike_name_with_a_nearby_pin_goes_to_review(db_seeded_sources):
    conn = db_seeded_sources
    _incident(conn, "lassen", "Lassen Fire", "Tehama", lat=40.3, lon=-121.9)
    _fact(conn, "f1", "Aspen Fire", "Human activity caused the Aspen Fire.", lat=40.3, lon=-121.9)
    outcome, attached = _outcome(conn, "f1")
    assert attached is None
    assert outcome == "review"


def test_an_exact_name_far_outside_the_fires_activity_goes_to_review(db_seeded_sources):
    """Fire names repeat, and with no time overlap or location a name is all there is."""
    conn = db_seeded_sources
    _incident(conn, "old", "Ridge Fire", None)
    conn.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " confidence, observed_at, ingested_at)"
        " VALUES ('old1', 'old', 'e1', 's1', 1, 'c', 'start', 0.9, ?, ?)",
        (_ago(40), utcnow()),
    )
    _fact(conn, "f1", "Ridge Fire", "Ridge Fire was reported today.")
    assert _outcome(conn, "f1") == ("review", None)


def test_an_exact_name_within_the_fires_activity_still_attaches(db_seeded_sources):
    conn = db_seeded_sources
    _incident(conn, "bouquet", "Bouquet Fire", "Los Angeles")
    conn.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " confidence, observed_at, ingested_at)"
        " VALUES ('b1', 'bouquet', 'e1', 's1', 1, 'c', 'start', 0.9, ?, ?)",
        (_ago(3), utcnow()),
    )
    _fact(conn, "f1", "Bouquet Fire", "The Bouquet Fire size is 1,048 acres.")
    assert _outcome(conn, "f1") == ("attributed", "bouquet")
