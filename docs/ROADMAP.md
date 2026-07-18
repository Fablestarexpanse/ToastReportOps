# Roadmap

Plan first; the operator approves before code. Each phase ends with something
runnable and demoed. Correctness of the ledger, supersession, attribution, and
time-window logic is the whole product — those get tests first.

## Phase 0 — Foundation

**Tasks**
- Repo scaffold (this repo), `docker-compose.yml` with `desktop` and `degraded` profiles
- Config via pydantic-settings from environment; `.env.example` documenting every setting
- Migration runner + schema v1 (`0001_init.sql`)
- Jobs table + worker claim loop + exponential backoff + dead-letter
- Structured logging (structlog JSON) with secret scrubbing; `/healthz` per service
- pytest harness (in-memory + tmpfile SQLite fixtures)
- GitHub Actions CI: ruff, mypy, pytest; UI: eslint, tsc, vitest
- Litestream config (local replica target first, B2 later)
- DECISIONS.md and OPERATIONS.md seeded

**Acceptance**
- `docker compose up` brings all services healthy
- A synthetic job flows queued → running → done; retry-on-failure and dead-letter demonstrated
- CI green on GitHub
- Triggers proven by tests: fact UPDATE/DELETE rejected, illegal draft transition rejected

**Demo**: compose up; watch a test job retry and dead-letter in logs; CI badge.

## Phase 1 — Official ingest + core ledger

**Tasks**
- Adapters: WFIGS/IRWIN (ArcGIS REST), FIRMS URT (free map key), NWS CAP alerts (Atom),
  CA evacuation aggregation (Genasys/Zonehaven public layers + county feeds — **TOS flag**)
- Envelope dedup; incident auto-creation from official reports
- Fact writes with supersession on changed values (acreage/containment updates supersede prior)
- Operator UI shell in the mockup design system: top bar with mode nav + health dots,
  OPS intake stream with tier/kind/confidence filters, incident panel with ledger tab
  (bi-temporal columns, supersedes links, stale-caught banner), SOURCES table,
  read-only ARCHIVE list

**Acceptance**
- A live CA incident appears within one poll cycle of WFIGS listing it
- Changed containment produces a supersession chain, never an edit
- Stale-news detector flags `ingested_at − observed_at > threshold`
- UI shows the ledger with supersedes links

**Demo**: real statewide incidents on screen, ledger growing live; kill/restart a
worker mid-poll with no data loss.

## Phase 2 — AI sifting

**Tasks**
- LLM extractor (claims → structured facts + `observed_at` dating) with
  delimited-content prompt template, schema-validated output, versioned prompt files
- Embedding pipeline (gpu-worker) + sqlite-vec; embedding-based dedupe/corroboration
- Attribution scorer (geo + time + alias + embedding) with auto/adjudicate thresholds;
  adjudication queue UI
- PFIRS/CARB prescribed-burn matching → candidate `rx_burn` suppression
- Confidence model v1 (tier prior × corroboration × extractor self-score)

**Acceptance**
- Extractor never executes embedded instructions (adversarial fixture tests)
- ≥90% of official-source facts auto-attribute correctly on a replayed historical day
- Prescribed burns suppressed; ambiguous cases land in adjudication with one-click resolve

**Demo**: paste a news article → structured, dated, attributed facts in the ledger;
a prompt-injection fixture visibly refused.

## Phase 3 — Rich sources

**Tasks**
- Scanner audio: stream capture → Silero VAD → distil-Whisper (gpu-worker) → entity
  extraction → clip storage. Operator-picked stream set, statewide-capable (**TOS flag**)
- Article scrapers with content-hash change detection + robots.txt compliance
- Social adapters: Bluesky jetstream, Reddit API, Mastodon, RSS (X ingest deferred — see Flagged)
- ADS-B ingest (adsb.fi / airplanes.live free feeds — **flag**) + orbit/grid-pattern
  detection over incidents
- ALERTCalifornia camera sampling + smoke detection model (**TOS flag**)
- Image pipeline: EXIF → VLM visual placement → bearing triangulation →
  reverse-image check against the archive
- "Add source by URL, AI probes it" flow v1

**Acceptance**
- Scanner clip → transcript → fact in under 2 minutes during an active incident
  (priority preemption proven)
- An aircraft orbit over a fire creates a resource fact
- An image with EXIF geolocates; a recycled old-fire image is flagged by reverse-image check

**Demo**: live scanner traffic becoming ledger facts; a tanker orbit on the map.

## Phase 4 — Newsroom + publishing

**Tasks**
- Draft generator from the ledger over operator-set `observed_at` windows, locked citations
- Point-at-URL analysis: extract claims → check vs ledger → conflict flags
- Editor UI with approval workflow (DB state machine already enforced)
- Public site: Next.js static export + MapLibre/PMTiles incident maps
- Share-card generation (Pillow, burned-in timestamp)
- Publishers: Cloudflare Pages deploy, X, Facebook Page, RSS, email digest, webhook/Discord
- Supersessions/corrections visible on the public site; evac rendered verbatim with
  deep link + "verify with officials" notice

**Acceptance**
- Nothing publishes without `approved_by`/`approved_at` (trigger-tested)
- A correction supersedes visibly on the site
- Each channel adapter delivers and records a receipt; site rebuild < 60 s

**Demo**: full loop — incident update drafted, edited, approved, live on the public
site + X + Discord webhook with share card.

## Phase 5 — Watch board, weather, deployments, alerts

**Tasks**
- Candidate scoring: multi-signal clustering + corroboration state machine
- RAWS/HRRR ingest + AI fire-weather synthesis (drafted, operator-approved like everything else)
- Resource/deployment tracker fusing dispatch audio + ADS-B + official updates
- Alert rules engine: plain-English → compiled condition (validated) + delivery channels
- Archive freeze automation (contained + N days → frozen) + retrospective export
  (self-contained incident bundle)
- Degraded-mode profile deployed to a free-tier VM and drilled
- Backup restore drill documented in OPERATIONS.md and executed

**Acceptance**
- A synthetic new-start scenario walks watching → corroborating → promoted
- An alert rule fires within one poll cycle
- A frozen incident rejects non-supersession writes
- Full restore from B2 onto a clean machine succeeds

**Demo**: watch board catching a seeded new start; PSPS drill — desktop off, the
degraded sentinel keeps the site updated.

---

## Flagged — operator research/decision needed before the relevant phase

| Item | Issue | When |
|---|---|---|
| Broadcastify | TOS prohibits recording/redistribution — clips-for-analysis likely still violates. Alternatives: OpenMHz (has API), county-run streams, own SDR (RTL-SDR + trunk-recorder is the clean legal path) | before Phase 3 |
| ALERTCalifornia cameras | TOS on automated sampling unknown — needs reading; research-partnership routes exist | before Phase 3 |
| ADS-B | ADSBExchange is now paid; adsb.fi / airplanes.live free feeds have fair-use terms to verify; own receiver is an option | before Phase 3 |
| Evac aggregators (Genasys/Zonehaven) | public-layer scraping posture varies by county | before Phase 1 evac work |
| X / Twitter ingest | free read tier (~100 reads/mo) is useless; scraping violates TOS. Deferred — X is publish-only (~500 posts/mo free writes) unless a paid tier is approved | revisit anytime |
| Facebook Page API | app review required for Pages publishing — lead time; start early | before Phase 4 |
| FIRMS | free but requires map-key registration | Phase 1 setup |
