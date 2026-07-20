-- 0004 — derived, recomputable per-fact assessment. The facts row is the
-- immutable as-observed record; corroboration and effective confidence are
-- computed state and live here (mutable by design, rebuildable from scratch).

CREATE TABLE fact_assessments (
  fact_id TEXT PRIMARY KEY REFERENCES facts(id),
  corroborating_sources INTEGER NOT NULL DEFAULT 0,
  corroborated_by TEXT NOT NULL DEFAULT '[]',   -- JSON fact ids
  duplicate_of TEXT REFERENCES facts(id),       -- same-source near-duplicate
  effective_confidence REAL NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;
