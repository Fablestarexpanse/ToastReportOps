-- Operator-edited prompts. The versioned file in prompts/ stays the default
-- and the fallback; an override here replaces its body at runtime so the
-- operator can change how the newsroom writes without a rebuild.
--
-- Overrides are validated on save: every placeholder the default uses must
-- still be present, or the render silently loses its facts. Reverting is a
-- delete — the file default can never be destroyed from the UI.
CREATE TABLE prompt_overrides (
  prompt_id TEXT NOT NULL,
  version TEXT NOT NULL,
  template TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (prompt_id, version)
) STRICT;
