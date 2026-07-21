-- A rendered incident card attached to a draft (CAS key). Optional: text
-- publishes fine without one; a publisher that supports media attaches it.
ALTER TABLE drafts ADD COLUMN graphic_sha TEXT;
