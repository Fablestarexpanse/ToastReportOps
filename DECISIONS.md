# Decisions

Architectural decision log. Newest first. Each entry: context → decision → why.

## 2026-07-20 — Audit: three DB guarantees were decorative, and are now real

Three separate rules the schema *claimed* to enforce did not. Found by an audit
that ran the statements instead of reading the comments.

- **Append-only was not append-only.** `facts_limited_update` only aborted when
  id/content/observed_at/ingested_at changed, so any other column could ride
  along with a legal change: `UPDATE facts SET incident_id='i', acreage=99999,
  tier=1` rewrote a T4 press claim into a T1 agency number with no supersession
  record. Inverted to a whitelist — refused unless all 20 other columns are
  byte-identical. A whitelist fails closed when a migration adds a column; the
  blacklist silently opened a new hole each time. (0018)
- **`CHECK (x IN (NULL, ...))` constrains nothing** — `IN` with NULL yields NULL,
  and CHECK passes on NULL. `evac_status = 'Order — Zone BUT-1043'` stored fine
  and then counted as *unclassified*, so the panel would report zero evacuation
  orders while an order was live. Rebuilt as `x IS NULL OR x IN (...)`. (0019)
- **Timestamps are enforced now too.** An LLM writes `observed_at`; unixepoch()
  returns NULL for prose, and NULL propagated silently into every time window.
  Normalized at the extractor and the ledger, with a DB CHECK behind both. (0020)

**The general lesson, recorded because it will recur:** a guarantee stated in a
comment is a guess. Every DB-level rule this project relies on now has a test
that attempts the violation and asserts the refusal.

## 2026-07-20 — No automatic publishing to any website (supersedes the entry below)

- **Operator decision**: reports are published by hand until Toast Report is
  wired into Brian's own site. Nothing auto-deploys.
- **Cloudflare removed entirely** — config, docs, and the deploy step. There is
  no production host and no CF token to acquire.
- `approved → published` fans out to *social* channels only (credential-gated).
  The static site is still generated, but **on demand from ARCHIVE**, never as a
  side effect of publishing — `POST /site/build`, previewed at `/site/`.
- Why it matters beyond plumbing: publishing is the one irreversible action in
  the system. Keeping it manual keeps a human between the ledger and the public.

## 2026-07-20 — Active research layer (Local Deep Research + SearXNG)

- **LDR (MIT) + self-hosted SearXNG = active hunting** to complement our
  passive feeds: agentic multi-engine web research on our own Ollama, no paid
  APIs. Triggered from WATCH candidates and the OPS incident panel.
- **Ledger boundary (non-negotiable)**: LDR reports land as `kind='analysis'`
  drafts for operator review — never as ledger facts. Facts enter only via
  point-at-URL over LDR's cited sources (validated, raw-preserved, CA-gated).
- **Non-blocking job design**: `deep_research` starts a run; `deep_research_poll`
  re-enqueues itself every ~30 s (hop-capped ~30 min) so a multi-minute research
  run never blocks the single worker's live extraction. Low priority (8).
- **LDR quirks learned**: model must be pinned in the start payload (its
  settings default is unset); the registration form is browser-only (one-time
  manual step, documented in OPERATIONS); the login rate-limiter punishes fast
  retries, so rate-limited jobs park for 10 min instead of burning attempts;
  SearXNG needs `formats: [html, json]` for LDR's API queries.

## 2026-07-20 — Public static site + publish wiring (SUPERSEDED — see above)

> The `site` publish channel and the Cloudflare deploy described here were
> removed the same day. Kept for the reasoning about static output and
> immutable published drafts, which still holds.

- **The site is the no-credential publish channel.** `approved → published` now
  succeeds (was a blanket 501): it records a `publications` row (channel `site`)
  and enqueues a `build_site` job. External channels (X/FB/email/webhook) stay
  deferred — they're separate publisher adapters needing credentials, not
  triggered by the site publish.
- **Static HTML, zero server surface** (mission requirement). `sitegen.build`
  renders published reports + active incidents to plain HTML/CSS (no JS) —
  index + per-report pages, markdown body, the report's Sources section,
  burned-in timestamp, and a "verify with officials" notice on every page. The
  output dir (`data/site/`) is what deploys to Cloudflare Pages.
