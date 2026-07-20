-- Cache for external map layers (fire perimeters today). Keyed, TTL-checked by
-- the caller. Kept out of the ledger deliberately: this is borrowed geometry,
-- not an observation we are asserting.
CREATE TABLE map_cache (
  key TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  fetched_at TEXT NOT NULL
) STRICT;
