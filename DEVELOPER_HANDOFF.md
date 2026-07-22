# Developer Handoff — Toast Report OPs

A working developer's guide to the codebase: what it is, how it's built, how
to run it, where each feature lives, and where to be careful. Written for an
experienced developer inheriting the project cold.

For the *why* behind decisions, read [DECISIONS.md](DECISIONS.md) (a dated,
newest-first log). For the last full audit, see
[docs/AUDIT-2026-07-22.md](docs/AUDIT-2026-07-22.md).

---

## 1. Current status

**Built and operating.** The full pipeline runs: ingest → append-only ledger
→ AI sifting → newsroom drafting → human-approved publishing, with a nine-mode
operator UI. 444 tests pass (unit + HTTP integration). It runs as a local
Docker stack on the operator's machine.

- **Works:** all nine UI modes; CAL FIRE + WFIGS/federal ingest; the fact
  ledger; AI extraction/triage/activity-check; statewide + per-fire drafts and
  graphics; site generation; Bluesky/X/Facebook/Telegram publishing
  (credential-gated); ADS-B aircraft, ALERTCalifornia cameras, Cal OES evac
  zones, NWS weather/red-flag, AirNow AQI, Meshtastic mesh, scanner-radio
  inbox.
- **Experimental / optional:** RADIO transcription (needs a Whisper endpoint +
  SDR hardware); FIRMS satellite hotspots (adapter present, disabled pending a
  key); deep research (LDR + SearXNG containers).
- **Do not change without discussion:** the append-only ledger triggers
  (`migrations/0001`,`0018`–`0020`,`0035`); the draft state-machine trigger
  (approval gate); the SSRF fetcher (`core/fetch.py`); the localhost-only bind.
  See §11.

---

## 2. Technology stack

| Layer | Choice |
|-------|--------|
| Backend | Python 3.11, FastAPI, uvicorn |
| Data | SQLite (WAL, STRICT tables, enforced FKs, append-only bi-temporal ledger) |
| Async work | in-process job queue (a `jobs` table) + a worker process + a scheduler process |
| AI | in-stack Ollama container (OpenAI-compatible API); embeddings via sqlite-vec |
| Frontend | Next.js 16 (App Router, Turbopack), React 19, TypeScript, Leaflet (bundled, no CDN) |
| Packaging | Docker Compose (8 services), all ports bound to `127.0.0.1` |
| Tooling | pytest, ruff (lint+format), tsc, eslint |

**No authentication by design** — single operator, localhost only. The browser
origin is the trust boundary (see the XSS findings in the audit). Any
non-localhost deployment would need auth + CSRF added first.

---

## 3. Repository structure

```
backend/
  src/toast/
    api/          FastAPI app + all HTTP routes (app.py, routes.py)
    core/         cross-cutting infra: db, config, fetch (SSRF), logging,
                  credentials, sanitize, ids, timestamps, rawstore, envelope
    sources/      ingest adapters (register via @register): calfire, wfigs,
                  rss, nws_cap, firms, youtube; probe + capture helpers
    pipeline/     the domain: one module per concern (see §5). Ledger writes,
                  incident upsert, extraction, drafting, graphics, all the
                  external-data layers (aircraft, cameras, evaczones, firewx…)
    queue/        jobs.py (enqueue/claim/complete), worker.py (run loop),
                  handlers.py (stage registry)
    publishers/   bluesky, facebook, telegram, x  (credential-gated)
    sitegen/      static site builder
    migrations/   0001…0035 *.sql, applied in order at startup
    llm/, mesh/, eval/, assets/, seed.py, scheduler.py, backup*.py, rawsync.py
  tests/
    unit/         ~62 files, one per module
    integration/  real FastAPI app over HTTP on a fresh migrated DB
operator-ui/
  app/            Next.js App Router entry (page.tsx → Workstation)
  components/     one file per mode + shared (workstation.tsx is the shell)
  lib/            api.ts (typed client), theme.ts, time.ts, html.ts, california.ts
docs/             architecture, audits, roadmap, per-subsystem guides
prompts/          versioned, operator-editable LLM prompts (*.vN.md)
docker-compose.yml, ToastReport.cmd, scripts/launch.ps1
```

**Where to find a feature:** almost every backend feature is one
`pipeline/<name>.py` module + a `@register("<stage>")` handler +
route(s) in `routes.py` + a `components/<mode>.tsx` UI + typed client
functions in `lib/api.ts`. Trace that quintet.

---

## 4. Local setup

**Prerequisites:** Docker Desktop, Node 20+ (only if running the UI outside
Docker), and (Windows) the bundled launcher.

```bash
cp .env.example .env      # fill in optional keys/credentials; LLM works as-is
docker compose --profile desktop up   # starts all 8 services
```

The **one-step Windows launcher** is `ToastReport.cmd` (double-click): it
brings up the Docker stack, starts the operator UI on **port 3005**, and opens
it in a standalone app window. See `scripts/launch.ps1`.

