-- Meshtastic mesh state, heard via the public MQTT broker (own nodes later).
--
-- Region logic: a node is in_region once a position inside the NorCal bbox is
-- seen for it. A gateway that is itself in-region hears its neighbours by RF,
-- so packets it reports are treated as local even before those nodes send a
-- position. Borrowed community data: T5-grade signal, never ledger fact.
CREATE TABLE IF NOT EXISTS mesh_nodes (
    node_id TEXT PRIMARY KEY,        -- !hex id
    long_name TEXT,
    short_name TEXT,
    hw_model TEXT,
    lat REAL,
    lon REAL,
    alt_m INTEGER,
    battery INTEGER,                 -- percent; 101 = powered
    voltage REAL,
    snr REAL,
    hops INTEGER,
    in_region INTEGER NOT NULL DEFAULT 0,
    last_gateway TEXT,
    first_heard TEXT NOT NULL,
    last_heard TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS mesh_messages (
    id INTEGER PRIMARY KEY,
    node_id TEXT NOT NULL,
    gateway_id TEXT,
    channel TEXT,
    text TEXT NOT NULL,
    rx_at TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_mesh_messages_rx ON mesh_messages (rx_at DESC);
CREATE INDEX IF NOT EXISTS idx_mesh_nodes_heard ON mesh_nodes (last_heard DESC);
