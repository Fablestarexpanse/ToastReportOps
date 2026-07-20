-- Timestamps the database cannot do arithmetic on are refused at the door.
--
-- `observed_at` is written by an LLM. Given enough articles it will emit
-- "July 19, 2026" or "yesterday evening". SQLite's unixepoch() returns NULL for
-- those, and NULL propagates without complaint:
--
--   * attribution.score_candidates does abs(unixepoch(a) - unixepoch(b))
--     -> abs(None) -> TypeError, inside the extract job (retries, dies), and
--     then GET /adjudication 500s for EVERY queued fact, because the route
--     scores all of them. One bad date darkens the whole queue.
--   * watch.py's recency window evaluates NULL, so the fact silently never
--     appears on the watch board at all.
--
-- core/timestamps.py normalizes at the extractor and again at the ledger
-- boundary. This is the third layer: the one that holds even if a future writer
-- forgets, which is exactly the argument the schema makes for every other rule.

PRAGMA foreign_keys = OFF;

CREATE TABLE facts_ts_checked (
  id TEXT PRIMARY KEY,
  incident_id TEXT REFERENCES incidents(id),   -- NULL while adjudicating
  envelope_id TEXT NOT NULL REFERENCES envelopes(id),
  source_id TEXT NOT NULL REFERENCES sources(id),
  tier INTEGER NOT NULL,              -- source tier AT OBSERVATION TIME (denormalized)
  content TEXT NOT NULL,              -- human-readable claim
  kind TEXT NOT NULL CHECK (kind IN ('start','acreage','containment','evac','road',
                                     'resource','damage','cause','weather','other')),
  acreage REAL,
  containment_pct REAL,
  evac_status TEXT CHECK (evac_status IS NULL OR
    evac_status IN ('order','warning','shelter_in_place','lifted')),
  structured TEXT NOT NULL DEFAULT '{}',
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  -- A timestamp SQLite cannot parse returns NULL from unixepoch() and then
  -- propagates silently: staleness, supersession order, the attribution time
  -- score and the watch window all quietly misbehave. Refuse it at the door.
  observed_at TEXT NOT NULL CHECK (unixepoch(observed_at) IS NOT NULL),
  ingested_at TEXT NOT NULL CHECK (unixepoch(ingested_at) IS NOT NULL),
  lat REAL, lon REAL,
  geo_method TEXT CHECK (geo_method IS NULL OR
    geo_method IN ('exif','visual','triangulated','geocoded_text','source_fixed',
                   'named_feature')),
  supersedes TEXT REFERENCES facts(id),
  starred INTEGER NOT NULL DEFAULT 0, -- operator flag; never pruned
  prompt_version TEXT,
  raw_ref TEXT REFERENCES raw_blobs(sha256)
) STRICT;

INSERT INTO facts_ts_checked SELECT
  id, incident_id, envelope_id, source_id, tier, content, kind, acreage,
  containment_pct, evac_status, structured, confidence, observed_at, ingested_at,
  lat, lon, geo_method, supersedes, starred, prompt_version, raw_ref
FROM facts;

DROP TABLE facts;
ALTER TABLE facts_ts_checked RENAME TO facts;

CREATE INDEX facts_incident_observed ON facts(incident_id, observed_at);
CREATE INDEX facts_supersedes ON facts(supersedes);

-- Triggers do not survive a table rebuild. Recreated verbatim from 0001 + 0018;
-- the append-only guarantee must never have a gap, including inside a migration.
CREATE TRIGGER facts_no_delete BEFORE DELETE ON facts
  BEGIN SELECT RAISE(ABORT, 'facts are append-only'); END;

CREATE TRIGGER facts_limited_update BEFORE UPDATE ON facts
  WHEN NOT (
    (NEW.incident_id IS OLD.incident_id
     OR (OLD.incident_id IS NULL AND NEW.incident_id IS NOT NULL))
    AND NEW.id IS OLD.id
    AND NEW.envelope_id IS OLD.envelope_id
    AND NEW.source_id IS OLD.source_id
    AND NEW.tier IS OLD.tier
    AND NEW.content IS OLD.content
    AND NEW.kind IS OLD.kind
    AND NEW.acreage IS OLD.acreage
    AND NEW.containment_pct IS OLD.containment_pct
    AND NEW.evac_status IS OLD.evac_status
    AND NEW.structured IS OLD.structured
    AND NEW.confidence IS OLD.confidence
    AND NEW.observed_at IS OLD.observed_at
    AND NEW.ingested_at IS OLD.ingested_at
    AND NEW.lat IS OLD.lat
    AND NEW.lon IS OLD.lon
    AND NEW.geo_method IS OLD.geo_method
    AND NEW.supersedes IS OLD.supersedes
    AND NEW.prompt_version IS OLD.prompt_version
    AND NEW.raw_ref IS OLD.raw_ref
  )
  BEGIN SELECT RAISE(ABORT, 'facts are append-only; use supersession'); END;

CREATE TRIGGER facts_frozen_guard BEFORE INSERT ON facts
  WHEN NEW.incident_id IN (SELECT id FROM incidents WHERE status = 'frozen')
       AND NEW.supersedes IS NULL
  BEGIN SELECT RAISE(ABORT, 'incident frozen; corrections require supersession'); END;

CREATE TRIGGER facts_frozen_update_guard BEFORE UPDATE ON facts
  WHEN NEW.incident_id IS NOT OLD.incident_id
       AND NEW.incident_id IN (SELECT id FROM incidents WHERE status = 'frozen')
       AND NEW.supersedes IS NULL
  BEGIN SELECT RAISE(ABORT, 'incident frozen; corrections require supersession'); END;

PRAGMA foreign_keys = ON;
