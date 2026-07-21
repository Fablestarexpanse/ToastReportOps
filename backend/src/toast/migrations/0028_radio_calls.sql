-- Radio calls: scanner traffic captured by the operator's own SDR.
--
-- Dispatch beats the CAL FIRE website by 20-60 minutes routinely; this is the
-- speed edge. Audio bytes live in the CAS like every other raw capture; the
-- row is the index. transcript_state is honest about the pipeline: 'pending'
-- until a local Whisper endpoint actually heard it, 'none' when transcription
-- is switched off — a transcript is never fabricated or assumed.
CREATE TABLE IF NOT EXISTS radio_calls (
    id TEXT PRIMARY KEY,
    system TEXT,                -- e.g. 'calfire-vhf', operator-labelled
    talkgroup TEXT,             -- talkgroup / channel name as captured
    freq_hz INTEGER,
    started_at TEXT NOT NULL,
    duration_s REAL,
    audio_sha TEXT NOT NULL,    -- CAS key of the audio bytes
    audio_type TEXT NOT NULL,   -- 'audio/wav' | 'audio/mp4' | ...
    transcript TEXT,
    transcript_state TEXT NOT NULL DEFAULT 'pending'
        CHECK (transcript_state IN ('pending', 'done', 'failed', 'none')),
    watch_fact_id TEXT,         -- set when a fire-worded transcript fed WATCH
    ingested_at TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_radio_calls_started ON radio_calls (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_radio_calls_state ON radio_calls (transcript_state);