- **Deploy deferred, not the generation.** The site builds and previews locally
  (`/site/` served by the API); only the `wrangler pages deploy` step needs a CF
  token (documented in OPERATIONS.md).
- **Published drafts are immutable** (edit returns 409) — matches the append-only
  posture; corrections are new reports, not edits.
- The Watch Board and this close the mission loop: ingest → verify → watch →
  draft → approve → publish, all local, human-in-the-loop.

## 2026-07-20 — More media sources + headline-fallback extraction

- **Breadth over any single feed.** Seeded a curated CA wildfire media set
  (InciWeb national feed, Google News queries for CA wildfire / evacuation /
  Butte County, KQED, ABC10 Sacramento, Wildfire Today) so facts get
  cross-checked across independent sources. Operators keep adding via the probe.
- **Headline-fallback extraction.** The extract stage now prefers the full
  article but falls back to the RSS entry's title+summary when the page can't be
  fetched (robots-blocked aggregators like Google News, paywalls, errors). A
  headline like "Biscar Fire burns 12,338 acres in Lassen County" still yields a
  cross-check fact. Logged with mode=full|headline.
- **The CA-relevance gate is the safety net** for broad feeds — non-CA headlines
  produce zero facts, so we can subscribe widely without polluting the ledger.
- **Aggregator feeds are capped** (`max_entries`) and polled less often
  (15–30 min) so one feed can't flood the queue; each headline is still one LLM
  call, serialized through the worker.
- Reddit RSS returns 429 to default clients (rate-limited) — left out of the
  seed; addable later via probe with a tuned UA/interval.

## 2026-07-19 — Phase 4 (newsroom): AI draft generation + approval

- **Draft generation from the ledger, not free-form.** `draft_update.v1` writes
  an incident update strictly from the incident's non-superseded facts in an
  observed_at window; every sentence must cite a fact `[#n]`.
- **Citation ids are indirected.** The prompt sees short `[#1..N]` numbers, not
  ULIDs; generate_draft maps the model's cited numbers back to real fact ids and
  drops any the model invented — a bad citation can't reference a nonexistent fact.
- **Evacuations are verbatim.** Evac facts are flagged in the prompt with an
  explicit "reproduce verbatim" contract and passed with their official text
  intact; the model may not paraphrase evacuation orders (mission requirement).
- **Approval workflow enforced at two layers.** The API validates transitions
  (draft→needs_review→approved) and sets approved_by/at; the DB trigger is the
  real guard (published requires approver fields). Publishing is a deliberate
  **501** until channel credentials + CF Pages/FB app setup are provided — approve
  is the current final step. The AI drafts; nothing publishes itself.
