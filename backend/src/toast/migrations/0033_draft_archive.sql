-- Draft archival: orthogonal to status, deliberately. A published draft's
-- immutability and the state machine stay untouched; archiving just moves it
-- out of the working queue. Nothing is lost — the ARCHIVED filter shows it.
ALTER TABLE drafts ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
