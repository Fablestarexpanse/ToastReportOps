"""Fact embeddings — sqlite-vec storage, near-duplicate detection, and
cross-source corroboration.

The vec0 virtual table is created lazily on first use (the extension is a
Python package, loaded per-connection). Distance is cosine; smaller = closer.
"""

from __future__ import annotations

import json
import sqlite3
import struct

from toast.core.config import Settings

# NOTE on units: the fact_embeddings vec0 table is created without a distance_metric,
# so sqlite-vec reports plain L2 (euclidean) distance, NOT cosine distance (verified
# against the installed extension). For unit-length vectors L2 = sqrt(2 * (1 - cos)), so
# 0.10 means cosine similarity >= ~0.995 and 0.45 means >= ~0.90. The thresholds below
# were tuned empirically in these units; changing the metric or the embedding model
# means re-tuning (python -m toast.eval.model_bakeoff embeddings does it from labelled
# pairs).
DUP_THRESHOLD = 0.10  # L2 distance below this = near-duplicate content
# Tuned against the live ledger (see docs/CORROBORATION.md): with the same-kind
# guard, 0.45 captures 22 genuine same-incident matches vs 14 at 0.35 while
# admitting zero cross-kind pairs; 0.55 lets 14 cross-kind pairs in.
CORROBORATION_THRESHOLD = 0.45
CORROBORATION_WINDOW_S = 12 * 3600
CORROBORATION_K = 16  # a chatty source's own repeats must not crowd out others


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
    # delete first: vec0 ignores OR REPLACE, so a retried embed job would die on UNIQUE
    conn.execute("DELETE FROM fact_embeddings WHERE fact_id = ?", (fact_id,))
    conn.execute(
        "INSERT INTO fact_embeddings (fact_id, embedding) VALUES (?, ?)",
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


def _claimed_name(fact: sqlite3.Row) -> str:
    try:
        return (
            str(json.loads(fact["structured"] or "{}").get("incident_name") or "").strip().lower()
        )
    except (json.JSONDecodeError, TypeError):
        return ""


def _same_subject(fact: sqlite3.Row, other: sqlite3.Row) -> bool:
    """Are these two claims about the same fire?

    Corroboration means independent sources asserting the SAME thing. Semantic
    similarity alone does not establish that: wire copy like "A new wildfire was
    reported in Los Angeles County" is textually identical (distance 0.0) across
    stories about entirely different fires. Treating that as confirmation
    manufactures confidence out of boilerplate, so subject identity must be
    established from structure, never from the text alone."""
    a, b = fact["incident_id"], other["incident_id"]
    if a and b:
        return bool(a == b)
    # unattributed (watch-board) claims: only a matching claimed fire name ties
    # them together. Generic text with no named fire corroborates nothing.
    name_a, name_b = _claimed_name(fact), _claimed_name(other)
    return bool(name_a and name_a == name_b)


def corroborate(conn: sqlite3.Connection, fact_id: str, vec: list[float]) -> tuple[int, list[str]]:
    """Count independent-source corroborations of this fact within the
    observed_at window. Returns (corroborating_source_count, fact_ids)."""
    fact = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if fact is None:
        return 0, []
    corroborating: dict[str, str] = {}
    for other_id, distance in nearest_facts(conn, vec, k=CORROBORATION_K, exclude=fact_id):
        if distance > CORROBORATION_THRESHOLD:
            continue
        other = conn.execute("SELECT * FROM facts WHERE id = ?", (other_id,)).fetchone()
        if other is None or other["source_id"] == fact["source_id"]:
            continue  # same source can't corroborate itself
        if other["kind"] != fact["kind"]:
            continue  # an acreage claim does not confirm an evacuation claim
        if not _same_subject(fact, other):
            continue
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