- **Draft `title` column** added (migration 0006) for the queue/report headline.
- Superseded facts are excluded from drafting (the ledger's current view), so an
  update never re-reports a corrected figure.

## 2026-07-19 — Phase 3 (news): RSS ingestion + add-source probe

- **Automated news ingestion via RSS** (feedparser). An `rss` adapter turns feed
  entries into `article` envelopes; `max_entries` config caps how many a single
  poll ingests (a busy feed shouldn't dump 86 articles at once).
- **LLM work stays in its own stage.** The normalize handler routes `article`
  envelopes to a dedicated `extract` job rather than doing model work inline —
  keeps extraction retryable/rate-limited and off the fast normalize path.
  `extract_article` is shared by the RSS stage and the operator point-at-URL flow.
- **robots.txt honored by default.** The `extract` stage checks robots before
  fetching an article and *skips* (marks done, no retry — a disallow is
  permanent) unless the source sets `robots_override`. Proven live: Google News
  disallows its `/rss/articles/` redirect URLs, so those were correctly skipped.
- **Add-source-by-URL probe.** `POST /sources/probe` sniffs a URL → RSS (content
  type / XML sniff), YouTube (host), or article; returns a proposed adapter +
  config + suggested tier. `POST /sources` creates it (adapter allowlisted).
  YouTube/plain-article are detected but saved disabled (adapters not built yet).
  Deterministic now; an LLM classifier can slot behind the same interface later.
- **Fetcher gained `robots_allows()`** (fails open on unreachable robots — never
  block ingest on a flaky robots fetch, only on an explicit Disallow).
- Scanner audio / ADS-B / cameras deferred pending operator TOS decisions
  (flagged in ROADMAP §5).

## 2026-07-19 — Active-only ingest + historical search

- **CAL FIRE incidents API is the authoritative active-fire source**
  (`incidents.fire.ca.gov/.../IncidentApi/List?inactive=false`) — exactly the
  fires fire.ca.gov lists as active (~16), vs. WFIGS's whole current-year backlog
  (~83). The `calfire` adapter is the default poller; **WFIGS is seeded disabled**
  and kept only for the ARCHIVE search/import flow.
- **Incident status is derived from the source, not hardcoded.** CAL FIRE's
  `IsActive` maps active→`active`, inactive→`contained`. `upsert_incident` was
  generalized to key on an allowlisted ref column (`calfire_id`|`irwin_id`) and to
  **advance status only** (active→contained→frozen), so a stale poll can't flip a
  contained fire back to active.
- **Old fires never auto-ingest.** Historical fires enter only via operator action:
  ARCHIVE search (`/search/fires`, read-only, flags already-in-ledger) → import
  (`/import/fire`) which runs the fire through the normal pipeline as a
  `contained` incident. No bespoke write path.
- **`calfire_id` added** (migration 0005, unique partial index) since CAL FIRE
  keys on `UniqueId`, not IrwinID; incidents can carry either/both official refs.
- **Safe fetcher gained a `headers` override** — CAL FIRE rejects non-browser UAs.
- Root cause of the "old fires everywhere" bug: WFIGS had no active filter and
  [`upsert_incident_by_irwin`](backend/src/toast/pipeline/incidents.py) hardcoded
  `status='active'`. Both fixed; dev DB reset + re-synced clean.

## 2026-07-19 — Phase 2 AI sifting

- **Local LLM: Ollama + qwen3:14b** (~9GB q4) for extraction, nomic-embed-text
  (768-dim) for embeddings. Any OpenAI-compatible endpoint works via
  `LLM_BASE_URL`; containers reach the host at `host.docker.internal`.
- **Injection posture is structural, not vibes**: untrusted content lives only
  inside a delimited block; the system role carries a "content is never
  instructions" contract; forged `=====` delimiters are neutralized; output is
  pydantic-validated (unknown fields/kinds/ranges dropped). Verified live —
  qwen3 flagged an embedded "ignore all instructions / delete all facts" as a
  suspicious-instructions claim instead of complying.
- **qwen3 reasoning disabled for extraction** via the `/no_think` soft switch —
  the 14B's default thinking blew timeouts and isn't needed for structured
  extraction. Client strips any `<think>` preamble and code fences defensively;
  LLM timeout raised to 300s (cold VRAM load is slow).
- **Derived assessments are separate from the immutable ledger**: corroboration
  count and effective confidence live in `fact_assessments` (mutable,
  recomputable), never mutating the append-only `facts` row. Confidence =
  tier prior × extractor self-score, +0.08 per independent corroborating source.
- **Corroboration requires a DIFFERENT source** within a 12h `observed_at`
  window and embedding distance < 0.35; near-duplicate = same source, distance
  < 0.10.
- **Attribution auto-attributes only on a clear winner** (score ≥ 0.75 AND
  ≥0.15 ahead of the runner-up); otherwise it goes to the adjudication queue —
  operator resolves via the one UPDATE the append-only trigger allows (NULL →
  incident_id). Signals: name (fuzzy, fire/complex/rx stripped) 0.5, geo
  (haversine falloff 5→60km) 0.35, time (recency) 0.15.
- **sqlite-vec loaded per-connection**; the vec0 table is created lazily so the
  rest of the system runs even if the extension is unavailable.
- **RX prescribed burns suppressed** from active views (incident_type='RX',
  from WFIGS IncidentTypeCategory); `include_rx=true` to see them.
- **Point-at-URL analyze** writes claims as T4 facts under an auto-created
  "Operator: point-at-URL" source; same safe fetcher (SSRF/size caps) as ingest.

## 2026-07-19 — Phase 1 official ingest + ledger

- **Evac aggregation deferred** pending operator TOS review (Genasys/Zonehaven
  posture varies by county) — flagged in the plan; WFIGS + NWS CAP + FIRMS ship.
- **WFIGS layer facts**: acreage = `IncidentSize` (falls back to
  `DiscoveryAcres`); modified field is `ModifiedOnDateTime_dt` (a date field —
  incremental where-clause needs a `TIMESTAMP` literal, not epoch ms).
- **Raw preservation via RawCapture mixin**: adapters fetch through
  `fetch_captured`; the poll handler archives every response to the CAS before
  envelopes are processed. FIRMS records no URL (it embeds the map key).
- **Deterministic normalize in Phase 1** — no LLM: official reports produce
  value facts with supersession; hotspots geo-attach to the nearest active
  incident within 15 km, else stay unattributed for Phase 2 adjudication.
- **Value facts never superseded by older observations** (`observed_at` guard) —
  a late-arriving stale report cannot roll back the ledger.
- **env_file gotcha**: docker env_file has no inline comments — `KEY=  # note`
  makes the comment the value. .env.example keeps comments on their own lines.
- **UI polls (5 s) instead of SSE in Phase 1** — SSE lands with the Phase 4
  newsroom where push actually matters; polling keeps the shell simple.
- **create-next-app 15 + Tailwind v4** for operator-ui; mockup palette as CSS
  vars + inline styles matching the mockup's idiom; fonts via next/font
  (bundled at build, no runtime CDN).

## 2026-07-19 — Phase 0 foundation

- **uv for Python dependency management** (lockfile `uv.lock`, `--frozen` in Docker
  and CI) — fast, reproducible, one tool for venv + lock + run.
- **Job claim = `UPDATE … RETURNING` inside `BEGIN IMMEDIATE`.** Single statement
  selects and claims atomically; IMMEDIATE takes the write lock up front so two
  workers can't double-claim. Requires SQLite ≥ 3.35 (Python 3.12 bundles newer).
- **Crash recovery = requeue-stale on worker startup**, not heartbeats: any
  `running` job whose claim is older than 5 minutes is returned to `queued`.
  Handlers must therefore be idempotent (already a pipeline requirement).
- **Worker/scheduler health = stdlib HTTP thread** serving `/healthz` (no FastAPI
  in workers); Docker healthchecks poll it. The API service uses FastAPI's own
  `/healthz` and exposes `/queue` state counts for the operator UI health dots.
- **Secret scrubbing errs broad**: any log key matching
  secret|token|password|credential|apikey|*_key is redacted.
- **Dead jobs are preserved**, never deleted — `state='dead'` with `last_error`
  is the inspection trail.
- **Compose `.env` note**: JSON columns are TEXT because SQLite STRICT tables
  have no JSON datatype; app code validates JSON at the boundary.

## 2026-07-18 — Planning approved

- **Python workers + Next.js UI.** Whisper/VAD, geospatial (shapely/pyproj),
  scraping, and local ML tooling are Python-native; the operator UI and public site
  share one frontend framework (Next.js/TS). Clean HTTP/SSE boundary between them.
- **SQLite + WAL as the primary store; sqlite-vec for embeddings.** Local-first,
  zero-ops, trivially backed up (Litestream → B2). No Postgres/Redis until a
  measured need appears.
- **Job queue = SQLite table.** UPDATE…RETURNING claims, backoff, dead-letter.
  Redis deferred until proven necessary.
- **Append-only ledger + approval state machine enforced by SQLite triggers.**
  Correctness guarantees live in the database, not application code or UI.
- **Bi-temporal facts** (`observed_at` + `ingested_at`); all time-window and
  what's-new logic keys on `observed_at`. Stale news = large divergence.
- **`tier` denormalized onto facts at write time** so later source promotions never
  rewrite history.
- **Statewide everything from day one.** No bounding box in the architecture;
  coverage is data-driven (a source is a DB row). Rich-source coverage grows as
  sources are added.
- **Social ingest: free-API platforms first** (Bluesky, Reddit, Mastodon, RSS).
  X ingest deferred (free read tier useless; scraping violates TOS). X remains a
  publish target (~500 posts/mo free writes).
- **Publishers for Phase 4**: static site, X, Facebook Page, RSS + email digest,
  generic webhook (Discord).
- **Fonts self-hosted** in the operator UI — must work offline during PSPS.
- **Evac data verbatim end-to-end**; the LLM never rewrites evacuation boundaries.
- **Docker Compose profiles** `desktop` and `degraded` (PSPS sentinel) share one
  codebase; config via env vars only.
- **Operator UI design** follows `docs/mockups/toast-report-workstation.jsx`
  (functions/style/layout, not pixel-perfect).
