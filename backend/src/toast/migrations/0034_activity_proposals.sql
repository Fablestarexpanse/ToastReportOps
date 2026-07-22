-- Activity-check proposals: is each "active" fire actually still burning?
--
-- Same contract as triage_proposals: the AI/rules PROPOSE, the operator
-- decides, and acting flows through the normal status endpoint. Evidence is
-- stored so the operator sees WHY, not just a verdict.
CREATE TABLE IF NOT EXISTS activity_proposals (
    incident_id TEXT PRIMARY KEY REFERENCES incidents (id),
    verdict TEXT NOT NULL CHECK (verdict IN ('close', 'keep', 'uncertain')),
    evidence TEXT NOT NULL,       -- JSON: the deterministic signals
    reasoning TEXT,               -- one sentence, LLM or rules
    confidence REAL,
    prompt_version TEXT NOT NULL,
    proposed_at TEXT NOT NULL
) STRICT;
