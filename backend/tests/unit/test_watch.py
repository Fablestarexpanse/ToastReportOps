"""Watch board: clustering unattributed named-fire signals, corroboration
state, exclusion of active-incident names, and promotion."""

import json

from toast.core.db import utcnow
from toast.pipeline.watch import build_watchlist, promote


def _source(conn, sid, name, tier=4):
    conn.execute(
        "INSERT INTO sources (id, name, type, adapter, tier, created_at, updated_at)"
        " VALUES (?, ?, 'scrape', 'rss', ?, ?, ?)",
        (sid, name, tier, utcnow(), utcnow()),
    )


def _named_fact(conn, fid, source_id, name, tier=4, lat=None, lon=None, loc=None):
    conn.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " structured, confidence, observed_at, ingested_at, lat, lon)"
        " VALUES (?, NULL, 'e1', ?, ?, ?, 'start', ?, 0.6, ?, ?, ?, ?)",
        (
            fid,
            source_id,
            tier,
            f"{name} reported burning",
            json.dumps({"incident_name": name, "location_text": loc}),
            utcnow(),
            utcnow(),
            lat,
            lon,
        ),
    )


def test_single_source_is_watching(seeded):
    _named_fact(seeded, "f1", "s1", "Ridge Fire")
    board = build_watchlist(seeded)
    assert len(board) == 1
    c = board[0]
    assert c.name == "Ridge Fire" and c.source_count == 1 and c.state == "watching"


def test_multiple_sources_corroborate(seeded):
    _source(seeded, "s2", "KTLA")
    _source(seeded, "s3", "Mastodon")
    _named_fact(seeded, "f1", "s1", "Ridge Fire")
    _named_fact(seeded, "f2", "s2", "Ridge Fire")
    _named_fact(seeded, "f3", "s3", "ridge fire")  # same fire, different casing
    board = build_watchlist(seeded)
    assert len(board) == 1  # clustered into one candidate
    c = board[0]
    assert c.source_count == 3 and c.fact_count == 3 and c.state == "corroborating"
    assert c.score >= 0.75  # 3 sources → high corroboration


def test_names_matching_active_incidents_are_excluded(seeded):
    # an active incident named "Biscar Fire" exists; media naming it is
    # attribution, not a new start
    seeded.execute(
        "INSERT INTO incidents (id, canonical_name, status, created_at, updated_at)"
        " VALUES ('i-biscar', 'BISCAR FIRE', 'active', ?, ?)",
        (utcnow(), utcnow()),
    )
    _named_fact(seeded, "f1", "s1", "Biscar Fire")
    _named_fact(seeded, "f2", "s1", "Ridge Fire")
    board = build_watchlist(seeded)
    names = {c.name for c in board}
    assert "Ridge Fire" in names and "Biscar Fire" not in names


def test_promote_creates_incident_and_attributes(seeded):
    _source(seeded, "s2", "KTLA")
    _named_fact(seeded, "f1", "s1", "Ridge Fire", lat=39.5, lon=-121.5, loc="near Oroville")
    _named_fact(seeded, "f2", "s2", "Ridge Fire")
    board = build_watchlist(seeded)
    key = board[0].key
    result = promote(seeded, key)
    assert result["promoted"] and result["facts_attributed"] == 2

    inc = seeded.execute(
        "SELECT * FROM incidents WHERE id = ?", (result["incident_id"],)
    ).fetchone()
    assert inc["canonical_name"] == "Ridge Fire" and inc["status"] == "candidate"
    assert inc["lat"] == 39.5 and inc["first_detected_fact_id"] is not None
    # facts now attributed → cluster leaves the watch board
    assert not any(c.key == key for c in build_watchlist(seeded))
