# Operations Runbook

Sections below are seeded now and filled in as each capability lands (phase noted).

## Launch as a standalone app (recommended)

Double-click **`ToastReport.cmd`** in the repo root. It starts the Docker
backend, starts the operator UI (port 3005), and opens the workstation in an
Edge **app window** — its own taskbar icon, no browser chrome. Safe to run
repeatedly; anything already running is reused. Pin the window to the taskbar
for one-click relaunch. (Public site preview: http://localhost:8400/site/)

To stop: close the window, then `docker compose --profile desktop down` if you
want the backend down too (it's fine to leave it running).

## Start / stop (Phase 0)

```sh
docker compose --profile desktop up -d     # everything
docker compose --profile desktop down
docker compose restart <service>           # any service is individually restartable
docker compose logs -f <service>
```

Health: every service exposes `/healthz`; the operator UI top bar shows
GPU / FEEDS / SOCIAL / BUILD dots wired to these.

## Configuration (Phase 0)

All config via environment variables — copy `.env.example` to `.env` and fill in.
No hardcoded paths; `data/` location is configurable for the desktop→server move.

## Backup / restore (Phase 0 local, Phase 5 drilled)

- Continuous: Litestream replicates `toast.db` to B2 (`infra/litestream.yml`).
- Nightly: `VACUUM INTO` snapshot + rclone sync of `data/raw/` to B2.
- **Restore drill** (to be documented step-by-step and actually executed in
  Phase 5): fresh machine → install → restore DB from B2 → verify ledger counts →
  rclone raw archive → compose up.

## Adding a source (Phase 1 manual, Phase 3 AI probe)

1. Operator UI → SOURCES → ADD SOURCE, or insert a `sources` row.
2. Set adapter, config, schedule, starting tier, and `tos_notes` (legal posture +
   review date). robots.txt override only via explicit `robots_override` flag.
3. Phase 3+: paste a URL and the AI probe detects what it is (YouTube channel →
   live-stream watcher; article site → scraper; audio URL → Whisper pipeline).

## PSPS degraded mode (Phase 5)

When the desktop loses power, a free-tier cloud VM runs the same codebase with
`docker compose --profile degraded up`: official-API polling + static site updates
only. Cutover: DNS unchanged (site is on Cloudflare Pages); start sentinel, confirm
site updates flow; on power restore, stop sentinel, Litestream-restore any delta,
resume desktop profile.

## Active fires vs. historical (source of truth)

The default poller is **CAL FIRE active incidents** (`incidents.fire.ca.gov`) —
only fires CAL FIRE currently lists as active are auto-ingested. The WFIGS source
is seeded **disabled** (it lists the whole current-year backlog with no reliable
active flag) and is used only by the ARCHIVE search/import flow.

To bring an older/closed fire into the ledger: ARCHIVE mode → SEARCH HISTORICAL
FIRES → IMPORT. Imported fires land as `contained` incidents with their final
facts and never re-enter the active OPS view.

## Reset & re-sync the dev database

When source config changes materially (e.g. switching the active source), wipe
the local DB and re-ingest — all stored data is machine-ingested public fire data,
so nothing operator-authored is lost:

```sh
docker compose --profile desktop down
rm -f data/toast.db data/toast.db-wal data/toast.db-shm
docker compose --profile desktop up -d
docker compose exec worker python -m toast.seed
# CAL FIRE polls within ~2 min; confirm the active list:
curl -s http://127.0.0.1:8400/incidents | python -c "import sys,json;print(len(json.load(sys.stdin)['incidents']),'active')"
```

## Publishing (public static site)

Approved reports publish to a static site with zero server attack surface. The
`published` transition records a `publications` row (channel `site`) and enqueues
a `build_site` job; the generator writes `data/site/` (index + per-report pages,
each with sources, a burned-in timestamp, and a "verify with officials" notice).

- Preview locally: `http://127.0.0.1:8400/site/index.html` (the API serves
  `data/site/` for preview; production is Cloudflare Pages).
- **Deploy to Cloudflare Pages (deferred — needs a token):** set
  `CLOUDFLARE_API_TOKEN` + `CF_PAGES_PROJECT` in `.env`, then
  `npx wrangler pages deploy data/site --project-name=$CF_PAGES_PROJECT`. Wire
  this into a publisher step once the token is provided.
- **External channels** (X, Facebook, RSS, email, webhook) are separate
  publisher adapters, still deferred until their credentials/app-review are set
  up. They are NOT triggered by the site publish.

## Incident freeze / archive (Phase 5)

Contained + N days → automatic freeze. Frozen incidents reject all writes except
supersession corrections (DB trigger). Retrospective export produces a
self-contained incident bundle (ledger JSONL, media manifest, GeoJSON perimeters).