**UI-only dev loop** (backend already up): `npm --prefix operator-ui run dev`.
The UI proxies `/api/*` to `127.0.0.1:8400`.

**Commands:**

```bash
# backend (from backend/)
uv run pytest                         # 444 tests
uv run ruff check .                   # lint  (ruff format . to format)
uv run python -m toast.seed           # seed sources into a fresh DB

# frontend (from operator-ui/)
npx tsc --noEmit                      # type check
npx eslint .                          # lint
npm run build                         # production build
npm audit                             # 0 vulnerabilities as of 2026-07-22
```

Inside the container, run backend commands via
`docker compose exec api|worker python -m toast.<module>`.

---

## 5. Architecture & major flows

### Ingest → ledger (the core)
1. A **scheduler** (`scheduler.py`) enqueues poll jobs on cadences (CAL FIRE
   120 s, aircraft 60 s, activity-check 1800 s, etc.).
2. A **source adapter** (`sources/*`) fetches through the SSRF-guarded
   `core/fetch.py`, stores the raw bytes content-addressed (`core/rawstore.py`),
   and writes an **envelope** (`core/envelope.py`) — deduped on
   `(source_id, external_id, content_hash)`.
3. The **normalize** stage (`pipeline/normalize.py`) turns an envelope into an
   **incident** (upsert, with cross-source CAL FIRE↔WFIGS adoption) and
   **facts** through the one ledger writer, `pipeline/ledger.py::write_fact`.

### The fact ledger — the invariant that matters most
Facts are **append-only and bi-temporal** (`observed_at` = when the source saw
it, `ingested_at` = when we did). A value never changes in place: a new fact
**supersedes** the old one (`facts.supersedes`). Enforced by SQLite triggers
(no-delete + whitelist-update). "The current value of X" is always
`ledger.value_order()` — official tiers (1–2) beat media (3+), future-stamped
facts rank last, newest within a band wins. **Every card, draft, panel and API
number flows through that ordering** — do not re-implement it inline.

### AI — propose, never act
LLM output never mutates published state directly. Extraction lands facts at
tier 4 (untrusted); triage/activity-check write to side tables
(`triage_proposals`, `activity_proposals`); drafts require an operator click
that trips the `drafts` state-machine trigger. Ingested text is fenced in
prompts (`prompts/*.md` carry an explicit untrusted-content contract). The one
exception, deliberately: an official source (CAL FIRE / NIFC) *delisting* a
fire auto-closes it — that's relaying authority, not judgement (see DECISIONS).

### Publishing
Site generation is always available; social publishers
(`publishers/{bluesky,x,facebook,telegram}.py`) are inert without credentials.
Approval is enforced by the DB, not the UI.

### Validation & error handling
- **Trust boundary validation:** `core/fetch.py` (SSRF, size, content-type,
  redirect re-check), `core/sanitize.py` (nh3 on any markdown→HTML),
  allowlisted mutation bodies in routes, `core/appsettings.py` /
  `core/credentials.py` allowlisted keys, the ledger's own CHECK constraints.
- **Error handling:** routes raise `HTTPException`; pipeline jobs raise and the
  worker records the failure to `jobs.last_error` (secrets redacted by
  `fetch.redact_url` and the `core/logging` scrubber) with backoff/dead-letter.
  External-data layers degrade gracefully (a feed outage returns cached/empty,
  never a 500 that breaks the map).

---

## 6. Data model (high-value tables)

- **incidents** — one row per fire; mutable status (`active`→`contained`→
  `frozen`), `lat/lon/county`, and the official cross-refs `calfire_id` /
  `irwin_id`.
- **facts** — the append-only ledger. `incident_id, kind, observed_at,
  ingested_at, tier, supersedes`, plus typed value columns
  (`acreage`, `containment_pct`, `evac_status`, …). Never DELETE/UPDATE except
  the trigger-permitted whitelist.
- **envelopes** / **raw_blobs** — provenance + content-addressed raw bytes
  (permanent archive, unbounded by design).
- **incident_events** — append-only lifecycle log (status changes, adoptions).
- **drafts** / **publications** — review-gated content and its delivery record.
- **jobs** — the work queue (terminal rows pruned after 7 days).
- **map_cache** — keyed TTL cache for every external-data layer (perimeters,
  aircraft, cameras, evac zones, weather…).
- Side tables: `triage_proposals`, `activity_proposals`, `source_suggestions`
  — AI proposals awaiting an operator decision.

Ownership is trivial (single operator). "Authorization" is really *provenance
and approval*: tier trust on facts, and the human-approval gate on drafts.

---

## 7. Adding things

- **A route:** add to `api/routes.py` under the relevant path group; mutations
  take a `body: dict` and validate keys explicitly; add a typed function to
  `operator-ui/lib/api.ts`. Side-effecting endpoints are POST, never GET.
- **A background stage:** write `pipeline/<name>.py`, register it with
  `@register("<stage>")` in `queue/handlers.py` (or `pipeline/handlers.py`),
  and either add a scheduler cadence or enqueue it from another handler.
