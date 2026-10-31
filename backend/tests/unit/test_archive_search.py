"""Archive search: index what Toast holds, return only capped snippets and windows,
and never let typed input act as a search command or a file path."""

from __future__ import annotations

import pytest

from toast.core.config import Settings
from toast.core.db import utcnow
from toast.core.ids import ulid
from toast.pipeline import archive_search as arch


def fact(conn, content, *, kind="evac", ingested=None):
    fid = ulid()
    conn.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " confidence, observed_at, ingested_at) VALUES (?, 'i1', 'e1', 's1', 1, ?, ?, 0.9, ?, ?)",
        (fid, content, kind, utcnow(), ingested or utcnow()),
    )
    return fid


def page(
    conn,
    tmp_path,
    body: bytes,
    media="text/html; charset=utf-8",
    rel=None,
    url="https://news.example/a",
):
    sha = ulid().lower()
    rel = rel or f"raw/{sha[:2]}/{sha}"
    target = tmp_path / rel
    if ".." not in rel:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    conn.execute(
        "INSERT INTO raw_blobs (sha256, media_type, size_bytes, path, fetched_at, url)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (sha, media, len(body), rel, utcnow(), url),
    )
    return sha


@pytest.fixture()
def s(tmp_path):
    return Settings(toast_data_dir=tmp_path)


def test_facts_are_found_with_a_highlighted_snippet(seeded, s):
    fact(seeded, "EVACUATION ORDER for Zone BUT-1043 due to the Flume Fire")
    fact(seeded, "Flume Fire grew to 1,240 acres overnight", kind="acreage")
    arch.index_pending(seeded, s)
    hits = arch.search(seeded, "evacuation order")
    assert len(hits) == 1 and hits[0]["kind"] == "fact"
    assert "[EVACUATION]" in hits[0]["snippet"] and hits[0]["title"] == "FLUME FIRE"
    assert hits[0]["source"].endswith("(T1)")


def test_indexing_is_incremental_and_does_not_duplicate(seeded, s):
    fact(seeded, "Red flag warning issued for Butte County")
    assert arch.index_pending(seeded, s)["fact"] == 1
    assert arch.index_pending(seeded, s)["fact"] == 0
    fact(seeded, "Second red flag warning")
    assert arch.index_pending(seeded, s)["fact"] == 1
    assert len(arch.search(seeded, "red flag")) == 2


def test_an_edited_draft_is_reindexed_not_duplicated(seeded, s):
    did = ulid()
    seeded.execute(
        "INSERT INTO drafts (id, kind, status, title, body_md, created_at, updated_at)"
        " VALUES (?, 'update', 'draft', 'Flume update', 'Crews hold the line', ?, ?)",
        (did, utcnow(), "2026-07-19T01:00:00.000Z"),
    )
    arch.index_pending(seeded, s)
    seeded.execute(
        "UPDATE drafts SET body_md = 'Crews lost the line near Magalia', updated_at = ?"
        " WHERE id = ?",
        ("2026-07-19T02:00:00.000Z", did),
    )
    arch.index_pending(seeded, s)
    assert arch.search(seeded, "Magalia")[0]["doc_id"] == f"draft:{did}"
    assert arch.search(seeded, "hold") == []  # the old text is gone
    assert arch.status(seeded)["documents"]["draft"] == 1


def test_a_radio_transcript_added_later_is_still_indexed(seeded, s):
    cid = ulid()
    seeded.execute(
        "INSERT INTO radio_calls (id, started_at, audio_sha, audio_type, ingested_at)"
        " VALUES (?, ?, 'a1', 'audio/wav', ?)",
        (cid, utcnow(), utcnow()),
    )
    arch.index_pending(seeded, s)  # nothing to index yet
    seeded.execute(
        "UPDATE radio_calls SET transcript = 'Engine 12 requesting a second alarm',"
        " transcript_state = 'done' WHERE id = ?",
        (cid,),
    )
    assert arch.index_pending(seeded, s)["radio"] == 1
    assert arch.search(seeded, "second alarm", kind="radio")[0]["ref"] == cid


def test_pages_are_indexed_but_json_feeds_and_scripts_are_not(seeded, s, tmp_path):
    page(
        seeded,
        tmp_path,
        b"<html><script>var leak='zebra'</script><p>Mandatory evacuation near Concow</p></html>",
    )
    page(seeded, tmp_path, b'{"incidents":[{"name":"Concow Fire"}]}', media="application/json")
    arch.index_pending(seeded, s)
    hits = arch.search(seeded, "Concow")
    assert [h["kind"] for h in hits] == ["page"] and hits[0]["source"] == "news.example"
    assert arch.search(seeded, "zebra") == []  # <script> text never reaches the index


