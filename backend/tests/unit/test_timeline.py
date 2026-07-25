"""The fire timeline: every real ledger record on one axis, in order, with a
single start moment and categories the UI filters by — nothing invented."""

from toast.core.db import utcnow
from toast.pipeline.timeline import fire_timeline


def _fact(db, kind, at, *, content="claim", acreage=None, containment=None, evac=None):
    from toast.core.ids import ulid

    db.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " acreage, containment_pct, evac_status, confidence, observed_at, ingested_at)"
        " VALUES (?, 'i1', 'e1', 's1', 1, ?, ?, ?, ?, ?, 0.9, ?, ?)",
        (ulid(), content, kind, acreage, containment, evac, at, utcnow()),
    )


def test_timeline_orders_categorizes_and_collapses_starts(seeded):
    # two sources both reporting the start — the timeline keeps ONE
    _fact(seeded, "start", "2026-07-01T06:00:00Z", content="fire reported A")
    _fact(seeded, "start", "2026-07-01T06:30:00Z", content="fire reported B")
    _fact(seeded, "acreage", "2026-07-01T08:00:00Z", acreage=100)
    _fact(seeded, "evac", "2026-07-01T09:00:00Z", content="Zone A ordered", evac="order")
    _fact(seeded, "containment", "2026-07-02T00:00:00Z", containment=40)

    tl = fire_timeline(seeded, "i1")

    assert tl["incident"]["name"] == "FLUME FIRE"
    assert tl["ongoing"] is True and tl["finish"] is None
    assert tl["start"] == "2026-07-01T06:00:00Z"
    # exactly one start survived the collapse
    assert tl["counts"].get("start") == 1
    cats = [e["category"] for e in tl["events"]]
    assert {"start", "acreage", "evac", "containment"} <= set(cats)
    # evac carries the official word, verbatim, in the title
    evac = next(e for e in tl["events"] if e["category"] == "evac")
    assert "EVACUATION ORDER" in evac["title"]
    # strictly chronological
    ats = [e["at"] for e in tl["events"]]
    assert ats == sorted(ats)


def test_timeline_articles_ride_at_release_time(seeded):
    _fact(seeded, "start", "2026-07-01T06:00:00Z")
    seeded.execute(
        "INSERT INTO drafts (id, incident_id, kind, status, body_md, created_at, updated_at,"
        " title) VALUES ('d1', 'i1', 'incident', 'published', '#x', ?, ?, 'Flume Fire grows')",
        (utcnow(), utcnow()),
    )
    seeded.execute(
        "INSERT INTO publications (id, draft_id, channel, status, external_url, sent_at)"
        " VALUES ('p1', 'd1', 'web', 'sent', 'https://example.org/flume', '2026-07-01T12:00:00Z')"
    )
    tl = fire_timeline(seeded, "i1")
    art = next(e for e in tl["events"] if e["category"] == "article")
    assert art["at"] == "2026-07-01T12:00:00Z"
    assert art["url"] == "https://example.org/flume"
    assert art["title"] == "Flume Fire grows"


def test_timeline_finish_when_contained(seeded):
    seeded.execute("UPDATE incidents SET status = 'contained' WHERE id = 'i1'")
    _fact(seeded, "start", "2026-07-01T06:00:00Z")
    _fact(seeded, "containment", "2026-07-05T00:00:00Z", containment=100)
    tl = fire_timeline(seeded, "i1")
    assert tl["ongoing"] is False
    assert tl["finish"] == "2026-07-05T00:00:00Z"


def test_timeline_missing_incident():
    import sqlite3

    import pytest

    from toast.core.db import apply_migrations, connect

    conn = connect(":memory:")
    apply_migrations(conn)
    with pytest.raises(ValueError):
        fire_timeline(conn, "nope")
    assert isinstance(conn, sqlite3.Connection)
