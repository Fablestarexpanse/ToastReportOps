-- 0002 — per-source poll cursor (adapter-defined opaque string, e.g. WFIGS
-- max ModifiedOnDateTime). State, not config, so it lives in its own column.

ALTER TABLE sources ADD COLUMN cursor TEXT;
