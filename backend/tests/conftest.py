import sqlite3

import pytest

from toast.core.db import apply_migrations, connect, utcnow
from toast.core.ids import ulid

NOW = "2026-07-19T00:00:00.000Z"


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    conn = connect(tmp_path / "test.db")
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture
def seeded(db: sqlite3.Connection) -> sqlite3.Connection:
    """DB with one source, envelope, and active incident."""
    db.execute(
        "INSERT INTO sources (id, name, type, adapter, tier, created_at, updated_at)"
        " VALUES ('s1', 'CAL FIRE', 'api', 'wfigs', 1, ?, ?)",
        (NOW, NOW),
    )
    db.execute(
        "INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,"
        " fetched_at) VALUES ('e1', 's1', 'x1', 'report', '{}', 'h1', ?)",
        (NOW,),
    )
    db.execute(
        "INSERT INTO incidents (id, canonical_name, status, created_at, updated_at)"
        " VALUES ('i1', 'FLUME FIRE', 'active', ?, ?)",
        (NOW, NOW),
    )
    return db


def insert_fact(
    conn: sqlite3.Connection,
    fact_id: str | None = None,
    incident_id: str | None = "i1",
    supersedes: str | None = None,
    observed_at: str = NOW,
) -> str:
    fact_id = fact_id or ulid()
    conn.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " confidence, observed_at, ingested_at, supersedes)"
        " VALUES (?, ?, 'e1', 's1', 1, 'claim', 'acreage', 0.9, ?, ?, ?)",
        (fact_id, incident_id, observed_at, utcnow(), supersedes),
    )
    return fact_id
