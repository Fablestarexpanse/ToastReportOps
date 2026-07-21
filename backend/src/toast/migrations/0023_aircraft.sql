-- Aircraft positions observed near active fires (airplanes.live, free, no key).
--
-- Borrowed geometry, not ledger truth — like map_cache and perimeters. An ADS-B
-- return is a position in the sky at a moment; whether it is an air tanker is a
-- guess from altitude, speed and type, never an asserted fact. So this lives in
-- its own table, is pruned aggressively, and never becomes a `facts` row.
--
-- Only low aircraft are stored (the poller filters >15,000 ft): an airliner
-- passing over a fire at cruise is not air operations, and keeping it would bury
-- the tankers and helos under transient noise.
CREATE TABLE aircraft_positions (
  hex TEXT NOT NULL,             -- ICAO 24-bit address; identity across time
  incident_id TEXT NOT NULL REFERENCES incidents(id),
  flight TEXT,                   -- callsign, e.g. TANKER 914, or blank
  type TEXT,                     -- ICAO type code, e.g. S2, H60, C130
  descr TEXT,                    -- human description, e.g. "Sikorsky UH-60"
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  alt_ft INTEGER,                -- barometric altitude
  gs REAL,                       -- ground speed, knots
  track REAL,                    -- heading
  fire_ops INTEGER NOT NULL DEFAULT 0,  -- heuristic: low + slow / firefighting type
  seen_at TEXT NOT NULL
) STRICT;

-- current position per aircraft, and per-incident trail queries
CREATE INDEX aircraft_recent ON aircraft_positions(incident_id, seen_at);
CREATE INDEX aircraft_hex ON aircraft_positions(hex, seen_at);
