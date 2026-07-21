-- Perimeter progression history.
--
-- The current-perimeter cache keeps only the latest outline; the previous one
-- is discarded on every refresh. But which way a fire has RUN — last night's
-- edge against tonight's — is the story, and history only exists if we keep
-- it. One snapshot per (incident, mapped time): the feed can be re-fetched a
-- hundred times, but a new row appears only when the IR flight actually flew.
--
-- Borrowed data (WFIGS), so it lives beside map_cache, not in the ledger.
CREATE TABLE IF NOT EXISTS perimeter_history (
    id INTEGER PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents (id),
    mapped_at TEXT,             -- poly_DateCurrent: when the outline was flown
    acres REAL,                 -- poly_GISAcres at that mapping
    geometry TEXT NOT NULL,     -- GeoJSON geometry, simplified as served
    captured_at TEXT NOT NULL,
    UNIQUE (incident_id, mapped_at)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_perimeter_history_incident
    ON perimeter_history (incident_id, mapped_at);
