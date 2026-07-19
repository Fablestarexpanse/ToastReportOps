# Decisions

Architectural decision log. Newest first. Each entry: context → decision → why.

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
