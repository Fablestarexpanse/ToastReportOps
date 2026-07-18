-- 0001_init — Toast Report Workstation schema v1
-- Conventions: TEXT ISO-8601 UTC timestamps; ULID ids; STRICT tables; FKs ON.
-- The append-only ledger and the draft approval state machine are enforced HERE,
-- by triggers — not in application code or UI.

PRAGMA foreign_keys = ON;

-- ============ sources & trust ============
CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('api','scrape','audio','video','social','community','satellite','aircraft','weather')),
  adapter TEXT NOT NULL,              -- entry-point name, e.g. 'wfigs', 'rss'
  tier INTEGER NOT NULL CHECK (tier BETWEEN 1 AND 5),
  config JSON NOT NULL DEFAULT '{}',  -- endpoint, region hints, channel ids…
  schedule TEXT,                      -- cron or interval; NULL for stream adapters
  enabled INTEGER NOT NULL DEFAULT 1,
  robots_override INTEGER NOT NULL DEFAULT 0,   -- explicit operator opt-out only
  tos_notes TEXT,                     -- legal posture notes, reviewed_at
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE source_stats (           -- rolling performance, recomputed nightly
  source_id TEXT NOT NULL REFERENCES sources(id),
  window_days INTEGER NOT NULL,
  lead_time_median_s INTEGER,
  superseded_rate REAL,
  yield_facts_per_day REAL,
  computed_at TEXT NOT NULL,
  suggested_tier INTEGER,             -- promotion suggestion; operator approves
  PRIMARY KEY (source_id, window_days)
) STRICT;

-- ============ raw preservation ============
CREATE TABLE raw_blobs (              -- content-addressed archive index
  sha256 TEXT PRIMARY KEY,
  media_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  storage TEXT NOT NULL DEFAULT 'local',   -- local | b2
  path TEXT NOT NULL,                 -- data/raw/ab/abcd… or b2 key
  fetched_at TEXT NOT NULL,
  url TEXT
) STRICT;

CREATE TABLE envelopes (              -- every normalized ingest unit
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  external_id TEXT,                   -- source-native id for dedup
  kind TEXT NOT NULL CHECK (kind IN ('report','hotspot','article','post','audio','video','image','aircraft','weather','alert')),
  payload JSON NOT NULL,
  raw_ref TEXT REFERENCES raw_blobs(sha256),
  content_hash TEXT NOT NULL,         -- dedup key component
  fetched_at TEXT NOT NULL,
  UNIQUE (source_id, external_id, content_hash)
) STRICT;

