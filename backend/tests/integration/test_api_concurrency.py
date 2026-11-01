"""The API serves sync routes from a threadpool, so requests really do overlap.

Sharing one sqlite3 connection between those threads produced random 500s
("bad parameter or other API misuse"), false 404s on rows that exist, and None
where a COUNT belonged. These pin the fix: every thread has its own connection.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from toast.core.db import ThreadConnections, utcnow


@pytest.fixture()
def http(tmp_path, monkeypatch):
    monkeypatch.setenv("TOAST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOAST_DB_PATH", str(tmp_path / "toast.db"))
    from toast.api.app import app

    with TestClient(app, base_url="http://127.0.0.1:8400") as client:
        yield client, app.state.conn


def test_each_thread_gets_its_own_connection_and_keeps_it(tmp_path):
    pool = ThreadConnections(tmp_path / "t.db")
    mine = pool.get()
    assert pool.get() is mine

    theirs: list = []
    other = threading.Thread(target=lambda: theirs.append(pool.get()))
    other.start()
    other.join()
    assert theirs[0] is not mine


def test_overlapping_requests_all_succeed(http):
    client, conn = http
    conn.execute(
        "INSERT INTO incidents (id, canonical_name, status, created_at, updated_at)"
        " VALUES ('i-race', 'Race Fire', 'active', ?, ?)",
        (utcnow(), utcnow()),
    )
    paths = ["/incidents", "/queue", "/incidents/i-race/history", "/incidents/i-race/intel"]

    def hit(n: int) -> tuple[str, int]:
        path = paths[n % len(paths)]
        return path, client.get(path).status_code

    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(hit, range(400)))
    assert [r for r in results if r[1] != 200] == []
