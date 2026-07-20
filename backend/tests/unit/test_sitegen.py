"""Static site generation from published reports + incidents."""

from toast.core.db import utcnow
from toast.sitegen.build import build_site

NOW = "2026-07-20T04:00:00.000Z"
BODY = "The Flume Fire is 1,200 acres [#1].\n\n**Sources**\n\n1. CAL FIRE (T1) — ..."


def _published_draft(conn, did="d1", title="FLUME FIRE UPDATE", incident_id=None):
    conn.execute(
        "INSERT INTO drafts (id, incident_id, title, body_md, status, approved_by, approved_at,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, 'published', 'operator', ?, ?, ?)",
        (did, incident_id, title, BODY, NOW, NOW, NOW),
    )


def test_build_site_writes_index_and_report(seeded, tmp_path):
    _published_draft(seeded, incident_id="i1")
    counts = build_site(seeded, tmp_path)
    assert counts["reports"] == 1

    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "FLUME FIRE UPDATE" in index
    assert "verify" in index.lower()  # the officials notice
    assert 'href="./report/d1.html"' in index

    report = (tmp_path / "report" / "d1.html").read_text(encoding="utf-8")
    assert "1,200 acres" in report  # body rendered
    assert "Sources" in report  # provenance carried through
    assert "Published 2026-07-20 04:00 UTC" in report  # burned-in timestamp


def test_only_published_drafts_appear(seeded, tmp_path):
    _published_draft(seeded, "d1")
    seeded.execute(
        "INSERT INTO drafts (id, title, body_md, status, created_at, updated_at)"
        " VALUES ('d2', 'DRAFT ONLY', 'x', 'draft', ?, ?)",
        (utcnow(), utcnow()),
    )
    build_site(seeded, tmp_path)
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "FLUME FIRE UPDATE" in index and "DRAFT ONLY" not in index
    assert not (tmp_path / "report" / "d2.html").exists()


def test_empty_site_builds_cleanly(seeded, tmp_path):
    counts = build_site(seeded, tmp_path)
    assert counts["reports"] == 0
    assert "No published updates yet" in (tmp_path / "index.html").read_text(encoding="utf-8")
