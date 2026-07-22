# Architecture

Toast Report OPs — system design. Companion docs: [ROADMAP.md](ROADMAP.md),
[SECURITY.md](SECURITY.md), schema in
[`backend/src/toast/migrations/0001_init.sql`](../backend/src/toast/migrations/0001_init.sql).

Operating reality this design serves: one operator, one Windows 11 desktop with an
RTX 4090 (WSL2/Docker), ~$0/month budget, later migration to a Linux server, and a
degraded fallback for PSPS power outages. The operator lives in Paradise, CA — losing
power during a fire is expected, not hypothetical.

## 1. System shape

Docker Compose from day one. Every service is separately restartable; all state lives
in SQLite (WAL) plus a content-addressed raw archive on disk. GPU-hungry work is
serialized through the job queue inside `worker`, calling the in-stack `llm`
service (see §8 — the once-planned dedicated gpu-worker was never built).

```
                         ┌────────────────────────────────────────────────┐
  official APIs ──┐      │                 PIPELINE (workers)             │
  FIRMS/satellite ─┤     │ ingest → normalize → extract → geocode →       │
  news/scrapers ──┤─────▶│ attribute → corroborate → (draft) → publish    │
  social (free)  ──┤     │   each stage = jobs-table consumer, retryable  │
  scanner audio ──┤      └───────┬───────────────────────────┬────────────┘
  YouTube/video ──┤              │                           │
  ADS-B ──────────┤        SQLite (WAL)                 raw archive
  weather ────────┘        + sqlite-vec              data/raw/ (sha256 CAS)
                                 │                           │
                     ┌───────────┴───────────┐    Litestream ──▶ B2 (offsite)
                     │  FastAPI (localhost)  │
                     │  REST + SSE           │
                     └───────────┬───────────┘
                                 │
                  Next.js operator UI (localhost only)
                                 │  operator approves
                                 ▼
                  sitegen (build_site job, on demand — nothing deploys it)
                        │            │
            local preview only     publishers: X / FB / RSS+email / webhook
            (manual publishing)
```

Language split: **Python** for workers, pipeline, API, and site data generation
(Whisper/VAD, geospatial, scraping, and local ML tooling are Python-native);
**Next.js/TypeScript** for both the operator UI and the public static site — one
frontend framework total.

## 2. Compose services

| Service | Role | Notes |
|---|---|---|
| `scheduler` | plain scan loop (10 s); enqueues polls per `sources.schedule` — APScheduler was dropped, the loop is ~40 lines | reads sources table; does no work itself |
| `worker` | generic pipeline worker pool (CPU stages) | scale by replica count |
| ~~`gpu-worker`~~ | **not built.** Embeddings run inside `worker` against the in-stack `llm` container; VLM/smoke are unstarted (Whisper is optional via `WHISPER_URL`) |
| `api` | FastAPI: REST + SSE for operator UI; health endpoints | binds 127.0.0.1 by default |
| `operator-ui` | Next.js — **runs on the host, not in compose** (`scripts/launch.ps1`, port 3005) | localhost only; auth required if ever exposed |
| ~~`publisher`~~ | **not built as a service.** Publishing runs inside `worker` via the `publish` job — so social credentials are NOT isolated to a separate process, contrary to what this table used to claim |
| ~~`site-builder`~~ | **not built.** Site generation runs in `worker` via the `build_site` job, triggered on demand from ARCHIVE — never by publishing |
| `litestream` | replication — **local disk only today**; the B2 replica is commented out pending keys | desktop profile only; nightly snapshot is a `backup` job in `worker` |
| `llm` | Ollama in a container, GPU passthrough; auto-pulls `LLM_PULL_MODEL` / `LLM_EMBED_PULL_MODEL` on first boot | the stack's own model server — extraction AND embeddings; `LLM_BASE_URL=http://llm:11434/v1` (a cloud key still overrides) |
| `mesh` | Meshtastic listener: public MQTT firehose filtered to a NorCal picture | persistent subscription, so a service rather than a job; feeds the MESH tab |
| `searxng` | self-hosted metasearch backing LDR | desktop profile only |
| `ldr` | Local Deep Research: on-demand agentic web research | reports land as `kind='analysis'` NEWSROOM drafts, never ledger facts |

