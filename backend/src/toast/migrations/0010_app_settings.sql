-- Operator-adjustable runtime settings (key/value). Environment supplies the
-- defaults; anything set here overrides at runtime so the operator can change
-- models and display preferences without editing .env or restarting.
CREATE TABLE app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;
