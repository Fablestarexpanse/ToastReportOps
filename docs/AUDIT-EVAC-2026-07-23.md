# Evacuation-data audit — 2026-07-23

Full audit of how evacuation notices are ingested, reconciled, and displayed,
prompted by the operator's concern about duplicates, missing zone/time detail,
and stale orders after a reduction or cancellation. Evac is the one data class
this project treats as sacred, so findings are recorded here in full.

## The core problem

Two evac channels sat side by side:

- **Cal OES zone layer** (`pipeline/evaczones.py`) — authoritative. One row per
  *elevated* zone, status/id/times/critical-text verbatim, refreshed every few
  minutes. Because a county only publishes a zone while it is elevated, a
  reduced or cancelled zone **drops out automatically** — the lifecycle is
  self-correcting at the source.
- **Free-text evac facts** (LLM extraction, `pipeline/analyze.py`) — narrative.
  Never superseded, official-tier ranked above recency, only ever incident-level.

Everything the operator acted on or published (pin colour, intel counts,
graphics cards, Telegram, drafts) read the **facts** channel, not the zones.

## Findings (all verified against live data)

| # | Sev | Finding | Live proof |
|---|-----|---------|-----------|
| E1 | Critical | Incident evac status came from a "pick-the-winner" query over facts that ranks official tier above recency and can never be superseded by a lift; incident-level, not per-zone. | BISCAR displayed **warning** while newest fact said **order** (8 h newer) and Cal OES showed a zone under Order. Little Fire: 43 live "order" + 22 live "lifted" facts coexisting; **0** evac facts ever superseded another. |
| E2 | High | `intel.py` counted every live evac fact → order/warning tallies over-counted dupes/paraphrases. | BISCAR: 23 live "order" facts for 1 zone actually under order. |
| E3 | High | A `lifted` evac fact pulsed red like an active order in the intake feed; "cancelled" was not representable. | `workstation.tsx` styled by `kind==="evac"`, ignoring status. |
| E4 | Med-High | Non-CA and unattached evac facts rendered as pink "EVACUATION" alerts. | A **Spain** evacuation was live in the feed; 78 evac facts had no incident. |
| E5 | Med | Map zone tooltip hardcoded "STILL IN EFFECT"; app did no status filtering. | `map-panel.tsx`, `evaczones.py` (`ACTIVE_STATUSES` unused). |
| E6 | Med | Drafts passed *all* evac facts regardless of tier to the LLM tagged "reproduce verbatim" — an unofficial evac claim could land in a report body. | `drafting.py:_facts_block`. |

Dedup itself (the operator's first ask) was already handled going forward by the
`write_fact` idempotence guard + intake feed collapse shipped 2026-07-23; both
apply to evac facts. Pre-guard duplicate rows are append-only and remain, but no
longer drive status or counts (see E1/E2 fix).

## Fixes (this change)

New module **`pipeline/evacstate.py`** — the single authoritative
`evac_state(incident)`: strongest active status among the fire's matched Cal OES
zones, with per-status counts and the verbatim zone list. Falls back to
**official (tier ≤ 2) facts only** where no zone has matched yet; a stale zone
snapshot (> 1 h) is not trusted. Media/community evac never paints the pin.

Rewired every reader through it:

- **E1** — `routes.list_incidents` (pin colour), `graphics.py` (card + state
  card), `drafting.py` (roundup word), `telegram_ops.py` (transition alerts, now
  firing on downgrade and on active→none = reduced/cancelled).
- **E2** — `intel.py` counts are zone-derived; added `advisory`, dropped
  `unclassified`; exposes `evac_zones` (verbatim) + `evac_status`/`evac_source`.
- **E3** — intake `FactRow`: active evac pulses, **lifted/cancelled shows a green
  "✓ LIFTED" pill and does not pulse**.
- **E4** — the pink alert is gated to CA-relevant evac (attached to a fire *or*
  official); the Spain-type stray no longer raises an alarm.
- **E5** — the zone tooltip derives "IN EFFECT" from the real status and colours
  by status; a non-active row is labelled, not painted as an order.
- **E6** — unofficial (tier > 2) evac facts are filtered out of report bodies;
  verbatim reproduction stays officials-only. They remain visible in the intel
  panel, badged REPORTED.

The intel panel now shows the authoritative **ACTIVE ZONES** list — zone id,
status, issued time, and CRITICAL_INFO verbatim — so the operator sees zones and
times directly, not buried in prose.

## Verified live

- BISCAR: status **order** from zone LAS-553; counts **1 order / 6 warnings**
  exactly matching its 7 elevated zones (was 23 orders / 8 lifts).
- Intake feed: pulsing evac rows 15 → 5 (active only); 7 lifted rows show the
  green pill, none pulse; the Spain evac does not pulse.
- Backend suite green (455 tests), ruff/tsc/eslint clean.

## Freshness / cadence (follow-up, same day)

Evac is time-sensitive, so the whole detection chain was audited for latency. The
gap: `evac_zone_sync` was **not a scheduled stage** — the authoritative Cal OES
layer only refreshed reactively (when a news evac fact fired a sync) or lazily
(when the map tab was open). If a county elevated a zone and no article mentioned
it, the pin/counts/bell/Telegram — all now zone-driven — could sit on a stale
cache.

Fix: `evac_zone_sync` is now a scheduled stage at **120 s**, enqueued at **ingest
priority (2)**, ahead of the maintenance backlog (`scheduler.py`,
`STAGE_PRIORITY`). The Cal OES layer rebuilds every ~5 min at source, so 2-min
polling reflects a new order or a reduction within ~2 min of the county
publishing it — proactively, regardless of news or which tab is open.

End-to-end latency now: county elevates → Cal OES publishes (their ~5 min) → our
sync ≤ 2 min → operator ≤ 5 s (browser bell/pin, 5 s poll) / ≤ 30 s (Telegram).
The dominant term is Cal OES's own rebuild, which no consumer can beat. The bell's
lift detection was also corrected: zone-authoritative status returns `null` (not
`"lifted"`) when the last zone clears, so the all-clear now fires on active→none.

Official evac-carrying source polls were reviewed and left as-is: CAL FIRE 120 s,
NWS CAP 300 s (NWS asks for restraint; the zone layer is the fast path).

Verified live: zone cache age dropped 246 s → 27 s after the change; sync jobs run
at priority 2.

## Not done (noted)

- The CA-relevance gate at ingest still let the Spain article through once; the
  display gate (E4) is the safety net. Tightening the extractor's evac
  CA-relevance is a deeper follow-up.
- No `cancelled` value added to the `evac_status` vocabulary — the zone layer
  handles cancellation by dropping the zone, and `lifted` covers the fact case,
  so a schema/CHECK migration was judged unnecessary churn.
