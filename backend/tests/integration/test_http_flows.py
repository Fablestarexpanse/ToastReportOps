"""Integration tests: real HTTP through the FastAPI app, real job queue.

The unit suite calls route functions directly; nothing proved the wiring —
lifespan, migrations at startup, router paths, request parsing, and the
API → jobs table → worker handler → DB chain. These do, against a fresh
migrated database in tmp_path.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from toast.core.db import utcnow


@pytest.fixture()
def http(tmp_path, monkeypatch):
    """The real app over HTTP, on a fresh migrated DB. Yields (client, conn)."""
    monkeypatch.setenv("TOAST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOAST_DB_PATH", str(tmp_path / "toast.db"))
    from toast.api.app import app

    with TestClient(
        app, base_url="http://127.0.0.1:8400"
    ) as client:  # enters lifespan: connect + migrations
        yield client, app.state.conn


def _article_fact(conn, fact_id, published_at):
    conn.execute(
        "INSERT OR IGNORE INTO sources (id, name, type, adapter, tier, created_at, updated_at)"
        " VALUES ('s-adj', 'Feed', 'scrape', 'rss', 4, ?, ?)",
        (utcnow(), utcnow()),
    )
    conn.execute(
        "INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,"
        " fetched_at) VALUES (?, 's-adj', ?, 'article', ?, ?, ?)",
        (
            f"e-{fact_id}",
            fact_id,
            json.dumps({"published_at": published_at}),
            fact_id,
            utcnow(),
        ),
    )
    conn.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " confidence, observed_at, ingested_at)"
        " VALUES (?, NULL, ?, 's-adj', 4, ?, 'other', 0.5, ?, ?)",
        (fact_id, f"e-{fact_id}", f"claim {fact_id}", utcnow(), utcnow()),
    )


def test_adjudication_reports_real_total_and_hides_only_old_articles_with_a_count(http):
    client, conn = http
    _article_fact(conn, "f-new", utcnow())
    _article_fact(conn, "f-undated", None)
    _article_fact(conn, "f-old", "2020-01-01T00:00:00.000Z")

    body = client.get("/adjudication", params={"limit": 1}).json()

    assert len(body["queue"]) == 1  # the page respects its limit...
    assert body["total"] == 2  # ...but the count does not, and undated facts stay
    assert body["older_hidden"] == 1


CATALOG = [
    {
        "UniqueId": "aaaa-norte",
        "Name": "Norte Fire",
        "County": "Orange",
        "Type": "Wildfire",
        "AcresBurned": 570.0,
        "PercentContained": 100.0,
        "Latitude": 33.8,
        "Longitude": -117.8,
        "IsActive": False,
        "Started": "2026-10-16T12:00:00Z",
        "Updated": "2026-10-20T12:00:00Z",
    }
]


def _stub_catalog(monkeypatch):
    monkeypatch.setattr(
        "toast.pipeline.fire_search.fetch_incidents",
        lambda adapter, *, inactive=False, year=None: CATALOG,
    )


def test_backfill_preview_writes_nothing(http, monkeypatch):
    client, conn = http
    _stub_catalog(monkeypatch)
    body = client.get("/fires/backfill", params={"year": 2026}).json()
    assert body == {"year": 2026, "listed": 1, "new": 1, "already": 0, "acres": 570}
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0


def test_backfill_refuses_without_explicit_confirmation(http, monkeypatch):
    client, conn = http
    _stub_catalog(monkeypatch)
    assert client.post("/fires/backfill", json={"year": 2026}).status_code == 400
    assert (
        client.post("/fires/backfill", json={"year": 2026, "confirm_year": 2025}).status_code == 400
    )
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0


def test_backfill_refuses_a_cross_site_request(http, monkeypatch):
    client, conn = http
    _stub_catalog(monkeypatch)
    resp = client.post(
        "/fires/backfill",
        json={"year": 2026, "confirm_year": 2026},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0


def test_backfill_imports_and_queues_a_reattribution_pass(http, monkeypatch):
    client, conn = http
    _stub_catalog(monkeypatch)
    body = client.post("/fires/backfill", json={"year": 2026, "confirm_year": 2026}).json()
    assert body == {"year": 2026, "seen": 1, "imported": 1, "already": 0}
    assert conn.execute("SELECT status FROM incidents").fetchone()[0] == "contained"
    queued = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'reattribute' AND state = 'queued'"
    ).fetchone()[0]
    assert queued == 1


def _seed_incident(conn, inc_id="i-http", **cols):
    base = {
        "id": inc_id,
        "canonical_name": "HTTP Test Fire",
        "status": "active",
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    base.update(cols)
    keys = ", ".join(base)
    conn.execute(
        f"INSERT INTO incidents ({keys}) VALUES ({', '.join('?' * len(base))})",
        tuple(base.values()),
    )
    conn.commit()


class TestDraftLifecycleOverHTTP:
    def test_review_then_approve_records_the_approver(self, http):
        client, conn = http
        _seed_incident(conn)
        conn.execute(
            """INSERT INTO drafts (id, incident_id, title, body_md, status,
                 created_at, updated_at)
               VALUES ('d-http', 'i-http', 't', 'body', 'draft', ?, ?)""",
            (utcnow(), utcnow()),
        )
        conn.commit()

        r = client.post("/drafts/d-http/status", json={"status": "needs_review"})
        assert r.status_code == 200 and r.json()["status"] == "needs_review"
        r = client.post("/drafts/d-http/status", json={"status": "approved"})
        assert r.status_code == 200
        row = conn.execute("SELECT status, approved_by FROM drafts WHERE id='d-http'").fetchone()
        assert row["status"] == "approved" and row["approved_by"]

    def test_publish_without_approval_is_refused(self, http):
        client, conn = http
        _seed_incident(conn, inc_id="i-http2")
        conn.execute(
            """INSERT INTO drafts (id, incident_id, title, body_md, status,
                 created_at, updated_at)
               VALUES ('d-http2', 'i-http2', 't', 'body', 'draft', ?, ?)""",
            (utcnow(), utcnow()),
        )
        conn.commit()
        r = client.post("/drafts/d-http2/status", json={"status": "published"})
        assert r.status_code == 409  # the state machine, enforced over the wire

    def test_rendered_markdown_is_sanitized_over_the_wire(self, http):
        client, _conn = http
        hostile = "hello <script>alert(1)</script> <b>world</b>"
        r = client.post("/render/markdown", json={"body_md": hostile})
        assert r.status_code == 200
        html = r.json()["html"]
        assert "<script" not in html and "world" in html


class TestActivityCheckThroughTheQueue:
    def test_api_enqueues_and_the_worker_auto_closes_on_official_word(self, http, monkeypatch):
        client, conn = http
        _seed_incident(conn, inc_id="i-close", calfire_id="cf-x")

        import toast.pipeline.activity as activity

        monkeypatch.setattr(
            activity,
            "_calfire_live",
            lambda c: {"cf-x": {"is_active": False, "containment": 100, "updated": None}},
        )
        monkeypatch.setattr(activity, "_wfigs_live", lambda: {})
        monkeypatch.setattr(
            activity, "load_prompt", lambda *a, **k: (_ for _ in ()).throw(Exception())
        )

        r = client.post("/incidents/activity-check")
        assert r.status_code == 200 and r.json()["job_id"]

        from toast.core.config import Settings
        from toast.queue.worker import run_once

        assert run_once(conn, "test-worker", Settings()) is True  # processed it

        row = conn.execute("SELECT status FROM incidents WHERE id='i-close'").fetchone()
        assert row["status"] == "contained"
        ev = conn.execute(
            "SELECT actor FROM incident_events WHERE incident_id='i-close'"
        ).fetchone()
        assert ev["actor"] == "calfire-official"
        job = conn.execute("SELECT state FROM jobs ORDER BY created_at DESC").fetchone()
        assert job["state"] == "done"


def test_system_status_reports_problems_and_queue(http):
    client, _conn = http
    body = client.get("/system/status").json()
    assert {"version", "ok", "problems", "queue", "hazard_profile"} <= set(body)
    assert body["hazard_profile"] == "fire"
    assert body["ok"] is (not body["problems"])
