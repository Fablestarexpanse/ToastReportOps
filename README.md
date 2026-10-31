# Toast Report OPs

A local-first California **wildfire and flood** intelligence workstation for one person,
run at home. It ingests official feeds (CAL FIRE, NIFC/WFIGS, Cal OES evacuation zones,
NWS alerts and weather, NOAA river gauges, DWR reservoirs, AirNow), satellite hotspots,
ADS-B aircraft, your own scanner audio, news, social and community mesh traffic. Local
AI sifts and attributes facts to specific incidents into an append-only ledger; you
review AI-drafted reports and decide what, if anything, gets published. Every incident
is permanently archived.

**Status:** built and operating — ingest → ledger → AI sift → newsroom → publish, a
12-mode operator UI, an 8-service Docker stack (plus optional MESH), 690 backend tests (lint, format, types
and tests all gate CI), and a UI that type-checks, lints and builds clean.

## Quick start (Windows)

Four buttons in the install folder:

| Double-click | What it does |
|---|---|
| **`Install.cmd`** | **First time.** Checks this computer (starts Docker Desktop if needed, tests the GPU, Node, disk and ports), creates `.env` with fresh local passwords, installs the UI packages, starts the stack, seeds the sources, installs the AI models (shows the plan first), verifies everything and opens the app. Safe to re-run; `Install.cmd -CheckOnly` changes nothing. Writes `install.log`. |
| **`ToastReport.cmd`** | Starts the Docker stack and the operator UI (port 3005) in its own app window. Safe to run repeatedly. |
| **`Install Models.cmd`** | Downloads the AI models **safely**: official sources only, checked against pinned fingerprints before and after, skipped if too big for your graphics card, test-run afterwards. Resumable. → [docs/MODELS.md](docs/MODELS.md) |
| **`Update and Launch.cmd`** | Takes a verified database snapshot, pulls the latest code (fast-forward only — never overwrites your edits), rebuilds only what changed, restarts, checks health, opens the app. Stops with the exact undo command if anything fails. |

