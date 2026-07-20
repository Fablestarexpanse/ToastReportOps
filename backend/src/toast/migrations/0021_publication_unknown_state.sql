-- A publication has a fourth possible outcome, and pretending otherwise is how
-- you post the same thing to X five times.
--
-- publish_handler caught only PublishError. A bare httpx.ReadTimeout escaped it,
-- the job failed, and the queue retried up to max_attempts. X accepts a post and
-- then times out returning the response — so the retry posts it again. Five
-- identical public posts from one timeout, on an account people are meant to
-- trust during an emergency.
--
-- The honest state is 'unknown': we do not know whether it was delivered. It
-- must never auto-retry, and it must be visibly different from 'failed' —
-- 'failed' invites a re-send, and re-sending an 'unknown' is exactly the
-- duplicate we are avoiding. Only a human who has looked at the account can
-- resolve it.

CREATE TABLE publications_rebuilt (
  id TEXT PRIMARY KEY,
  draft_id TEXT NOT NULL REFERENCES drafts(id),
  channel TEXT NOT NULL,                -- site|x|facebook|rss|email|webhook:<name>
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'sent', 'failed', 'unknown')),
  external_url TEXT,
  receipt TEXT,
  sent_at TEXT
) STRICT;

INSERT INTO publications_rebuilt SELECT id, draft_id, channel, status, external_url, receipt, sent_at
  FROM publications;

DROP TABLE publications;
ALTER TABLE publications_rebuilt RENAME TO publications;
