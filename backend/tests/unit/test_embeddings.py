"""Embeddings: sqlite-vec storage, near-duplicate detection, corroboration.

Uses tiny hand-built vectors (not a model) so the vector math is deterministic.
Skips if the sqlite-vec extension can't load in this environment.
"""

import pytest

from toast.core.db import utcnow
from toast.pipeline.embeddings import (
    corroborate,
    ensure_vec_tables,
    is_near_duplicate,
    nearest_facts,
    store_fact_embedding,
)

NOW = "2026-07-19T12:00:00.000Z"
DIM = 8


@pytest.fixture
def vecdb(seeded):
    if not ensure_vec_tables(seeded, dim=DIM):
        pytest.skip("sqlite-vec extension unavailable")
    # second source so corroboration across sources is possible
    seeded.execute(
        "INSERT INTO sources (id, name, type, adapter, tier, created_at, updated_at)"
        " VALUES ('s2', 'Scanner', 'audio', 'x', 3, ?, ?)",
        (NOW, NOW),
    )
    seeded.execute(
        "INSERT INTO envelopes (id, source_id, external_id, kind, payload, content_hash,"
        " fetched_at) VALUES ('e2', 's2', 'x2', 'audio', '{}', 'h2', ?)",
        (NOW,),
    )
    return seeded


def _unit(*vals):
    v = list(vals) + [0.0] * (DIM - len(vals))
    return v


def _write(conn, fid, source_id, envelope_id, observed):
    conn.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " confidence, observed_at, ingested_at) VALUES (?, 'i1', ?, ?, 3, 'c', 'acreage',"
        " 0.7, ?, ?)",
        (fid, envelope_id, source_id, observed, utcnow()),
    )
    return fid


def test_nearest_orders_by_distance(vecdb):
    _write(vecdb, "f1", "s1", "e1", NOW)
    _write(vecdb, "f2", "s1", "e1", NOW)
    store_fact_embedding(vecdb, "f1", _unit(1.0, 0.0))
    store_fact_embedding(vecdb, "f2", _unit(0.0, 1.0))
    near = nearest_facts(vecdb, _unit(0.9, 0.1), k=2)
    assert near[0][0] == "f1" and near[0][1] < near[1][1]


def test_corroboration_counts_distinct_sources_in_window(vecdb):
    _write(vecdb, "f1", "s1", "e1", NOW)
    _write(vecdb, "f2", "s2", "e2", NOW)  # different source, ~same content/time
    v = _unit(1.0, 0.0)
    store_fact_embedding(vecdb, "f1", v)
    store_fact_embedding(vecdb, "f2", _unit(0.99, 0.01))
    n, ids = corroborate(vecdb, "f1", v)
    assert n == 1 and "f2" in ids


def test_same_source_does_not_corroborate(vecdb):
    _write(vecdb, "f1", "s1", "e1", NOW)
    _write(vecdb, "f2", "s1", "e1", NOW)  # SAME source
    v = _unit(1.0, 0.0)
    store_fact_embedding(vecdb, "f1", v)
    store_fact_embedding(vecdb, "f2", _unit(0.99, 0.01))
    n, _ = corroborate(vecdb, "f1", v)
    assert n == 0


def test_corroboration_respects_time_window(vecdb):
    _write(vecdb, "f1", "s1", "e1", NOW)
    _write(vecdb, "f2", "s2", "e2", "2026-07-25T12:00:00.000Z")  # days later
    v = _unit(1.0, 0.0)
    store_fact_embedding(vecdb, "f1", v)
    store_fact_embedding(vecdb, "f2", _unit(0.99, 0.01))
    n, _ = corroborate(vecdb, "f1", v)
    assert n == 0  # outside the 12h window


def test_near_duplicate_same_source(vecdb):
    _write(vecdb, "f1", "s1", "e1", NOW)
    _write(vecdb, "f2", "s1", "e1", NOW)
    store_fact_embedding(vecdb, "f1", _unit(1.0, 0.0))
    store_fact_embedding(vecdb, "f2", _unit(1.0, 0.0))  # identical
    assert is_near_duplicate(vecdb, "f2", _unit(1.0, 0.0), "s1") == "f1"
