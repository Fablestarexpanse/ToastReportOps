-- The scanner channel directory. Until now a "channel" existed only as free
-- text copied onto each captured SDR call; there was nothing to browse, arm,
-- or organize by county. This table is the operator's tuning bank:
--
--   kind 'openmhz' — a trunked system on OpenMHz (free, open API), optionally
--                    narrowed to a talkgroup set; plays in-app, near-live.
--   kind 'stream'  — a direct audio stream URL the operator supplied (their
--                    own Broadcastify Premium stream, an open icecast, …);
--                    plays in-app, live.
--   kind 'link'    — a listen page that opens in the browser (Broadcastify
--                    public feeds stay link-outs by operator decision — their
--                    terms prohibit third-party embedding).
--
-- county NULL means statewide. Channels are operator configuration (like
-- sources), not evidence — editable and deletable, no append-only trigger.

CREATE TABLE IF NOT EXISTS radio_channels (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('openmhz', 'stream', 'link')),
    county      TEXT,
    -- openmhz: the system shortName; talkgroups = JSON array of talkgroup
    -- numbers to filter to (empty/null = whole system)
    system      TEXT,
    talkgroups  TEXT,
    -- stream: the audio URL; link: the listen page URL
    url         TEXT,
    notes       TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_radio_channels_county ON radio_channels(county);
