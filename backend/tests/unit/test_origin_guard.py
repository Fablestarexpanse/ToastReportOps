"""The unauthenticated loopback API must refuse browser CSRF and DNS rebinding."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toast.api.guard import install_origin_guard, origin_allowed


@pytest.fixture()
def client():
    app = FastAPI()

    @app.get("/r")
    def read():
        return {"ok": True}

    @app.post("/w")
    def write():
        return {"ok": True}

    install_origin_guard(app, frozenset({"testserver"}))
    return TestClient(app, base_url="http://127.0.0.1:8400")


@pytest.mark.parametrize(
    "origin,ok",
    [
        ("http://localhost:3005", True),
        ("http://127.0.0.1:3005", True),
        ("http://[::1]:3005", True),
        ("https://evil.example", False),
        ("http://localhost.evil.example", False),
        ("http://127.0.0.1.evil.example:3005", False),
        ("null", False),
        ("", False),
    ],
)
def test_origin_allowed(origin, ok):
    assert origin_allowed(origin) is ok


def test_no_origin_passes(client):
    assert client.post("/w").status_code == 200  # curl / server-side proxy


def test_foreign_origin_blocked_on_unsafe_methods(client):
    r = client.post("/w", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_local_ui_origin_allowed(client):
    assert client.post("/w", headers={"Origin": "http://localhost:3005"}).status_code == 200


def test_reads_with_foreign_origin_not_blocked(client):
    # blocked by the browser's own CORS rules (no CORS headers are sent)
    assert client.get("/r", headers={"Origin": "https://evil.example"}).status_code == 200


def test_rebound_host_blocked(client):
    r = client.get("/r", headers={"Host": "evil.example:8400"})
    assert r.status_code == 403
