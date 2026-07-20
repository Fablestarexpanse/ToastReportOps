-- Cached weather at an incident, from the National Weather Service (free, no
-- key). Cached because the operator may open the tab repeatedly and NWS asks
-- callers not to hammer it; observations only update every ~20 minutes anyway.
CREATE TABLE incident_weather (
  incident_id TEXT PRIMARY KEY REFERENCES incidents(id),
  payload TEXT NOT NULL,        -- JSON: observation + alerts + station
  fetched_at TEXT NOT NULL
) STRICT;
