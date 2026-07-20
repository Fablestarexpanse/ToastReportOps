-- The append-only guarantee was decorative. This makes it real.
--
-- The original facts_limited_update aborted only when id/content/observed_at/
-- ingested_at changed. Everything else — tier, acreage, confidence, evac_status,
-- lat/lon, supersedes — was free the moment either allowed branch was satisfied.
-- So an ordinary adjudication UPDATE could rewrite a T4 press claim into a T1
-- agency number, with no supersession record and no trace:
--
--   UPDATE facts SET incident_id='i1', acreage=99999, tier=1 WHERE id='f1'  -- passed
--
-- Inverted here: the update is refused unless EVERY column except incident_id
-- and starred is byte-identical. A whitelist fails closed when a migration adds
-- a column; the old blacklist silently opened a new hole each time.
--
-- `IS` (not `=`) throughout: it is null-safe, so a NULL acreage compares equal
-- to a NULL acreage instead of yielding NULL and skipping the guard — the same
-- three-valued-logic trap that made the CHECK constraints toothless.

DROP TRIGGER facts_limited_update;

CREATE TRIGGER facts_limited_update BEFORE UPDATE ON facts
  WHEN NOT (
    -- incident_id: unchanged, or the one-way NULL -> value adjudication step
    (NEW.incident_id IS OLD.incident_id
     OR (OLD.incident_id IS NULL AND NEW.incident_id IS NOT NULL))
    -- everything else is frozen for the life of the row (starred is exempt)
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

-- The frozen guard was BEFORE INSERT only, which left adjudication as an open
-- back door: attaching an orphan fact to a frozen incident by UPDATE bypassed
-- it entirely. A frozen incident is a closed record; corrections to it must
-- arrive as a superseding fact, on the insert path, like everything else.
CREATE TRIGGER facts_frozen_update_guard BEFORE UPDATE ON facts
  WHEN NEW.incident_id IS NOT OLD.incident_id
       AND NEW.incident_id IN (SELECT id FROM incidents WHERE status = 'frozen')
       AND NEW.supersedes IS NULL
  BEGIN SELECT RAISE(ABORT, 'incident frozen; corrections require supersession'); END;
