-- OPS incident intelligence panel: operator notes/tasks + FOLLOW flag.
CREATE TABLE operator_notes (
  id TEXT PRIMARY KEY,
  incident_id TEXT REFERENCES incidents(id),  -- NULL = global note
  body TEXT NOT NULL,
  done INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
) STRICT;

ALTER TABLE incidents ADD COLUMN followed INTEGER NOT NULL DEFAULT 0;
