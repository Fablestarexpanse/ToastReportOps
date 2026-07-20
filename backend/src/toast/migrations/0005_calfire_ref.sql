-- 0005 — CAL FIRE cross-reference id. CAL FIRE incidents key on UniqueId
-- (not IrwinID); incidents can carry either or both official references.

ALTER TABLE incidents ADD COLUMN calfire_id TEXT;
CREATE UNIQUE INDEX incidents_calfire_id ON incidents(calfire_id) WHERE calfire_id IS NOT NULL;
