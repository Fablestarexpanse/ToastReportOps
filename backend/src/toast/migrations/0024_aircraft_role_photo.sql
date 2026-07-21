-- Role on each aircraft position (bomber / spotter / helo), and a photo cache.
--
-- role is derived from type + ADS-B category at write time and stored so the map
-- icon, the roster, and the observed-count all read the same classification
-- rather than three copies of the rule drifting apart.
ALTER TABLE aircraft_positions ADD COLUMN role TEXT NOT NULL DEFAULT 'other';

-- One photo per aircraft, keyed by its ICAO hex — its "assigned ID". Borrowed
-- from planespotters.net (free, attribution required), cached so a fire's roster
-- does not re-hit them every refresh. Photographer + link are kept and shown:
-- the credit rides with the image, always.
CREATE TABLE aircraft_photos (
  hex TEXT PRIMARY KEY,
  thumb_url TEXT,       -- small, for the roster
  large_url TEXT,       -- 280px, for the detail view
  photographer TEXT,
  link TEXT,            -- the photo's page on planespotters, for attribution
  found INTEGER NOT NULL DEFAULT 0,  -- 0 = looked, none exists (do not keep asking)
  fetched_at TEXT NOT NULL
) STRICT;