- **A migration:** add `migrations/00NN_<name>.sql` (next number, no gaps),
  using `IF NOT EXISTS`; bump the count assertion in
  `tests/unit/test_migrations.py`. Migrations are forward-only — rollback =
  restore from a backup snapshot. If you rebuild a table, re-create its
  triggers verbatim inside the same migration (see 0019/0020).
- **A source:** add `sources/<name>.py` with `@register("<adapter>")`, seed it
  in `seed.py`.
- **A test:** unit test beside the module; HTTP-level behavior goes in
  `tests/integration/`.

---

## 8. Deployment

There is no cloud deployment — this is a **local-first** tool. "Deploy" =
`docker compose --profile desktop up` (or `ToastReport.cmd`). Backend code
changes require a rebuild: `docker compose --profile desktop build api worker
scheduler && … up -d`. Data lives in `./data` (SQLite + raw archive); nightly
`VACUUM INTO` snapshots + optional Backblaze B2 offsite (`backup*.py`, with the
`credentials` table stripped before upload).

---

## 9. External services

All free/keyless-first. Keyed ones are optional and inert until configured:

| Service | Purpose | Key? |
|---------|---------|------|
| CAL FIRE incidents API | primary state fire list | no |
| WFIGS/IRWIN (NIFC) | federal/USFS fires + perimeters | no |
| NWS (api.weather.gov) | weather, red-flag warnings, alerts | no |
| Cal OES ArcGIS | evacuation zone polygons | no |
| airplanes.live | ADS-B aircraft | no |
| ALERTCalifornia | camera metadata (frames load in operator browser) | no |
| public Meshtastic MQTT | mesh messages | no (community creds) |
| AirNow | AQI | optional key |
| FIRMS | satellite hotspots | key (disabled) |
| Bluesky / X / Facebook / Telegram | publishing | credentials |
| Ollama (in-stack `llm`) | LLM + embeddings | no |
| SearXNG + LDR (in-stack) | deep research | no |

---

## 10. Testing

`uv run pytest` from `backend/`. Unit tests mirror `src/toast/` one-to-one;
`tests/integration/test_http_flows.py` drives the real FastAPI app over
`TestClient` on a fresh migrated DB (draft lifecycle, publish guard, markdown
sanitizer, activity-check through the actual queue+worker). Tests are
deterministic (`utcnow` is the only time source; no network — adapters are
monkeypatched). No secrets, no live external calls.

---

## 11. Areas requiring caution

- **The ledger triggers** (`migrations/0001`,`0018`–`0020`,`0035`) — they make
  facts append-only. If you add a `facts` column you must extend the
  whitelist-update trigger (it fails *closed* — a forgotten column becomes
  immutable, not silently mutable). Never loosen them to make a test pass.
- **`value_order()`** — the single source of "current value" ordering. Changing
  it changes every number the tool publishes.
- **The draft state machine** — the only thing standing between AI output and a
  public post. Do not add a code path that publishes without the approval
  transition.
- **`core/fetch.py`** — the SSRF gate for all outbound requests; it resolves,
  checks, *and* re-checks the connected peer. Ingest touches hostile URLs.
- **Localhost bind / no auth** — safe only because nothing is exposed. Any
  remote exposure needs authentication and CSRF first.
- **DB writes from the host** — always write `data/toast.db` through a
  container while the stack is up; host-side writes can wedge Docker's
  file-share locks (see the outage note in the session history).
- **Migrations** — forward-only; test on a copy; never delete history.

---

## 12. Known issues & recommended next work

From the 2026-07-22 audit, three items are deliberately deferred (none is a
bug):

1. **Supersession anti-join** — `NOT IN (SELECT supersedes…)` scans the full
   `facts` table inside per-fire loops. Fine at current scale (the new
   composite index mitigates); revisit with a `superseded_by`/`is_current`
   column + a migration if a full season's facts slow the pickers.
2. **Disk capacity** — `raw_blobs`/`envelopes`/`facts` grow unbounded by design
   (permanent archive). Plan a season-end size estimate / cold-tier offload.
3. **Telegram approval** authenticates the chat, not the clicking user — only
   matters if the ops chat ever becomes a group; add a user-id allowlist then.

Product backlog lives in [docs/ROADMAP.md](docs/ROADMAP.md); the open UI-polish
item is char-count/queue-search/Spanish-version in the newsroom.

---

## 13. Handoff readiness

**Ready for an experienced developer.** The architecture is consistent (the
module + handler + route + component + client quintet repeats for every
feature), naming is purposeful, there is no dead code or TODO debt, types are
strong, and the invariants that matter are enforced in the database rather than
by convention. The two large files (`api/routes.py` ~2000 lines,
`components/workstation.tsx`) are big but coherent and internally sectioned —
grep by path group / mode. Read this file, then DECISIONS.md, then trace one
feature end to end (the OPS map or a draft) and the rest follows the same shape.
