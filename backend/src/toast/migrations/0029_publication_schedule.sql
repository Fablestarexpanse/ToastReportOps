-- Scheduled, channel-selected release.
--
-- not_before: hold a queued publication until this time (NULL = immediately).
-- enqueued: whether a publish job has been created for this row. Historic rows
-- were always enqueued at insert, so the default is 1; the scheduled path
-- inserts 0 and the release_due sweep flips it exactly once — the flag is the
-- idempotency guard that keeps a sweep from double-posting.
ALTER TABLE publications ADD COLUMN not_before TEXT;
ALTER TABLE publications ADD COLUMN enqueued INTEGER NOT NULL DEFAULT 1;
