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


def test_storing_a_fact_again_replaces_its_vector(vecdb):
    """vec0 does not honor INSERT OR REPLACE, so a retried embed job died with a
    UNIQUE error and could never succeed."""
    _write(vecdb, "f1", "s1", "e1", NOW)
    store_fact_embedding(vecdb, "f1", _unit(1.0, 0.0))
    store_fact_embedding(vecdb, "f1", _unit(0.0, 1.0))
    near = nearest_facts(vecdb, _unit(0.0, 1.0), k=5)
    assert [fid for fid, _ in near] == ["f1"]
    assert near[0][1] == pytest.approx(0.0, abs=1e-6)


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


def _write_ex(
    conn,
    fid,
    source_id,
    envelope_id,
    *,
    incident_id="i1",
    kind="acreage",
    structured="{}",
    observed=NOW,
):
    conn.execute(
        "INSERT INTO facts (id, incident_id, envelope_id, source_id, tier, content, kind,"
        " structured, confidence, observed_at, ingested_at)"
        " VALUES (?, ?, ?, ?, 3, 'c', ?, ?, 0.7, ?, ?)",
        (fid, incident_id, envelope_id, source_id, kind, structured, observed, utcnow()),
    )
    return fid


def test_identical_boilerplate_about_different_fires_is_not_corroboration(vecdb):
    """Wire copy like 'A new wildfire was reported in Los Angeles County' is
    textually identical across stories about DIFFERENT fires. Counting that as
    independent confirmation manufactures confidence out of boilerplate."""
    vecdb.execute(
        "INSERT INTO incidents (id, canonical_name, status, created_at, updated_at)"
        " VALUES ('i2', 'OTHER FIRE', 'active', ?, ?)",
        (NOW, NOW),
    )
    _write_ex(vecdb, "f1", "s1", "e1", incident_id="i1")
    _write_ex(vecdb, "f2", "s2", "e2", incident_id="i2")  # different fire
    v = _unit(1.0, 0.0)
    store_fact_embedding(vecdb, "f1", v)
    store_fact_embedding(vecdb, "f2", v)  # distance 0.0 — identical text
    n, _ = corroborate(vecdb, "f1", v)
    assert n == 0


def test_different_kinds_do_not_corroborate(vecdb):
    """An acreage claim does not confirm an evacuation claim."""
    _write_ex(vecdb, "f1", "s1", "e1", kind="acreage")
    _write_ex(vecdb, "f2", "s2", "e2", kind="evac")
    v = _unit(1.0, 0.0)
    store_fact_embedding(vecdb, "f1", v)
    store_fact_embedding(vecdb, "f2", _unit(0.99, 0.01))
    n, _ = corroborate(vecdb, "f1", v)
    assert n == 0


def test_unattributed_claims_corroborate_only_on_a_matching_fire_name(vecdb):
    """Watch-board case: two sources naming the same unlisted fire genuinely
    corroborate; generic unnamed claims do not."""
    named = '{"incident_name": "Coast Fire"}'
    _write_ex(vecdb, "f1", "s1", "e1", incident_id=None, structured=named)
    _write_ex(vecdb, "f2", "s2", "e2", incident_id=None, structured=named)
    v = _unit(1.0, 0.0)
    store_fact_embedding(vecdb, "f1", v)
    store_fact_embedding(vecdb, "f2", _unit(0.99, 0.01))
    assert corroborate(vecdb, "f1", v)[0] == 1

    # …but unnamed generic claims must not
    _write_ex(vecdb, "f3", "s1", "e1", incident_id=None)
    _write_ex(vecdb, "f4", "s2", "e2", incident_id=None)
    store_fact_embedding(vecdb, "f3", _unit(0.0, 1.0))
    store_fact_embedding(vecdb, "f4", _unit(0.0, 1.0))
    assert corroborate(vecdb, "f3", _unit(0.0, 1.0))[0] == 0


def test_near_duplicate_same_source(vecdb):
    _write(vecdb, "f1", "s1", "e1", NOW)
    _write(vecdb, "f2", "s1", "e1", NOW)
    store_fact_embedding(vecdb, "f1", _unit(1.0, 0.0))
    store_fact_embedding(vecdb, "f2", _unit(1.0, 0.0))  # identical
    assert is_near_duplicate(vecdb, "f2", _unit(1.0, 0.0), "s1") == "f1"
