-- Adjudication triage. Facts are append-only and `incident_id` may only go
-- NULL -> value, so a fact belonging to no tracked incident could never leave
-- the queue: the backlog could only grow. This derived table (like
-- fact_assessments) records the operator's disposition WITHOUT mutating the
-- fact — the claim itself stays exactly as observed.
CREATE TABLE fact_triage (
  fact_id TEXT PRIMARY KEY REFERENCES facts(id),
  state TEXT NOT NULL CHECK (state IN ('not_incident', 'watch', 'deferred')),
  note TEXT,
  actor TEXT NOT NULL DEFAULT 'operator',
  at TEXT NOT NULL
) STRICT;