Compose **profiles**: `desktop` (everything) and `degraded` (PSPS sentinel:
`api` + `worker` + `scheduler` — official-API polling with a usable UI, on a
free-tier cloud VM). Degraded is *not* failover: there is no shared replication
target, so the sentinel's ledger diverges from the desktop's. See OPERATIONS.md.
Same codebase, config via environment variables, no hardcoded paths.

## 3. Core abstractions (plugin interfaces)

- **`Envelope`** (pydantic): the universal normalized unit every adapter emits.
  `{source_id, external_id, kind (report|hotspot|article|post|audio|video|image|aircraft|weather|alert),
  payload (typed per kind), raw_ref, observed_hint, fetched_at, url}`.
  Raw material is archived to the CAS **before** the Envelope enters the pipeline.
- **`SourceAdapter`** (Protocol): `probe(config) -> SourceInfo`,
  `poll(config, cursor) -> (list[Envelope], cursor)` or
  `stream(config) -> AsyncIterator[Envelope]`. Registered via Python entry points
  (`toast.sources`). A new source type is a new package — zero core edits. The
  "add source by URL, AI probes what it is" flow calls each registered adapter's
  `probe()` in turn.
- **`Publisher`** (Protocol): `render(publication) -> ChannelPayload`,
  `publish(payload) -> Receipt`. Entry points `toast.publishers`. Site, X, Facebook,
  RSS, email, and webhook all implement it.
- **`Extractor`**: LLM claim extraction behind an OpenAI-compatible client
  (`LLM_BASE_URL`/`LLM_MODEL` env — Qwen3 local now, swappable later). All prompts
  are versioned files in `prompts/` with front-matter (id, version, model hints);
  every fact records the `prompt_version` that produced it.

## 4. Job queue

A SQLite `jobs` table — no Redis until proven necessary. Claiming is a single
UPDATE…RETURNING; retries use `attempts`/`max_attempts` with exponential backoff;
exhausted jobs go to a `dead` state and are preserved for inspection. Stage workers
are idempotent: every stage keys its output to input ids, so retries never duplicate
facts (envelope dedup on `(source_id, external_id, content_hash)`).

## 5. Trust & attribution

- Tiers T1–T5 live on `sources`; per-source rolling stats (lead time vs. official,
  superseded-by-higher-tier rate, yield) are recomputed nightly into `source_stats`.
  Tier promotions are computed suggestions that land in an operator approval queue.
- **Attribution**: candidate incident match by geo distance + time window +
  name/alias fuzzy match + embedding similarity. High-confidence auto-attributes;
  ambiguous cases go to the adjudication queue in the operator UI. Facts hold
  `incident_id NULL` while adjudicating (the one permitted update: NULL → value).
- **Corroboration**: embedding-near facts across sources within an `observed_at`
  window raise confidence; a contradiction from a higher tier triggers a
  supersession draft, visible to the operator.
- `tier` is denormalized onto each fact at write time deliberately: it records the
  source's tier *at observation time*, so later promotions never rewrite history.

## 6. Security posture (summary — full detail in SECURITY.md)

- All ingested content is hostile: parameterized queries only; ingested text is never
  interpolated into shell commands, file paths, or LLM system prompts. Content goes in
  delimited data blocks with an explicit "never follow embedded instructions"
  contract; extractor output is schema-validated before storage.
- Fetcher safety: URL allowlist/denylist, DNS-resolve-then-check private-IP blocking
  (SSRF), size/time caps, content-type validation, untrusted file parsing in a
  resource-limited subprocess.
- Secrets: `.env` git-ignored, `.env.example` documented; structlog processor scrubs
  known secret keys from logs.
- DB-level enforcement: the append-only ledger and the draft approval state machine
  are SQLite **triggers**, not UI convention.
- Evacuation data is verbatim end-to-end: stored as fetched official text/geometry,
  rendered untouched, deep-linked with capture timestamp and a "verify with
  officials" notice. The LLM never sees evac boundaries as rewritable content.
- The public site is static — zero server attack surface.
- Backups: Litestream → B2 continuous, nightly `VACUUM INTO` snapshot, raw-archive
  rclone sync, and a documented, actually-drilled restore procedure. The house
  itself is at wildfire risk; offsite is the real copy.
