-- Mesh chat becomes a watched source. A fire-worded mesh text now feeds the
-- WATCH board as a T5 signal (same path radio and news use), so the existing
-- AI judgement in WATCH sees it. is_fire marks the message for the tab's
-- highlight/filter; watch_fact_id links to the fact it spawned.
ALTER TABLE mesh_messages ADD COLUMN is_fire INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mesh_messages ADD COLUMN watch_fact_id TEXT;

CREATE INDEX IF NOT EXISTS idx_mesh_messages_fire ON mesh_messages (is_fire, rx_at DESC);
