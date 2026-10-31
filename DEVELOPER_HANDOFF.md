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
→ AI sifting → newsroom drafting → human-approved publishing, with a twelve-mode
operator UI. 663 backend tests pass (unit + HTTP integration) and CI also gates on
ruff, ruff format and mypy; the UI type-checks, lints and builds. It runs as a local
Docker stack (9 services) on the operator's machine.

- **Works:** all twelve UI modes; CAL FIRE + WFIGS/federal ingest; the fact
  ledger; AI extraction/triage/activity-check; statewide + per-fire drafts and
  graphics; site generation; Bluesky/X/Facebook/Telegram publishing
  (credential-gated); ADS-B aircraft, ALERTCalifornia cameras, Cal OES evac
  zones, NWS weather/red-flag, AirNow AQI, Meshtastic mesh, scanner-radio
  inbox; **flood season** (hazard switch, NWS flood alerts, NOAA river gauges,
  DWR reservoirs with daily history — the WATER mode); deterministic **verifiers**
  on extraction (`llm/verify_extraction.py`) and on drafts (`pipeline/verify_draft.py`);
  the **watchdog** (silent feeds / stalled queue / model down → phone);
  the **agent interface** (MCP server + `toast` CLI, read-only by default); the safe
  **model installer** (`modelsetup.py`, digest-pinned `model_manifest.json`); the
  **bake-off harness** (`eval/model_bakeoff.py`); one-click update and model install;
  **archive search** (`pipeline/archive_search.py`, FTS5 over facts, drafts, radio and
  archived pages; capped agent tools).
- **Experimental / optional:** RADIO transcription (needs a Whisper endpoint +
  SDR hardware); FIRMS satellite hotspots (adapter present, disabled pending a
  key); deep research (LDR + SearXNG containers); `LLM_THINK_MODE=native` (Ollama's
  real `think:false`; off until `model_bakeoff probe` confirms it on your setup).
- **Do not change without discussion:** the append-only ledger triggers
  (`migrations/0001`,`0018`–`0020`,`0035`); the draft state-machine trigger
  (approval gate); the SSRF fetcher (`core/fetch.py`); the localhost-only bind and
  the origin guard (`api/guard.py`); the agent tool allowlist (`agent_tools.py`);
  the model pins (`model_manifest.json` — change only via `modelsetup pin`). See §11.

---

## 2. Technology stack

| Layer | Choice |
|-------|--------|
| Backend | Python 3.12, FastAPI, uvicorn |
| Data | SQLite (WAL, STRICT tables, enforced FKs, append-only bi-temporal ledger) |
| Async work | in-process job queue (a `jobs` table) + a worker process + a scheduler process |
| AI | in-stack Ollama container (OpenAI-compatible API); embeddings via sqlite-vec |
| Frontend | Next.js 16 (App Router, Turbopack), React 19, TypeScript, Leaflet (bundled, no CDN) |
| Packaging | Docker Compose (9 services), all ports bound to `127.0.0.1` |
| Tooling | pytest, ruff (lint+format), tsc, eslint |

**No login by design** — single operator, localhost only. The browser origin is
the trust boundary, so `api/guard.py` refuses state-changing requests with a
foreign `Origin` and any request with an unknown `Host` (CSRF and DNS rebinding).
Any non-localhost deployment would still need real authentication first.

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
    migrations/   0001…0039 *.sql, applied in order at startup (index in its README)
    llm/          client, extraction, prompts, verify_extraction
    eval/         attribution replay eval + model_bakeoff (cases in eval/cases/)
    agent_tools.py, mcp_server.py, cli.py   the agent interface (one tool registry)
    modelsetup.py + model_manifest.json     safe model download (pinned digests)
    mesh/, assets/, seed.py, scheduler.py, backup*.py, rawsync.py
  tests/
    unit/         ~80 files, one per module
    integration/  real FastAPI app over HTTP on a fresh migrated DB
operator-ui/
  app/            Next.js App Router entry (page.tsx → Workstation)
  components/     one file per mode + shared (workstation.tsx is the shell)
  lib/            api.ts (typed client), theme.ts, time.ts, html.ts, california.ts
