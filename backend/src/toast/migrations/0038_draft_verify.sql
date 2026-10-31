-- Result of the deterministic draft verifier (pipeline/verify_draft.py).
-- NULL = never checked (mechanical roundups, older drafts, hand-written);
-- '[]' = checked and clean; otherwise a JSON list of {code, detail}.
ALTER TABLE drafts ADD COLUMN verify_issues TEXT;