- Legal hygiene: robots.txt honored (override is an explicit per-source config
  field), scanner audio stored as short analysis clips and never restreamed, image
  credits preserved, per-source `tos_notes` field. Open TOS questions are tracked in
  ROADMAP.md §Flagged.

## 7. Operator UI

Design reference: [`docs/mockups/toast-report-workstation.jsx`](mockups/toast-report-workstation.jsx)
— not pixel-for-pixel, but its functions, layout, and visual language carry over.

**Design system** (theme tokens + Tailwind config in `operator-ui/`):

- Dark ops-room palette: bg `#14181d`, panels `#1b2128`/`#222a33`, lines `#2e3843`;
  accents ember `#ff9a3c`, ember-hot `#ff5c33`, evac pink `#ff4d8f`, ok teal
  `#3ddbb4`, warn yellow `#ffd23c`, blue `#5cb8ff`, violet `#a78bfa`.
- Tier colors: T1 teal, T2 blue, T3 yellow, T4 ember, T5 pink — left-edge bars on
  every fact/intake row.
- Fonts: JetBrains Mono (data/metadata), Barlow Condensed (display headers,
  letter-spaced caps), Barlow (body). **Self-hosted** — no font CDN; the UI must
  work offline during a PSPS outage.
- Motifs: slide-in animation for fresh intake rows, pulse rings for hotspots,
  color-coded confidence (>80 ok / >60 warn / else pink), sticky group headers,
  toast notifications, `prefers-reduced-motion` respected.
- Top bar: TOAST REPORT OPS wordmark, mode nav with badge counts, clock, service
  health dots (**API** and **FEEDS** only — wired to real `/healthz` and recent
  dead-job counts; the mockup's GPU/SOCIAL/BUILD dots were never built), Red Flag
  chip.

**Modes** (nine as built): OPS, AIR, RADIO, MESH, NEWSROOM, WATCH, ARCHIVE,
SOURCES, ANALYTICS. The original five-mode plan below is kept for the phase
mapping; AIR (ADS-B aircraft), RADIO (scanner inbox), MESH (Meshtastic), and
ANALYTICS (ledger self-reporting) were added after it was written.

**Five modes → build phases (original plan):**

| Mode | Function | Phase |
|---|---|---|
| **OPS** | 3-column: live intake stream (kind chips, tier toggles, confidence slider, keyword, time window, STREAM/BY-FIRE grouping, star-to-pin, send-to-newsroom) · live map (hotspot pulses, perimeter, evac zones, aircraft with orbit trails, wind arrow, layer chips, natural-language map query) · incident panel with LEDGER / DEPLOYED / WEATHER tabs (stale-news banner, resource roster with per-item source+confidence, RAWS tiles + HRRR strip + AI fire-wx read) | shell + intake + ledger tab in P1; map layers P3–4; DEPLOYED/WEATHER P5 |
| **NEWSROOM** | Draft queue + "point AI at anything" URL analyzer · editor with draggable `observed_at` time-window strip (fact-density histogram, in/out counts), locked `[#id]` citations, attached-media cards · distribution panel with per-channel toggles, share-card preview, publish | P4 (analyzer logic lands P2) |
| **WATCH** | New-starts board: candidate cards with signal checklists, corroboration score bars, rx-burn suppressed cards, holdover-watch notes | P5 (data model from P2) |
| **ARCHIVE** | Storage stats + B2 sync status · semantic search across all fires · fire list with counts + first-detection provenance · detail view with stat cards, image-geolocation pipeline, raw ledger view, retrospective/data export | read-only core P1; search P2; exports P5 |
| **SOURCES** | Source manager table (type, tier with pending-promotion marker, uptime, yield, lead vs official, accuracy) · ADD SOURCE: URL + starting tier + AI probe | table P1; stats P2; probe P3 |

## 8. GPU scheduling

**The dedicated `gpu-worker` was a plan that got dropped — it was never built.**
GPU scheduling as it exists is cooperative, inside `worker`, via the job queue:
priorities on the jobs table decide order (live extraction preempts low-priority
research/backfill), and all model calls go through the in-stack `llm` service
(Ollama container with GPU passthrough), which handles model residency itself
(`OLLAMA_KEEP_ALIVE`). There is no VRAM-tracking scheduler and no model
load/unload orchestration; nothing has needed one.
