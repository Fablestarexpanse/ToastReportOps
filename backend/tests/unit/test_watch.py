"""Watch board: clustering unattributed named-fire signals, corroboration
state, exclusion of active-incident names, and promotion."""

import json
from datetime import UTC, datetime, timedelta

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


def _fact(conn, fid, name, kind="start", content=None, observed=None, estimated=False, env="e1"):
    structured = {"incident_name": name, **({"observed_at_estimated": True} if estimated else {})}
    conn.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " structured, confidence, observed_at, ingested_at)"
        " VALUES (?, NULL, ?, 's1', 4, ?, ?, ?, 0.6, ?, ?)",
        (
            fid,
            env,
            content or f"{name} reported burning",
            kind,
            json.dumps(structured),
            observed or utcnow(),
            utcnow(),
        ),
    )


def _envelope_published(conn, env_id, published_at):
    conn.execute(
        "INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,"
        " fetched_at) VALUES (?, 's1', ?, 'article', ?, ?, ?)",
        (env_id, env_id, json.dumps({"published_at": published_at}), env_id, utcnow()),
    )


def _ago(hours):
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _says(monkeypatch, **by_name):
    """Stub the official lists: name -> status, or (status, category)."""

    def fake_check(conn, name, county):
        answer = by_name.get(name, "none")
        status, category = answer if isinstance(answer, tuple) else (answer, None)
        return {
            "status": status,
            "source": "CAL FIRE",
            "acres": 570.0,
            "containment": 100.0,
            "started": None,
            "final": True,
            "category": category,
        }

    monkeypatch.setattr("toast.pipeline.official_lookup.check", fake_check)


def _groups(conn):
    return {c.name: c.group for c in build_watchlist(conn)}


def test_a_start_with_no_official_record_is_new(seeded, monkeypatch):
    _says(monkeypatch)
    _fact(seeded, "f1", "Ridge Fire")
    assert _groups(seeded) == {"Ridge Fire": "new"}


def test_a_closed_fire_is_already_over_whatever_the_story_says(seeded, monkeypatch):
    _says(monkeypatch, **{"Norte Fire": "closed"})
    _fact(seeded, "f1", "Norte Fire")
    assert _groups(seeded) == {"Norte Fire": "over"}


def test_an_officially_listed_fire_is_official(seeded, monkeypatch):
    _says(monkeypatch, **{"Aspen Fire": "reported", "Other Fire": "active"})
    _fact(seeded, "f1", "Aspen Fire")
    _fact(seeded, "f2", "Other Fire")
    assert _groups(seeded) == {"Aspen Fire": "official", "Other Fire": "official"}


def test_prescribed_burns_are_separated_by_text(seeded, monkeypatch):
    _says(monkeypatch)
    _fact(
        seeded, "f1", "Klamath Project", kind="other", content="Incident type is Prescribed Fire."
    )
    _fact(seeded, "f2", "Hayden", kind="acreage", content="70 acres were underburned at Hayden.")
    assert set(_groups(seeded).values()) == {"prescribed"}


def test_prescribed_burns_are_separated_by_official_category(seeded, monkeypatch):
    _says(monkeypatch, **{"Quiet Unit": ("reported", "RX")})
    _fact(seeded, "f1", "Quiet Unit")
    assert _groups(seeded) == {"Quiet Unit": "prescribed"}


def test_a_story_with_no_start_fact_is_a_mention(seeded, monkeypatch):
    _says(monkeypatch)
    _fact(seeded, "f1", "Old Town fire", kind="cause", content="Arson is behind the fire.")
    _fact(seeded, "f2", "Old Town fire", kind="damage", content="Seven structures burned.")
    assert _groups(seeded) == {"Old Town fire": "mentions"}


def test_an_unreadable_official_list_never_reads_as_no_record(seeded):
    _fact(seeded, "f1", "Ridge Fire")  # nothing has refreshed the lists
    (c,) = build_watchlist(seeded)
    assert c.official["status"] == "unknown"
    assert c.group == "new"


def test_an_undated_fact_is_as_old_as_its_article(seeded, monkeypatch):
    _says(monkeypatch)
    _envelope_published(seeded, "e-old", _ago(96))
    _fact(seeded, "f1", "Vilas Fire", estimated=True, env="e-old")
    assert build_watchlist(seeded) == []


def test_a_dated_fact_keeps_its_own_date(seeded, monkeypatch):
    _says(monkeypatch)
    _envelope_published(seeded, "e-old", _ago(96))
    _fact(seeded, "f1", "Ridge Fire", env="e-old")
    assert [c.name for c in build_watchlist(seeded)] == ["Ridge Fire"]


def test_new_starts_rank_above_wordy_stories_and_newest_comes_first(seeded, monkeypatch):
    _says(monkeypatch)
    for i in range(4):
        _fact(seeded, f"m{i}", "Big Story", kind="cause", content=f"Detail {i}.")
    _fact(seeded, "s1", "Older Start", observed=_ago(10))
    _fact(seeded, "s2", "Newer Start", observed=_ago(1))
    assert [c.name for c in build_watchlist(seeded)] == ["Newer Start", "Older Start", "Big Story"]


def test_more_sources_still_outrank_recency_within_a_group(seeded, monkeypatch):
    _says(monkeypatch)
    _source(seeded, "s2", "KTLA")
    _named_fact(seeded, "a1", "s1", "Two Source Fire")
    _named_fact(seeded, "a2", "s2", "Two Source Fire")
    _fact(seeded, "b1", "One Source Fire")
    assert build_watchlist(seeded)[0].name == "Two Source Fire"


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
