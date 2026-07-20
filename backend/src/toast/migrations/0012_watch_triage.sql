-- Watch-board dispositions. Candidates are clustered on the fly from
-- unattributed named-fire signals, so "I've dealt with this one" needs its own
-- record keyed by the cluster key. Facts stay untouched (append-only).
--   ignored  — not a real new start; never show again
--   archived — a real fire, but old//already over; filed away
--   keep     — reviewed and still watching (holds an LLM age verdict)
CREATE TABLE watch_triage (
  cluster_key TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN ('ignored', 'archived', 'keep')),
  verdict TEXT,                      -- JSON: LLM age-check result
  note TEXT,
  actor TEXT NOT NULL DEFAULT 'operator',
  at TEXT NOT NULL
) STRICT;
