# Concept vs. built — panel-by-panel gap analysis (2026-07-20)

**Snapshot of 2026-07-20 — many gaps below have since closed; kept for history.**

Mapping the operator's reference screenshot against what exists, and — more
importantly — against what we can honestly show. Three categories:

- **BUILD NOW** — the data is already in the ledger; only UI is missing.
- **NEEDS A SOURCE** — real capability, blocked on a key, a feed, or a decision.
- **WON'T FAKE** — showing this without the underlying model or feed would be
  inventing operational information. Named explicitly so it never quietly
  becomes a mockup-shaped lie.

---

## BUILD NOW (data exists)

| Panel | Gap | Source of truth |
|---|---|---|
| **★ star / pin on intake** | schema has `facts.starred` and the append-only trigger already permits toggling it; never wired to UI | `facts.starred` |
| **Topic tags** (`#Brush #StructureThreat #Evacuation`) | not rendered | derivable from `facts.kind` + content keywords |
| **Evac breakdown** (Orders 6 · Warnings 12 · Closures 18) | we show one combined count | `facts.evac_status` = order/warning/lifted, `kind='road'` |
| **Incident number** (`CA-MEU-008756`) | not displayed | `incidents.calfire_id` / `irwin_id` |
| **ANALYTICS mode** | ~~nav item does not exist~~ **since BUILT** — the ANALYTICS mode exists | facts/day, source performance, corroboration + attribution rates, county spread — all in the ledger |
| **Notes vs Tasks tabs** | notes exist, not split | `operator_notes.done` |
| **+ NEW REPORT** shortcut | exists only inside NEWSROOM | existing draft generator |

## NEEDS A SOURCE (real, blocked)

| Panel | Blocked on | Notes |
|---|---|---|
| **Weather & fire behavior** (wind/RH/temp/Red Flag, flame length, fuels) | ~~RAWS + HRRR ingest~~ **since BUILT** — NWS forecast + red-flag zones | **free and keyless** — the single biggest honest win still available. Flame length/fuels come from fire-behavior fields WFIGS already carries. |
| **Resource tracker** (engines/crews/dozers/aircraft/tenders, committed vs available) | WFIGS on-demand enrich by IRWIN id | We have 14 free-text `resource` facts; the numeric grid needs the official record. |
| **Smoke / AQI layer** | ~~AirNow key (in OPTIONS)~~ **since BUILT** — AirNow AQI | |
| **Infrared / Satellite layers** | FIRMS key + NASA GIBS tiles | Leaflet can host GIBS directly now. |
| **Cameras** | ~~ALERTCalifornia TOS decision~~ **since BUILT** — metadata + operator-browser frames, viewer + view cones | |
| **Radio / comms log** | scanner audio — TOS + SDR decision | The mockup's dispatch log is the richest single panel and the most legally constrained. |
| **Perimeter history** | ~~NIFC perimeter feed~~ **since BUILT** | free; a polygon layer on the Leaflet map |
| **Water sources / hydrants / access points** | county GIS layers | availability varies per county |
| **Evac ZONES (polygons)** | ~~Genasys/Zonehaven TOS (audit R2)~~ **since BUILT** — Cal OES aggregation layer, no Genasys | We hold evac TEXT verbatim today; zone geometry is the gap. |

## WON'T FAKE

| Panel | Why |
|---|---|
| **PREDICTIVE SPREAD** (12h/24h, "FSPro v2.1") | Fire-spread modelling needs fuels, terrain, gridded weather and a validated model. A plausible-looking coloured blob that is not a real model is the most dangerous thing this system could display — someone could make an evacuation decision on it. If we ever do this, it will be by consuming an *official* published forecast product with its provenance shown, never by drawing our own. |
| **"LIVE" badge implying continuous feeds** | Our sources poll at intervals; the honest display is the age of the newest fact (already shown). |
| **Multi-state intake (CA/NV/OR/WA)** | The CA-relevance gate is a deliberate design decision. Expanding scope is a plan change, not a UI toggle. |

---

## Recommended order

1. **Star, tags, evac breakdown, incident number** — hours, pure UI over existing data.
2. **ANALYTICS mode** — a real one, from the ledger.
3. **RAWS/HRRR weather** — free, keyless, fills the largest visible hole.
4. **FIRMS + GIBS layers** — operator adds the key in OPTIONS; Leaflet is ready.
5. **WFIGS resource enrich** — the numeric grid.
6. Perimeters, then the TOS-gated items as decisions land.
