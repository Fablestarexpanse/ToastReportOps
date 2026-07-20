-- Operator-entered API credentials.
--
-- Values here OVERRIDE the matching .env variable at runtime, so keys can be
-- added from the UI without editing files or restarting containers.
--
-- SECURITY: values are never returned by the API (masked last-4 only) and are
-- STRIPPED FROM BACKUP SNAPSHOTS (see toast/backup.py) — a snapshot leaves the
-- machine for B2, and secrets must not. A restore therefore comes back with no
-- credentials; that is intended, and documented in docs/BACKUP-RESTORE.md.
CREATE TABLE credentials (
  name TEXT PRIMARY KEY,             -- e.g. 'FIRMS_MAP_KEY'
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

-- Last live connection test per service, so the operator can see at a glance
-- whether a key actually works rather than merely being present.
CREATE TABLE credential_checks (
  service TEXT PRIMARY KEY,          -- e.g. 'bluesky'
  status TEXT NOT NULL CHECK (status IN ('ok', 'rejected', 'error', 'unconfigured')),
  message TEXT,
  checked_at TEXT NOT NULL
) STRICT;
