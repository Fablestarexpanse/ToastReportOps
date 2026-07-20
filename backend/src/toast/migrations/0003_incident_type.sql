-- 0003 — incident type from official source (WF wildfire, RX prescribed burn,
-- CX complex). RX incidents are suppressed from active operational views.

ALTER TABLE incidents ADD COLUMN incident_type TEXT;
