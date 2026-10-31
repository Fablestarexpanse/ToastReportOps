# OPS Incident Intelligence Panel — build spec (from concept review, 2026-07-20)

> 2026-07-22: since built — weather, resources enrich, follow, notes, and timeline all shipped.

Scope for the "clean up ops page with more data bars and info" punch-list item.
Each panel below maps onto data we already collect; several showcase things
only this system has. Ordered by win-size.

## Build now (data already in the ledger)

### 1. Incident header panel (the "Williams Fire pane") — biggest win
- **Acres / containment / started / cause / status** header grid.
  Coverage today: acreage 51 facts, containment 34, start 48, cause 4,
  plus CAL FIRE's started/status per incident.
- **+delta figures** ("+1,234 est") come straight from supersession chains —
  prior value → current value is literally what supersession stores. No chains
  in the DB at this moment only because current fires haven't changed values
  since the reset; the mechanism is built and trigger-tested. Render the delta
  whenever the latest fact of a kind supersedes a prior one.

### 2. SOURCE CONFIDENCE meter — our signature stat
- Per-incident aggregation of `effective_confidence` (fact_assessments;
  avg 0.667 across 232 assessed) + corroborating-source counts → a meter with
  "Sources: 9". No other fire tracker can show this — nobody else has a
  corroboration model.

### 3. CURRENT SITUATION bullets
- Latest non-superseded fact per kind (behavior / spotting / structures /
  resources). Pure ledger query.

### 4. TIMELINE / INCIDENT HISTORY tab
- Merge `incident_events` + the fact stream chronologically. Data's there.

### 5. EVACUATION & ROAD IMPACTS counts
- 19 evac + 2 road facts today; countable and clickable (red count badges per
  the concept are fine). **Hard rule from the mission:** the detail view
  renders evac text VERBATIM with the official deep-link and "verify with
  officials" notice — never summarized, never paraphrased.

### 6. NEW vs UPDATE badges + county tags on intake
- Derivable: fact has `supersedes` → UPDATE, else NEW; confidence % already
  shown; county from the attributed incident.

### 7. ANALYST NOTES & TASKS
- New small table (`operator_notes`: id, incident_id NULL for global, body,
  done, created_at). Genuinely useful for a single operator.

### 8. ★ FOLLOW on an incident
- Small flag on incidents; pairs with future alert rules (followed incidents
  can alert on any change, not just creation).

## Partial — real but thinner than the mockup

### WEATHER (AT INCIDENT)
- We have Red Flag / CAP facts now; the wind/RH/temp instrument cluster needs
  the planned RAWS/HRRR work (Phase 5, free keyless APIs, no blocker).
  Show what we have (CAP alerts touching the incident county) until then.

### RESOURCES ON INCIDENT
- 11 resource facts exist as text ("fourth tanker assigned"), not the
  engines/crews/dozers numeric grid. WFIGS carries `TotalIncidentPersonnel`
  and fire-behavior/fuels fields per incident — enrich **on-demand by IRWIN
  id** (single-incident fetch) without re-enabling the WFIGS backlog poller.
