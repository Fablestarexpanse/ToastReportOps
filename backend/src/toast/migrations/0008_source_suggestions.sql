-- Source discovery: domains cited by research dossiers / linked by articles
-- that we don't already follow. Operator approves (probe+add) or dismisses.
CREATE TABLE source_suggestions (
  id TEXT PRIMARY KEY,
  domain TEXT NOT NULL UNIQUE,
  sample_url TEXT NOT NULL,
  contexts TEXT NOT NULL DEFAULT '[]',   -- JSON: up to 3 "seen in …" labels
  hit_count INTEGER NOT NULL DEFAULT 1,  -- distinct URLs seen for this domain
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','approved','dismissed')),
  source_id TEXT REFERENCES sources(id), -- set when approved
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
) STRICT;
