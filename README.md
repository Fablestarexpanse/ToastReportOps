# Toast Report OPs

Local-first California wildfire intelligence system. Ingests official APIs, satellite
hotspots, scanner audio, news, social, video, images, ADS-B aircraft data, and weather;
uses local AI to sift, verify, and attribute facts to specific fires; a single human
operator reviews AI-drafted reports and publishes them to a static public site and
social channels. Every incident is permanently archived.

**Status: built and operating** — the full loop runs (ingest → ledger → AI sift →
newsroom → publish), with a 9-mode operator UI, a Docker stack, and 440 passing tests.

**Quick start:** double-click `ToastReport.cmd` — it starts the Docker stack and the
operator UI (port 3005) in its own app window. Alternative:
`docker compose --profile desktop up`.

## System requirements

Everything runs on one machine, bound to localhost — no cloud account required. The
main hardware driver is the local LLM.

**The local LLM.** Reasoning and embeddings run on-machine via **Ollama** (in-stack
container, GPU passthrough). Two models, auto-pulled on first boot:

| Role | Model | Size | Notes |
|---|---|---|---|
| Extraction / drafting / sift | **`qwen3:14b`** (14B, Q4) | ~9 GB | Default; swappable — see `.env` `LLM_MODEL` |
| Embeddings (dedup / corroboration) | **`nomic-embed-text`** (768-dim) | ~0.3 GB | Runs on the same GPU |

The endpoint is OpenAI-compatible, so `LLM_BASE_URL` can point at a **cloud API**
instead — then no GPU is needed at all.

**Software prerequisites:**
- **Docker Desktop** — the backend is 8 containers (api, worker, scheduler, llm/Ollama,
  searxng, local-deep-research, mesh, litestream)
- Windows: **WSL2** + the **NVIDIA WSL driver**; Linux: **NVIDIA Container Toolkit** (GPU passthrough)
- **Node 20+** — only to run the operator UI outside Docker
- Broadband internet — for the free/keyless public feeds (CAL FIRE, Cal OES, NWS, ADS-B, …)

**Minimum hardware:**

| Component | Minimum | Notes |
|---|---|---|
| OS | Windows 10/11 (WSL2) or Linux | macOS only if you skip the NVIDIA GPU / run a smaller model on Metal |
| GPU | NVIDIA, **≥ 12 GB VRAM** (e.g. RTX 3060 12 GB / 4070) | To hold `qwen3:14b`. Use `qwen3:8b` (~5 GB) on an 8 GB card, or go cloud for none |
| CPU | Modern 6-core / 12-thread | Runs the 8 services + WSL2 |
| RAM | **16 GB** | 32 GB comfortable alongside a browser + the UI |
| Disk | **~50 GB free, SSD** | ~15–20 GB images + ~10 GB models + a growing data archive |
| Network | Broadband | First boot pulls ~10 GB of models + images |

**Recommended (the reference build):** NVIDIA **RTX 4090 (24 GB VRAM)** via WSL2/Docker,
**32 GB+ RAM**, 8-core+ CPU, and an **NVMe SSD with 100 GB+ free** — the append-only
ledger and raw-content archive grow unbounded by design, so budget storage for
long-running use.

## Install & run

**Prerequisites:** Docker Desktop (Windows: WSL2 backend enabled); for NVIDIA GPUs, the
WSL2 GPU driver (Windows) or NVIDIA Container Toolkit (Linux) so containers can see the
GPU; `git`; and Node 20+ only if you run the UI outside Docker.

```bash
# 1. Get the code
git clone https://github.com/Fablestarexpanse/ToastReportOps.git
cd ToastReportOps

# 2. Create config (works as-is with the local LLM; optional keys unlock more sources
#    and social publishing — see docs/API-KEYS.md). Never commit .env.
cp .env.example .env

# 3. Start the stack. FIRST BOOT downloads ~10 GB of models (qwen3:14b +
#    nomic-embed-text) into the ollama_models volume — later boots are instant.
docker compose --profile desktop up -d

# 4. Seed the source list into the fresh database
docker compose exec api python -m toast.seed
```

Then open the workstation at **http://localhost:3005** (API at `http://localhost:8400`,
public-site preview at `http://localhost:8400/site/`).

**Windows one-click:** instead of steps 3–4 you can double-click **`ToastReport.cmd`** —
it brings up the Docker stack, starts the operator UI on port 3005, and opens it in a
standalone app window (`scripts/launch.ps1`).

**Verify:**
```bash
docker compose ps                       # every service "healthy"
curl http://127.0.0.1:8400/healthz      # {"service":"api","ok":true,...}
docker compose exec llm ollama list     # qwen3:14b + nomic-embed-text present
```

**Stop:** `docker compose --profile desktop down` (add `-v` to also drop the DB and
downloaded models). **UI outside Docker:** `npm --prefix operator-ui run dev -- -p 3005`
(proxies `/api/*` to `:8400`).

**No NVIDIA GPU?** Either set `LLM_MODEL=qwen3:8b` (fits ~8 GB / slower on CPU) or point
`LLM_BASE_URL` at a cloud OpenAI-compatible endpoint with `LLM_API_KEY`, and remove the
`deploy.resources` GPU reservation from the `llm` service in `docker-compose.yml`.

### One-shot AI-agent setup prompt

Paste this into an AI coding agent with shell access (e.g. Claude Code) to install and
configure the stack on a fresh machine:

