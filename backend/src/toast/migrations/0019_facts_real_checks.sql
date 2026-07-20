-- The enum CHECKs on facts constrained nothing.
--
--   CHECK (evac_status IN (NULL,'order','warning','shelter_in_place','lifted'))
--
-- `x IN (NULL, ...)` yields NULL — not FALSE — for any non-matching value, and
-- a CHECK passes on NULL. Verified against the real schema: the string
-- 'Order — Zone BUT-1043' inserts cleanly. It then falls into intel.py's
-- `unclassified` bucket, so the OPS panel reports ZERO evacuation orders while
-- an order is live. On this project that is the worst possible failure.
--
-- Correct form is `x IS NULL OR x IN (...)`. SQLite cannot alter a CHECK, so
-- this is the documented table rebuild.
--
-- Two enum corrections, both drift the broken CHECK was hiding:
--   * geo_method gains 'named_feature' — geocode.py has written it since the
--     GNIS work and geoverify.py reads it; it was never a legal value.
--   * kind gains a CHECK it never had, matching ClaimKind in llm/extract.py
--     exactly. The extractor already validates this in pydantic; the DB is now
--     the backstop it was documented to be, for every other writer too.
--
-- Live ledger surveyed before writing: 363 facts, every value already legal.

PRAGMA foreign_keys = OFF;

CREATE TABLE facts_rebuilt (
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
  observed_at TEXT NOT NULL,          -- when it happened (LLM-extracted)
  ingested_at TEXT NOT NULL,          -- when we received it
  lat REAL, lon REAL,
  geo_method TEXT CHECK (geo_method IS NULL OR
    geo_method IN ('exif','visual','triangulated','geocoded_text','source_fixed',
                   'named_feature')),
  supersedes TEXT REFERENCES facts(id),
  starred INTEGER NOT NULL DEFAULT 0, -- operator flag; never pruned
  prompt_version TEXT,
  raw_ref TEXT REFERENCES raw_blobs(sha256)
) STRICT;

INSERT INTO facts_rebuilt SELECT
  id, incident_id, envelope_id, source_id, tier, content, kind, acreage,
  containment_pct, evac_status, structured, confidence, observed_at, ingested_at,
  lat, lon, geo_method, supersedes, starred, prompt_version, raw_ref
FROM facts;

DROP TABLE facts;
ALTER TABLE facts_rebuilt RENAME TO facts;

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
