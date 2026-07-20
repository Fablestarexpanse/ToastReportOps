import json
import sqlite3

from toast.pipeline.source_discovery import (
    known_domains,
    pending_suggestions,
    scan_for_suggestions,
)

NOW = "2026-07-20T00:00:00.000Z"


def _add_analysis_draft(db: sqlite3.Connection, body: str) -> None:
    db.execute(
        "INSERT INTO drafts (id, kind, status, title, body_md, created_at, updated_at)"
        " VALUES (?, 'analysis', 'draft', 'RESEARCH — Elephant Fire', ?, ?, ?)",
        (f"d{abs(hash(body)) % 10**9}", body, NOW, NOW),
    )


def _add_article_envelope(db: sqlite3.Connection, eid: str, url: str) -> None:
    db.execute(
        "INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,"
        " fetched_at) VALUES (?, 's1', ?, 'article', ?, ?, ?)",
        (eid, eid, json.dumps({"url": url}), f"h-{eid}", NOW),
    )


def test_known_domains_come_from_source_configs(seeded):
    seeded.execute(
        "UPDATE sources SET config = ? WHERE id = 's1'",
        (json.dumps({"feed_url": "https://www.kqed.org/news/feed/"}),),
    )
    assert "kqed.org" in known_domains(seeded)


def test_scan_suggests_unknown_domains_only(seeded):
    seeded.execute(
        "UPDATE sources SET config = ? WHERE id = 's1'",
        (json.dumps({"feed_url": "https://kqed.org/feed/"}),),
    )
    _add_analysis_draft(
        seeded,
        "Coverage: https://kolotv.com/2026/07/fire-story and "
        "https://www.kqed.org/known-outlet plus https://en.wikipedia.org/wiki/Fire "
        "and again https://kolotv.com/another-story",
    )
    _add_article_envelope(seeded, "e-art", "https://sfchronicle.com/fires/story-1")
    scan_for_suggestions(seeded)

    got = {s["domain"]: s for s in pending_suggestions(seeded)}
    assert set(got) == {"kolotv.com", "sfchronicle.com"}  # known + ignored excluded
    assert got["kolotv.com"]["hit_count"] == 2
    assert "RESEARCH — Elephant Fire" in got["kolotv.com"]["contexts"][0]
    assert got["sfchronicle.com"]["contexts"] == ["via CAL FIRE"]


def test_rescan_is_idempotent_and_preserves_dismissal(seeded):
    _add_analysis_draft(seeded, "see https://kolotv.com/story")
    scan_for_suggestions(seeded)
    (sug,) = pending_suggestions(seeded)
    seeded.execute("UPDATE source_suggestions SET status = 'dismissed' WHERE id = ?", (sug["id"],))
    scan_for_suggestions(seeded)
    assert pending_suggestions(seeded) == []  # dismissed never resurfaces
    row = seeded.execute(
        "SELECT status, hit_count FROM source_suggestions WHERE domain = 'kolotv.com'"
    ).fetchone()
    assert row["status"] == "dismissed"


def test_approved_domain_becomes_known_and_drops_from_scan(seeded):
    _add_analysis_draft(seeded, "see https://kolotv.com/story")
    scan_for_suggestions(seeded)
    seeded.execute(
        "INSERT INTO sources (id, name, type, adapter, tier, config, created_at, updated_at)"
        " VALUES ('s2', 'kolotv.com', 'scrape', 'rss', 4, ?, ?, ?)",
        (json.dumps({"feed_url": "https://kolotv.com/rss"}), NOW, NOW),
    )
    seeded.execute(
        "UPDATE source_suggestions SET status='approved', source_id='s2' WHERE domain='kolotv.com'"
    )
    scan_for_suggestions(seeded)
    assert pending_suggestions(seeded) == []
