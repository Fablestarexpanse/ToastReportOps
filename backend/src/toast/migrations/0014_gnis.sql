-- USGS GNIS place names (public domain) — the gazetteer that gives text a
-- location. Attribution weights geo at 0.35 but almost every media fact scores
-- 0.0 there, because the extractor gets "near Loyalton" or "Arrowhead Mine
-- Road" and never coordinates. This table closes that gap, and it also lets a
-- fire NAME be resolved to a place: California fires are named after nearby
-- features (Biscar, Elephant, Dove), so a brand-new fire can be put on the map
-- before any official source lists it.
--
-- Loaded from the California DomesticNames file; ships offline once loaded, so
-- it keeps working during a PSPS outage.
CREATE TABLE gnis_features (
  feature_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  name_norm TEXT NOT NULL,          -- lowercased, punctuation-stripped
  feature_class TEXT NOT NULL,      -- Populated Place, Summit, Stream, …
  state TEXT,
  county TEXT,
  lat REAL NOT NULL,
  lon REAL NOT NULL
) STRICT;

CREATE INDEX gnis_name_norm ON gnis_features(name_norm);
CREATE INDEX gnis_name_county ON gnis_features(name_norm, county);
