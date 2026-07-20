-- 0007 — draft lineage: a social rewrite (or future variant) records which
-- draft it was generated from, so provenance flows: facts → report → post.

ALTER TABLE drafts ADD COLUMN parent_id TEXT REFERENCES drafts(id);
