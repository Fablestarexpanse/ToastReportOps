"""Integration tests: real HTTP through the FastAPI app, real job queue.

The unit suite calls route functions directly; nothing proved the wiring —
lifespan, migrations at startup, router paths, request parsing, and the
API → jobs table → worker handler → DB chain. These do, against a fresh
migrated database in tmp_path.
"""

from __future__ import annotations

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