def test_a_raw_path_outside_the_data_dir_is_never_read(seeded, s, tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("TOP SECRET password hunter2")
    page(seeded, tmp_path, b"", media="text/plain", rel="../secret.txt")
    arch.index_pending(seeded, s)
    assert arch.search(seeded, "hunter2") == []


def test_oversized_pages_are_skipped(seeded, s, tmp_path, monkeypatch):
    monkeypatch.setattr(arch, "MAX_PAGE_BYTES", 100)
    page(seeded, tmp_path, b"<p>" + b"wildfire " * 50 + b"</p>")
    arch.index_pending(seeded, s)
    assert arch.search(seeded, "wildfire") == []


@pytest.mark.parametrize(
    "typed",
    [
        "body: secret",  # a column filter
        "evac* NEAR(fire, 2)",  # an operator
        'unbalanced "quote',
        "OR AND NOT",
        "title:x OR body:y",
        "^start",
    ],
)
def test_typed_search_syntax_is_inert(seeded, s, typed):
    fact(seeded, "evacuation near the fire")
    arch.index_pending(seeded, s)
    arch.search(seeded, typed)  # must not raise an FTS syntax error


def test_phrases_and_prefixes_work():
    assert arch.build_query('"evacuation order" Zone') == '"evacuation order" "Zone"'
    assert arch.build_query("evac*") == '"evac"*'
    with pytest.raises(ValueError):
        arch.build_query("  ***  ")


def test_results_are_capped(seeded, s):
    for i in range(60):
        fact(seeded, f"wildfire report number {i} " + "word " * 200)
    arch.index_pending(seeded, s)
    hits = arch.search(seeded, "wildfire", limit=500)
    assert len(hits) == arch.MAX_HITS
    assert all(len(h["snippet"]) <= arch.SNIPPET_CHARS for h in hits)


def test_read_returns_a_capped_window_around_the_term(seeded, s):
    fid = fact(seeded, "filler " * 600 + "the Magalia evacuation center opened " + "more " * 600)
    arch.index_pending(seeded, s)
    doc = arch.read(seeded, f"fact:{fid}", around="Magalia", max_chars=99_999)
    assert len(doc["text"]) == arch.MAX_READ_CHARS and "Magalia" in doc["text"]
    assert doc["truncated"] and doc["total_chars"] > arch.MAX_READ_CHARS
    with pytest.raises(KeyError):
        arch.read(seeded, "fact:NOPE")


def test_rebuild_continues_past_batches_with_nothing_to_index(seeded, s, tmp_path, monkeypatch):
    monkeypatch.setitem(arch.BATCH, "page", 2)
    for _ in range(5):  # five JSON feeds: whole batches that index nothing
        page(seeded, tmp_path, b"{}", media="application/json")
    page(seeded, tmp_path, b"<p>the needle article</p>")
    seeded.commit()
    totals = {k: 0 for k in arch.KINDS}
    while True:
        got = arch.index_pending(seeded, s)
        totals = {k: totals[k] + got[k] for k in arch.KINDS}
        if not got["more"]:
            break
    assert totals["page"] == 1 and arch.search(seeded, "needle")


def test_rebuild_starts_over(seeded, s):
    fact(seeded, "Oroville spillway inspection")
    arch.index_pending(seeded, s)
    arch.rebuild(seeded)
    assert arch.status(seeded)["documents"]["fact"] == 0
    arch.index_pending(seeded, s)
    assert arch.search(seeded, "spillway")


def test_missing_fts5_is_reported_not_fatal(seeded, monkeypatch):
    def no_fts(conn):
        raise arch.SearchUnavailable("no FTS5 in this SQLite")

    monkeypatch.setattr(arch, "ensure_index", no_fts)
    with pytest.raises(arch.SearchUnavailable):
        arch.search(seeded, "fire")
    monkeypatch.undo()
    assert arch.status(seeded)["available"] is True


def test_http_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("TOAST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOAST_DB_PATH", str(tmp_path / "toast.db"))
    from toast.api.app import app

    with TestClient(app, base_url="http://127.0.0.1:8400") as client:
        conn = app.state.conn
        conn.execute(
            "INSERT INTO sources (id, name, type, adapter, tier, created_at, updated_at)"
            " VALUES ('s1', 'CAL FIRE', 'api', 'calfire', 1, ?, ?)",
            (utcnow(), utcnow()),
        )
        conn.execute(
            "INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,"
            " fetched_at) VALUES ('e1', 's1', 'x', 'report', '{}', 'h', ?)",
            (utcnow(),),
        )
        conn.execute(
            "INSERT INTO incidents (id, canonical_name, status, created_at, updated_at)"
            " VALUES ('i1', 'PARK FIRE', 'active', ?, ?)",
            (utcnow(), utcnow()),
        )
        fid = fact(conn, "Shelter open at the Chico fairgrounds")
        conn.commit()
        arch.index_pending(conn, Settings(toast_data_dir=tmp_path))
        hits = client.get("/archive/search", params={"q": "fairgrounds"}).json()["results"]
        assert hits[0]["doc_id"] == f"fact:{fid}"
        doc = client.get("/archive/doc", params={"doc_id": hits[0]["doc_id"]}).json()
        assert "fairgrounds" in doc["text"]
        assert client.get("/archive/search", params={"q": "!!!"}).status_code == 422
        assert client.get("/archive/search", params={"q": "x", "limit": 999}).status_code == 422
        assert client.get("/archive/doc", params={"doc_id": "fact:NOPE"}).status_code == 404
        assert client.get("/archive/status").json()["available"] is True
