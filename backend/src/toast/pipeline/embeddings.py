"""Fact embeddings — sqlite-vec storage, near-duplicate detection, and
cross-source corroboration.

The vec0 virtual table is created lazily on first use (the extension is a
Python package, loaded per-connection). Distance is cosine; smaller = closer.
"""

from __future__ import annotations

import sqlite3
import struct

from toast.core.config import Settings

DUP_THRESHOLD = 0.10  # cosine distance below this = near-duplicate content
CORROBORATION_THRESHOLD = 0.35  # similar-claim distance for corroboration
CORROBORATION_WINDOW_S = 12 * 3600


def serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def ensure_vec_tables(conn: sqlite3.Connection, dim: int | None = None) -> bool:
    """Load sqlite-vec and create the embeddings table. Returns False when the
    extension can't load (embeddings disabled, everything else still works)."""
    dim = dim or Settings().embedding_dim
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        return False
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS fact_embeddings USING vec0("
        f"fact_id TEXT PRIMARY KEY, embedding float[{dim}])"
    )
    return True


def store_fact_embedding(conn: sqlite3.Connection, fact_id: str, vec: list[float]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO fact_embeddings (fact_id, embedding) VALUES (?, ?)",
        (fact_id, serialize_f32(vec)),
    )


def nearest_facts(
    conn: sqlite3.Connection, vec: list[float], *, k: int = 8, exclude: str | None = None
) -> list[tuple[str, float]]:
    rows = conn.execute(
        """SELECT fact_id, distance FROM fact_embeddings
           WHERE embedding MATCH ? AND k = ? ORDER BY distance""",
        (serialize_f32(vec), k + 1),
    ).fetchall()
    return [(r["fact_id"], r["distance"]) for r in rows if r["fact_id"] != exclude][:k]


def corroborate(conn: sqlite3.Connection, fact_id: str, vec: list[float]) -> tuple[int, list[str]]:
    """Count independent-source corroborations of this fact within the
    observed_at window. Returns (corroborating_source_count, fact_ids)."""
    fact = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if fact is None:
        return 0, []
    corroborating: dict[str, str] = {}
    for other_id, distance in nearest_facts(conn, vec, k=8, exclude=fact_id):
        if distance > CORROBORATION_THRESHOLD:
            continue
        other = conn.execute("SELECT * FROM facts WHERE id = ?", (other_id,)).fetchone()
        if other is None or other["source_id"] == fact["source_id"]:
            continue  # same source can't corroborate itself
        gap = abs(
            conn.execute(
                "SELECT unixepoch(?) - unixepoch(?)", (fact["observed_at"], other["observed_at"])
            ).fetchone()[0]
        )
        if gap <= CORROBORATION_WINDOW_S:
            corroborating.setdefault(other["source_id"], other_id)
    return len(corroborating), list(corroborating.values())


def is_near_duplicate(
    conn: sqlite3.Connection, fact_id: str, vec: list[float], source_id: str
) -> str | None:
    """Same-source near-identical content → duplicate; returns the earlier fact id."""
    for other_id, distance in nearest_facts(conn, vec, k=4, exclude=fact_id):
        if distance >= DUP_THRESHOLD:
            break
        other = conn.execute("SELECT source_id FROM facts WHERE id = ?", (other_id,)).fetchone()
        if other is not None and other["source_id"] == source_id:
            return other_id
    return None