**First time:** install Docker Desktop, Node 20+ and git (see below), clone the repo
(any drive works), then double-click **`Install.cmd`**. Elsewhere:
[Install & run (any OS)](#install--run-any-os).

> The stack itself has been installed end to end on Windows (images, build, seed, model
> plan). `Install.cmd` is new and gathers those same steps; if it stops, `install.log`
> has the exact error.

## What's in the workstation

| Mode | What it's for |
|---|---|
| **OPS** | Live intake stream, incident panel (ledger with supersession, evac state, weather, aircraft, cameras), statewide map |
| **EVAC** | Official Cal OES evacuation zones — orders, warnings, reductions — relayed verbatim |
| **AIR** | ADS-B aircraft over incidents; tanker/air-attack orbit detection |
| **WATER** | NWS flood alerts, NOAA river gauges at or above action stage (plus ones you pin), and all ~48 DWR reservoirs with % of capacity, % of average and 7-day trend |
| **RADIO** | Your own SDR captures and stream channels, optional transcription |
| **MESH** | Statewide Meshtastic picture via the public MQTT broker — **optional**, off by default (set `TOAST_MESH=1`; see [License](#license)) |
| **TIMELINE** | Per-incident event timeline |
| **NEWSROOM** | AI drafts for review, the draft verifier's findings, approval, release to channels, the local static-site build |
| **WATCH** | Candidate new-start fires not yet in the ledger, for triage |
| **ARCHIVE** | Every incident ever tracked, filed by year and month; frozen fires are immutable |
| **SOURCES** | Feeds, trust tiers (T1 official → T5 community) and their performance |
| **ANALYTICS** | Facts per day, source performance, attribution and corroboration rates |

**Hazard season.** OPTIONS → *Hazard season* switches between `fire`, `flood` and
`both`. Flood/both turns on background polling of gauges and flood alerts, phone alerts
for flood warnings and near-full reservoirs, and the matching health checks. The WATER
tab works on demand in any season; reservoir history is always collected.

**Phone.** With a Telegram bot configured, your ops chat gets new fires, evacuation
changes, flood warnings, near-full reservoirs, system-health problems, and drafts to
approve (a draft the verifier flagged can't be approved from the phone).

## How it stays trustworthy

- **A human publishes.** Nothing goes public without your approval — enforced by a
  database trigger, not convention. Publishing is manual; nothing auto-deploys.
- **The model proposes, code verifies.** Extracted claims must quote numbers, fire
  names and places that appear in their source, or they're dropped/cleared. AI drafts
  are checked against the facts they cite (invented figures, paraphrased or missing
  evacuation text, bad citations) and flagged in the NEWSROOM.
- **Official first.** Official figures outrank media; evacuation text comes verbatim
  from officials; unofficial reports are badged as such.
- **Silence is an alarm.** A watchdog tells your phone when a feed stops, the queue
  stalls or the model goes away — so "no alerts" never quietly means "broken".
- **Localhost only, guarded.** Everything binds to `127.0.0.1`; the API also refuses
  cross-site requests and foreign `Host` headers (CSRF / DNS rebinding).
- **Agents can read, not publish — and only a little at a time.** The MCP server and
  CLI are read-only by default, have no tool that can approve, publish, delete or
  change settings, and cap what any one call returns, since most of it is scraped text.
- **Safe models, no surprise downloads.** Official, digest-pinned models; community
  uploads are refused; nothing downloads a model unless you run the installer (or
  explicitly set `LLM_AUTO_PULL=1`).

Details: [docs/SECURITY.md](docs/SECURITY.md), [docs/AGENT-INTERFACE.md](docs/AGENT-INTERFACE.md),
[docs/MODELS.md](docs/MODELS.md).

## System requirements

Everything runs on one machine; no cloud account is required. The main hardware driver
is the local language model.

| Role | Default model | Download |
|---|---|---|
| Extraction, drafting, judgment | official **`qwen3.5:9b`** | ~6.6 GB |
| Embeddings (dedupe / corroboration) | **`nomic-embed-text`** (768-dim) | ~0.3 GB |
| *8 GB cards* | *`qwen3.5:4b` via `Install Models.cmd -Profile small-8gb`* | *~3.3 GB* |

The default is a starting point, not a measured winner: compare candidates on your own
data with the bake-off ([docs/MODELS.md](docs/MODELS.md)), using the research in
[reports/](reports/Lightweight%20local%20models%20for%20wildfire%20tool.md). The endpoint
is OpenAI-compatible, so `LLM_BASE_URL` can point at a cloud API instead (no GPU needed).

| Component | Minimum | Comfortable |
|---|---|---|
| OS | Windows 10/11 (WSL2) or Linux | — |
| GPU | NVIDIA **12 GB** (e.g. RTX 3060 12 GB / 4070); 8 GB with the small profile | 24 GB lets you try larger candidates |
| CPU | modern 6-core / 12-thread | 8-core+ |
| RAM | 16 GB | 32 GB |
| Disk | ~50 GB free, SSD | NVMe, 100 GB+ — the ledger and raw archive grow by design |
| Network | broadband (first run pulls images + ~7 GB of models) | — |

**Software:** Docker Desktop (Windows: WSL2 backend + the NVIDIA WSL driver; Linux:
NVIDIA Container Toolkit), `git`, and Node 20+ only if you run the UI outside Docker.

**The stack (8 services, plus 1 optional):** `api`, `worker`, `scheduler`, `llm`
(Ollama), `litestream`, `searxng` + `ldr` (optional deep research), `icecast` (radio
streams), and the optional `mesh` listener (`TOAST_MESH=1` in `.env`). The `degraded`
profile runs only `api` + `worker` + `scheduler` for power outages
([OPERATIONS.md](OPERATIONS.md)).

## Install & run (any OS)

```bash
git clone https://github.com/Fablestarexpanse/ToastReportOps.git
cd ToastReportOps
cp .env.example .env                       # works as-is; optional keys: docs/API-KEYS.md. Never commit .env
docker compose --profile desktop up -d     # models are not auto-downloaded; next two lines
docker compose --profile desktop exec -T api python -m toast.seed             # seed the source list
docker compose --profile desktop exec -T api python -m toast.modelsetup install --vram-gb 12
```

Open **http://localhost:3005** (API `http://localhost:8400`, site preview
`http://localhost:8400/site/`). UI outside Docker: `npm --prefix operator-ui run dev -- -p 3005`.

**Check it:**

```bash
docker compose --profile desktop ps                                            # all healthy
curl http://127.0.0.1:8400/system/status                                       # "ok": true, no problems
docker compose --profile desktop exec -T api python -m toast.modelsetup status # models match their pins
```

**Stop:** `docker compose --profile desktop down` (`-v` also drops the database and
models). **No NVIDIA GPU:** point `LLM_BASE_URL`/`LLM_API_KEY` at a cloud
OpenAI-compatible endpoint and remove the GPU reservation from the `llm` service.

**Update without Windows:** `git pull --ff-only`, take a snapshot
(`docker compose --profile desktop exec -T api python -m toast.backup`), then
`docker compose --profile desktop up -d --build`.

## Agents and scripts

An MCP server and a `toast` CLI let Claude, Hermes or any MCP client read the ledger,
the WATER board and drafts, and **search the archive** (facts, drafts, radio
transcripts, archived news pages) in capped snippets. `.mcp.json` is preconfigured for Claude Code. Read-only by
default; `TOAST_AGENT_WRITE=1` adds two tools (leave a note, request a draft for you to
review). → [docs/AGENT-INTERFACE.md](docs/AGENT-INTERFACE.md)

### Setup prompt for an AI coding agent

`Install.cmd` does the work, so an agent only needs to run it and read the log:

```text
Install "Toast Report OPs" (a local-first, Docker-based California wildfire and flood
workstation) on this Windows machine and report status as you go.

- Repo (private): https://github.com/Fablestarexpanse/ToastReportOps - clone it to the
  folder I name (I'll authorize GitHub access if prompted).
- Run commands in a real PowerShell on this machine. If your own shell is a sandbox
  that can't see Docker, my GPU or localhost, give me each command to run instead and
  read install.log afterwards.

Do this:
1. Ask me before installing any system software (Docker Desktop with WSL2, Node 20+, git).
2. Run: Install.cmd -CheckOnly   then read install.log and tell me what it found.
3. If it passed, run: Install.cmd   (it shows the model download plan and asks before
   downloading; tell me what it proposes before I answer).
4. Report the URLs, the active model and everything under "Needs your attention" in
   install.log.

Rules: don't commit or push; don't invent API keys (the installer generates only local
passwords); keep everything on localhost; never enable agent write access or publishing;
if a step fails, show me the exact error from install.log and stop.
```

## Core principles

1. **Local-first, append-only.** SQLite is the primary store. The fact ledger is
   append-only; corrections are supersession records, never edits — enforced by DB
   triggers, not convention.
2. **Bi-temporal facts.** Every fact records `observed_at` and `ingested_at`; stale
   news is a large gap between them.
3. **Everything is a pipeline stage.** ingest → normalize → extract → verify →
   geocode → attribute → corroborate → draft → verify → (human) publish. Raw source
   material is preserved before any processing.
4. **Trust is data.** Five source tiers with tracked performance; promotions are
   computed suggestions you approve.
5. **Human-in-the-loop publishing.** The AI drafts; you decide. Evacuation information
   is relayed verbatim from officials, never paraphrased by a model.
6. **The model proposes, code verifies.** Anything a model writes is checked in code
   against its source before it can matter.
7. **Plugin extensibility.** Sources and publishers are adapters behind stable
   interfaces.

## License

The code is licensed under the [Apache License 2.0](LICENSE); see [NOTICE](NOTICE).
Contributions are welcome under the same license with a DCO sign-off — see
[CONTRIBUTING.md](CONTRIBUTING.md). The *names* "Toast Report" and "Toast Report OPs"
are not licensed for use by other instances: run your own under your own name
([TRADEMARKS.md](TRADEMARKS.md)).

- **Optional GPL component.** The MESH listener uses the Meshtastic library (GPL-3.0),
  so it is an opt-in extra (`pip install .[mesh]`, Compose profile `mesh`). The default
  install and Docker images contain no GPL-licensed code.
- **Data is not code.** Feeds, news, imagery, radio and maps the tool shows or archives
  stay under each provider's terms — some may be viewed but not stored or republished
  (the source notes say which). Reports you publish are your own content.

## Documents

| Doc | Contents |
|---|---|
| [OPERATIONS.md](OPERATIONS.md) | Runbook: start/stop, updating, backup/restore, PSPS mode, publishing |
| [DEVELOPER_HANDOFF.md](DEVELOPER_HANDOFF.md) | Developer's guide to the codebase |
| [DECISIONS.md](DECISIONS.md) | Architectural decision log (newest first) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute: checks, DCO sign-off, the safety rules |
| [docs/MODELS.md](docs/MODELS.md) | Which models, safe installation, the bake-off |
| [docs/AGENT-INTERFACE.md](docs/AGENT-INTERFACE.md) | MCP server and `toast` CLI for agents and scripts |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, prompt-injection posture, SSRF rules |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, services, interfaces |
| [docs/API-KEYS.md](docs/API-KEYS.md) | Every optional key and what it unlocks |
| [docs/BACKUP-RESTORE.md](docs/BACKUP-RESTORE.md) | Backup layers and the restore drill |
| [docs/CORROBORATION.md](docs/CORROBORATION.md) | Corroboration tuning notes |
| [docs/RADIO-SETUP.md](docs/RADIO-SETUP.md) | SDR scanner hardware for the RADIO tab |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Original phase plan (history; most phases are built) |
| [docs/history/](docs/history/README.md) | Earlier audits, specs and plans — dated snapshots kept for the record, not current guidance |
| [reports/](reports/) | Research reports (lightweight local models) with notes in `research_notes/` |
| [backend/src/toast/migrations/](backend/src/toast/migrations/README.md) | Schema migrations and their rules |

## Layout

- `ToastReport.cmd`, `Install Models.cmd`, `Update and Launch.cmd` — the buttons; `scripts/` holds their PowerShell
- `backend/` — Python 3.12: FastAPI, workers, pipeline, migrations, MCP server and CLI (uv-managed)
- `operator-ui/` — Next.js operator workstation (localhost only)
- `prompts/` — versioned LLM prompt files (every fact records the prompt version that produced it)
- `infra/` — Litestream, SearXNG and PSPS degraded-mode config
- `docs/`, `reports/`, `research_notes/` — documentation and research
- `.mcp.json` — MCP server config for Claude Code
- `data/` — git-ignored: `toast.db`, raw archive (sha256 content-addressed), backups, eval results
