-- AI triage proposals for the adjudication queue.
--
-- A proposal is advice, never action: the fact is untouched until the operator
-- accepts, and acceptance flows through the SAME endpoints a manual decision
-- uses. One proposal per fact (re-proposing replaces); provenance carries the
-- prompt version like every LLM output in this system.
CREATE TABLE IF NOT EXISTS triage_proposals (
    fact_id TEXT PRIMARY KEY REFERENCES facts (id),
    action TEXT NOT NULL CHECK (action IN ('attach', 'watch', 'not_incident', 'unsure')),
    incident_id TEXT,
    incident_name TEXT,
    confidence REAL,
    reasoning TEXT,
    prompt_version TEXT NOT NULL,
    proposed_at TEXT NOT NULL
) STRICT;
