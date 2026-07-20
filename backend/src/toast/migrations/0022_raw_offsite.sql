-- Which raw blobs have reached the offsite copy.
--
-- Kept out of raw_blobs deliberately. That row describes the archived response
-- itself — its hash, type, size, where it came from. Whether a copy has been
-- shipped is a fact about our replication, not about the evidence, and it is
-- fully rebuildable by listing the bucket. Same reasoning as fact_assessments
-- and map_cache: derived state lives in its own table so the record of what we
-- fetched is never rewritten by bookkeeping.
CREATE TABLE raw_offsite (
  sha256 TEXT PRIMARY KEY REFERENCES raw_blobs(sha256),
  bytes INTEGER NOT NULL,
  uploaded_at TEXT NOT NULL
) STRICT;