docs/             architecture, audits, roadmap, per-subsystem guides
prompts/          versioned, operator-editable LLM prompts (*.vN.md)
docker-compose.yml, .mcp.json
ToastReport.cmd, Install Models.cmd, Update and Launch.cmd → scripts/*.ps1
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
docker compose --profile desktop up   # starts all 9 services
```

The **one-step Windows launcher** is `ToastReport.cmd` (double-click): it
brings up the Docker stack, starts the operator UI on **port 3005**, and opens
it in a standalone app window. See `scripts/launch.ps1`. Its siblings:
`Install Models.cmd` (safe, verified model download — docs/MODELS.md) and
`Update and Launch.cmd` (snapshot → fast-forward pull → rebuild what changed).

**UI-only dev loop** (backend already up): `npm --prefix operator-ui run dev`.
The UI proxies `/api/*` to `127.0.0.1:8400`.

**Commands:**

```bash
# backend (from backend/)
uv run --frozen pytest                # 663 tests
uv run --frozen ruff check .          # lint
uv run --frozen ruff format --check . # format (CI gate; `ruff format .` to fix)
uv run --frozen mypy                  # types (CI gate)
uv run --frozen python -m toast.seed  # seed sources into a fresh DB
# always pass --frozen: without it uv may re-resolve and rewrite uv.lock

# frontend (from operator-ui/)
npx tsc --noEmit                      # type check
npx eslint .                          # lint
npm run build                         # production build
npm audit --omit=dev                  # 0 vulnerabilities as of 2026-10-31 (dev-only: see §12)
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
monkeypatched). No secrets, no live external calls. A test that writes files must
point `TOAST_DATA_DIR` at `tmp_path` — the suite leaves the working tree clean.

Model quality is not unit-testable; `python -m toast.eval.model_bakeoff` measures it
against a live model (see docs/MODELS.md). Its own scoring is unit-tested: a scripted
perfect model must score perfectly and a reckless one must rank last.

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
- **The verifiers** — `verify_extraction.py` drops claims whose numbers aren't in the
  source; `verify_draft.py` flags drafts and blocks phone approval. Loosening either
  lets invented figures into a permanent ledger or a public post.
- **Agent tools** — adding a tool to `agent_tools.TOOLS` exposes it to any connected
  agent, which reads hostile scraped text. `test_agent_interface.py` asserts no tool
  reaches approve/publish/settings routes; keep it that way.
- **Embedding distances** are L2 (sqlite-vec default), not cosine — the thresholds in
  `pipeline/embeddings.py` are tuned in L2 units. Changing model or metric means
  re-embedding and re-tuning.

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
3. ~~**Telegram approval** authenticates the chat, not the clicking user~~ — fixed
   2026-10-31: `TELEGRAM_OPS_USER_IDS` limits who may press APPROVE / SEND BACK.
   Also fixed then: alerts and approvals read Telegram credentials saved in OPTIONS,
   not only `.env` (before, a bot set up in OPTIONS was silently ignored).

Open as of the 2026-10-31 audit:

4. **Offsite backup is not live** — the Litestream B2 replica is commented out
   pending keys; the only copy of the archive is on the same disk.
5. **No UI unit tests** — CI runs lint, types and build only.
6. **Windows scripts unrun** — `scripts/*.ps1` were reviewed, not executed, by
   their author; the model installer was tested against simulated Ollama/registry
   servers only.
7. **Default model unmeasured** — `qwen3.5:9b` is a safe official baseline, not a
   bake-off winner; run the bake-off on real cases. Extraction still uses the
   `/no_think` hint that Qwen3.5 ignores until `LLM_THINK_MODE=native` is confirmed.
8. **Dev-only npm advisory** — a moderate `minimatch` advisory inside the ESLint
   toolchain; its fix (minimatch 11) needs a breaking ESLint upgrade. Nothing shipped
   is affected; the high `brace-expansion` one is fixed by an override.
9. **Large files** — `api/routes.py` and `components/workstation.tsx` would benefit
   from splitting by area.
10. **Archive search has no UI yet** — it's reachable from agents, the CLI and the API;
    a NEWSROOM/OPS search box is the natural next step. Text inside images (OCR) is
    not indexed.

Product backlog lives in [docs/ROADMAP.md](docs/ROADMAP.md); the open UI-polish
item is char-count/queue-search/Spanish-version in the newsroom.

---

## 13. Handoff readiness

**Ready for an experienced developer.** The architecture is consistent (the
module + handler + route + component + client quintet repeats for every
feature), naming is purposeful, there is no dead code or TODO debt, types are
strong, and the invariants that matter are enforced in the database rather than
by convention. The two large files (`api/routes.py` ~2500 lines,
`components/workstation.tsx`) are big but coherent and internally sectioned —
grep by path group / mode. Read this file, then DECISIONS.md, then trace one
feature end to end (the OPS map or a draft) and the rest follows the same shape.
