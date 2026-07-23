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
