-- Daily reservoir snapshots from the DWR/CDEC "RES" conditions report.
-- One row per reservoir per Pacific day (re-fetches upsert the day), so trends
-- and "up N points in a week" come from our own history, not a recomputation.
-- NULL means CDEC printed '---' (no reading) — unknown, never zero.
CREATE TABLE IF NOT EXISTS reservoir_readings (
  station TEXT NOT NULL,
  day TEXT NOT NULL,
  name TEXT NOT NULL,
  basin TEXT,
  capacity_af REAL,
  elevation_ft REAL,
  storage_af REAL,
  storage_change_af REAL,
  pct_capacity REAL,
  avg_storage_af REAL,
  pct_average REAL,
  outflow_cfs REAL,
  inflow_cfs REAL,
  captured_at TEXT NOT NULL,
  PRIMARY KEY (station, day)
) STRICT;
