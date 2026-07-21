-- Military flag on each aircraft position (airplanes.live dbFlags bit 0).
--
-- Asked for directly: tell a fire plane from a military one. National Guard
-- MAFFS C-130s and military helicopters DO fly fires, so 'military' is a badge
-- surfaced alongside the role, not a disqualifier — the operator sees both.
ALTER TABLE aircraft_positions ADD COLUMN military INTEGER NOT NULL DEFAULT 0;
