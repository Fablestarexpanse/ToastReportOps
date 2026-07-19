"""SQLite connection + migration runner.

WAL mode, foreign keys ON, busy timeout for multi-service writers.
Migrations are numbered .sql files in toast/migrations, applied in order,
recorded in schema_migrations. Applying is idempotent.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

MIGRATION_RE = re.compile(r"^(\d{4})_.+\.sql$")


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def connect(db_path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """check_same_thread=False is for the API service only: FastAPI runs sync
    endpoints in a threadpool, so the startup connection is used cross-thread.
    Workers keep the default (one thread, one connection)."""
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path, timeout=30, isolation_level=None, check_same_thread=check_same_thread
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """IMMEDIATE transaction — takes the write lock up front."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _migration_files() -> list[tuple[int, str, str]]:
    """(version, name, sql) for every bundled migration, sorted."""
    out = []
    root = resources.files("toast") / "migrations"
    for entry in root.iterdir():
        m = MIGRATION_RE.match(entry.name)
        if m:
            out.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    return sorted(out)


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if row is None:
        return set()
    return {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply all pending migrations; returns versions applied this call."""
    done = applied_versions(conn)
    applied = []
    for version, _name, sql in _migration_files():
        if version in done:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (version, utcnow()),
        )
        applied.append(version)
    return applied