```text
Set up "Toast Report OPs", a local-first, Docker-based California wildfire
intelligence system, on this machine — end to end — and report status at each step.

Facts:
- Repo (private): https://github.com/Fablestarexpanse/ToastReportOps
- Runs as a Docker Compose stack (profile "desktop", 8 services) plus a Next.js
  operator UI on port 3005. API on 8400. Localhost-only, single operator, no auth.
- Local LLM via in-stack Ollama: qwen3:14b + nomic-embed-text, auto-pulled on first
  boot (~10 GB). NVIDIA GPU with >=12 GB VRAM strongly recommended.

Do this:
1. Check prerequisites; ask me before installing any system software:
   - Docker Desktop installed and running (Windows: WSL2 backend on).
   - If an NVIDIA GPU is present, confirm containers can use it:
     `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi`.
   - git present; Node 20+ if the UI will run outside Docker.
2. Clone the repo (I'll authorize access if prompted) and cd into it.
3. `cp .env.example .env`. Keep the local-LLM defaults. Do NOT invent secrets —
   leave every optional key blank.
4. If this machine has NO usable NVIDIA GPU, stop and tell me, then either set
   LLM_MODEL=qwen3:8b, or (if I provide one) set LLM_BASE_URL + LLM_API_KEY to a
   cloud OpenAI-compatible endpoint and remove the deploy.resources GPU reservation
   from the `llm` service in docker-compose.yml. Confirm the choice with me first.
5. `docker compose --profile desktop up -d`. First boot pulls ~10 GB of models —
   poll `docker compose exec llm ollama list` until BOTH qwen3:14b and
   nomic-embed-text appear.
6. Seed sources: `docker compose exec api python -m toast.seed`.
7. Verify: `docker compose ps` all healthy; `curl http://127.0.0.1:8400/healthz`
   returns ok; UI answers on http://localhost:3005 (start it with
   `npm --prefix operator-ui run dev -- -p 3005` if needed; on Windows you may run
   ToastReport.cmd instead).
8. Report the workstation URL, the API URL, which LLM is active, and anything needing
   my attention (missing GPU, blank optional keys).

Rules: do not commit or push; do not fabricate keys or credentials; bind everything to
localhost; if a step fails, show the exact error and stop for my input.
```

## Core principles

1. **Local-first, append-only.** SQLite is the primary store. The fact ledger is
   append-only; corrections are supersession records, never edits. Frozen incidents
   are immutable (enforced by DB triggers, not convention).
2. **Bi-temporal facts.** Every fact records `observed_at` (when it happened) and
   `ingested_at` (when we received it). Stale news = large divergence between the two.
3. **Everything is a pipeline stage.** ingest → normalize → extract → geocode →
   attribute → corroborate → publish. Raw source material is preserved before any
   processing.
4. **Trust is data, not config.** Five-tier source trust (T1 official → T5 community)
   with per-source performance tracking. Promotions are computed, operator-approved.
5. **Human-in-the-loop publishing.** The AI drafts; nothing goes public without
   explicit operator approval. Evacuation data is rendered verbatim from official
   sources — never paraphrased by an LLM.
6. **Plugin extensibility.** Sources, extractors, and publishers are adapters behind
   stable interfaces; new ones require zero core changes.

## Documents

| Doc | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, services, interfaces, operator UI |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phases 0–5 with tasks, acceptance criteria, demos |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, prompt-injection posture, SSRF rules |
| [docs/API-KEYS.md](docs/API-KEYS.md) | Every key/credential to register and what each unlocks |
| [docs/AUDIT-2026-07-20.md](docs/AUDIT-2026-07-20.md) | Audit findings (partly superseded — its header says which) |
| [docs/BACKUP-RESTORE.md](docs/BACKUP-RESTORE.md) | Backup layers + the executed restore drill, output recorded |
| [docs/CORROBORATION.md](docs/CORROBORATION.md) | Corroboration model tuning notes (audit R6) |
| [docs/OPS-PANEL-SPEC.md](docs/OPS-PANEL-SPEC.md) | OPS incident panel build spec (from concept review) |
| [docs/RADIO-SETUP.md](docs/RADIO-SETUP.md) | SDR scanner hardware setup feeding the RADIO tab |
| [docs/SATELLITE-MEDIA-GNIS-PLAN.md](docs/SATELLITE-MEDIA-GNIS-PLAN.md) | Plan: satellite layers, media/video, GNIS place names |
| [docs/UI-GAP-ANALYSIS.md](docs/UI-GAP-ANALYSIS.md) | Concept vs. built, panel by panel (2026-07-20 snapshot) |
| [backend/src/toast/migrations/0001_init.sql](backend/src/toast/migrations/0001_init.sql) | Full SQLite schema |
| [DECISIONS.md](DECISIONS.md) | Architectural decision log |
| [OPERATIONS.md](OPERATIONS.md) | Runbook (start/stop, backup/restore, PSPS mode) |
| [docs/mockups/](docs/mockups/) | Operator UI design reference |

## Layout

- `backend/` — Python workers, pipeline, FastAPI, migrations (uv-managed)
- `operator-ui/` — Next.js operator workstation (localhost only)
- `public-site/` — placeholder; the site is generated by `backend/src/toast/sitegen/`
  and previewed at `/site/`. Nothing publishes it — that is manual, by decision.
- `prompts/` — versioned LLM prompt files
- `infra/` — Litestream/B2 and PSPS degraded-mode configs
- `data/` — git-ignored: `toast.db`, raw archive (sha256 CAS), tiles