-- ============ incidents ============
CREATE TABLE incidents (
  id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'candidate'
    CHECK (status IN ('candidate','active','contained','frozen')),
  lat REAL, lon REAL, county TEXT,
  irwin_id TEXT UNIQUE,               -- official cross-ref when known
  first_detected_fact_id TEXT,        -- provenance: who saw it first
  frozen_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE incident_aliases (
  incident_id TEXT NOT NULL REFERENCES incidents(id),
  alias TEXT NOT NULL,
  PRIMARY KEY (incident_id, alias)
) STRICT;

CREATE TABLE incident_events (        -- lifecycle + merge/split; rows never deleted
  id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL REFERENCES incidents(id),
  event TEXT NOT NULL CHECK (event IN ('created','status_change','merged_into','split_from','alias_added','frozen')),
  detail JSON NOT NULL DEFAULT '{}',  -- e.g. {"into": "<id>"}
  actor TEXT NOT NULL,                -- 'system' | 'operator'
  at TEXT NOT NULL
) STRICT;

-- ============ the fact ledger (append-only, bi-temporal) ============
CREATE TABLE facts (
  id TEXT PRIMARY KEY,
  incident_id TEXT REFERENCES incidents(id),   -- NULL while adjudicating
  envelope_id TEXT NOT NULL REFERENCES envelopes(id),
  source_id TEXT NOT NULL REFERENCES sources(id),
  tier INTEGER NOT NULL,              -- source tier AT OBSERVATION TIME (denormalized)
  content TEXT NOT NULL,              -- human-readable claim
  kind TEXT NOT NULL,                 -- acreage|containment|evac|road|resource|start|cause|damage|weather|other
  acreage REAL,
  containment_pct REAL,
  evac_status TEXT CHECK (evac_status IN (NULL,'order','warning','shelter_in_place','lifted')),
  structured JSON NOT NULL DEFAULT '{}',
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  observed_at TEXT NOT NULL,          -- when it happened (LLM-extracted)
  ingested_at TEXT NOT NULL,          -- when we received it
  lat REAL, lon REAL,
  geo_method TEXT CHECK (geo_method IN (NULL,'exif','visual','triangulated','geocoded_text','source_fixed')),
  supersedes TEXT REFERENCES facts(id),
  starred INTEGER NOT NULL DEFAULT 0, -- operator flag; never pruned
  prompt_version TEXT,
  raw_ref TEXT REFERENCES raw_blobs(sha256)
) STRICT;
CREATE INDEX facts_incident_observed ON facts(incident_id, observed_at);
CREATE INDEX facts_supersedes ON facts(supersedes);

-- APPEND-ONLY ENFORCEMENT: only incident_id (NULL -> value, adjudication) and
-- starred may ever change; deletes are forbidden outright.
CREATE TRIGGER facts_no_delete BEFORE DELETE ON facts
  BEGIN SELECT RAISE(ABORT, 'facts are append-only'); END;
CREATE TRIGGER facts_limited_update BEFORE UPDATE ON facts
  WHEN NOT ( (OLD.incident_id IS NULL AND NEW.incident_id IS NOT NULL)
             OR (NEW.starred <> OLD.starred) )
       OR NEW.id <> OLD.id OR NEW.content <> OLD.content
       OR NEW.observed_at <> OLD.observed_at OR NEW.ingested_at <> OLD.ingested_at
  BEGIN SELECT RAISE(ABORT, 'facts are append-only; use supersession'); END;
-- Frozen incidents accept only supersession corrections:
CREATE TRIGGER facts_frozen_guard BEFORE INSERT ON facts
  WHEN NEW.incident_id IN (SELECT id FROM incidents WHERE status = 'frozen')
       AND NEW.supersedes IS NULL
  BEGIN SELECT RAISE(ABORT, 'incident frozen; corrections require supersession'); END;

-- ============ media & audio ============
CREATE TABLE media (
  id TEXT PRIMARY KEY,
  raw_ref TEXT NOT NULL REFERENCES raw_blobs(sha256),
  envelope_id TEXT REFERENCES envelopes(id),
  exif JSON,
  vlm_analysis JSON,
  reverse_image JSON,
  lat REAL, lon REAL, geo_method TEXT, geo_confidence REAL,
  credit TEXT,                        -- preserved attribution
  incident_id TEXT REFERENCES incidents(id),
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE audio_clips (
  id TEXT PRIMARY KEY,
  raw_ref TEXT NOT NULL REFERENCES raw_blobs(sha256),
  source_id TEXT NOT NULL REFERENCES sources(id),
  channel TEXT,                       -- talkgroup / stream label
  started_at TEXT NOT NULL,
  duration_s REAL NOT NULL,
  transcript TEXT,
  entities JSON,                      -- extracted units, locations, commands
  incident_id TEXT REFERENCES incidents(id),
  created_at TEXT NOT NULL
) STRICT;

-- ============ drafts & publishing (state machine in DB) ============
CREATE TABLE drafts (
  id TEXT PRIMARY KEY,
  incident_id TEXT REFERENCES incidents(id),
  kind TEXT NOT NULL DEFAULT 'update',      -- update|summary|correction|retrospective
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','needs_review','approved','published','rejected')),
  window_start TEXT, window_end TEXT,       -- observed_at window used
  body_md TEXT NOT NULL,
  cited_fact_ids JSON NOT NULL DEFAULT '[]',
  share_card_ref TEXT REFERENCES raw_blobs(sha256),
  approved_by TEXT, approved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;
-- Legal transitions only; publishing REQUIRES prior approval fields:
CREATE TRIGGER drafts_state_machine BEFORE UPDATE OF status ON drafts
  WHEN NOT (
    (OLD.status = 'draft'        AND NEW.status IN ('needs_review','rejected')) OR
    (OLD.status = 'needs_review' AND NEW.status IN ('approved','draft','rejected')) OR
    (OLD.status = 'approved'     AND NEW.status IN ('published','draft'))
  ) OR (NEW.status = 'published' AND (OLD.approved_at IS NULL OR OLD.approved_by IS NULL))
  BEGIN SELECT RAISE(ABORT, 'illegal draft transition'); END;

CREATE TABLE publications (             -- one row per channel delivery
  id TEXT PRIMARY KEY,
  draft_id TEXT NOT NULL REFERENCES drafts(id),
  channel TEXT NOT NULL,                -- site|x|facebook|rss|email|webhook:<name>
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed')),
  external_url TEXT,
  receipt JSON,
  sent_at TEXT
) STRICT;

-- ============ watch board & alerts ============
CREATE TABLE candidates (
  id TEXT PRIMARY KEY,
  lat REAL, lon REAL, county TEXT,
  score REAL NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'watching'
    CHECK (state IN ('watching','corroborating','promoted','dismissed','rx_burn')),
  promoted_incident_id TEXT REFERENCES incidents(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE candidate_signals (
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  envelope_id TEXT NOT NULL REFERENCES envelopes(id),
  weight REAL NOT NULL,
  PRIMARY KEY (candidate_id, envelope_id)
) STRICT;

CREATE TABLE alert_rules (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_text TEXT NOT NULL,          -- operator's plain-English rule
  compiled JSON NOT NULL,             -- structured condition (validated)
  channels JSON NOT NULL,             -- delivery targets
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE alert_deliveries (
  id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL REFERENCES alert_rules(id),
  fired_at TEXT NOT NULL,
  matched JSON NOT NULL,
  delivered JSON
) STRICT;

-- ============ jobs ============
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  stage TEXT NOT NULL,                -- normalize|extract|geocode|attribute|corroborate|transcribe|vlm|embed|publish|build_site
  payload JSON NOT NULL,
  priority INTEGER NOT NULL DEFAULT 5,      -- 1 = urgent (active-incident audio)
  state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','running','done','failed','dead')),
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 5,
  run_after TEXT,
  claimed_by TEXT,
  claimed_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX jobs_claim ON jobs(state, priority, run_after);

-- ============ embeddings (sqlite-vec; dim matches embedding model) ============
-- Created by the migration runner only when the sqlite-vec extension is loaded:
-- CREATE VIRTUAL TABLE fact_embeddings USING vec0(fact_id TEXT PRIMARY KEY, embedding float[768]);
-- CREATE VIRTUAL TABLE clip_embeddings USING vec0(clip_id TEXT PRIMARY KEY, embedding float[768]);

CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
) STRICT;
