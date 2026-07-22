-- 2026-07-22 audit hardening.
--
-- 1) The hottest query in the program — "current value of <kind> for this
--    incident" — filters on (incident_id, kind) and orders by observed_at,
--    but the only index was (incident_id, observed_at): kind was checked
--    row-by-row. One composite index serves every latest-fact picker.
CREATE INDEX IF NOT EXISTS facts_incident_kind_observed
    ON facts (incident_id, kind, observed_at);

-- 2) incident_events promised "rows never deleted" in its column comment
--    since 0001, but nothing enforced it. Facts got triggers; the lifecycle
--    log now gets the same guarantee.
CREATE TRIGGER IF NOT EXISTS incident_events_no_delete
BEFORE DELETE ON incident_events
BEGIN
    SELECT RAISE(ABORT, 'incident_events are append-only');
END;

-- 3) The jobs table had no retention: done/dead rows accumulated forever
--    (every poll cycle enqueues). The jobs_prune stage sweeps terminal rows
--    older than the retention window; this index makes that sweep and the
--    claim query's state filter cheap as the table churns.
CREATE INDEX IF NOT EXISTS jobs_state_created
    ON jobs (state, created_at);
