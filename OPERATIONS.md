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

Health: `api` exposes `/healthz` and `/queue`. The operator UI top bar shows
two dots — **API** (is the backend answering) and **FEEDS** (are jobs dying
right now: `dead_recent`, a 6-hour window, not the lifetime total) — plus a
queue chip. **Click the queue chip** to open the dead-letter panel: what died,
why, how long ago, and a RETRY button per stage. Publishing is deliberately
excluded from bulk retry, because a dead publish job may already have posted.

## Configuration (Phase 0)

All config via environment variables — copy `.env.example` to `.env` and fill in.
No hardcoded paths; `data/` location is configurable for the desktop→server move.

## Never open toast.db read-write from the Windows host

The containers own the database. Opening it read-write from a host Python/CLI
session (anything that runs `PRAGMA journal_mode=WAL`, including
`toast.core.db.connect`) writes `toast.db-wal` / `toast.db-shm` with Windows
SQLite, and the Linux container's SQLite then refuses the file:

```
sqlite3.OperationalError: unable to open database file   # or: SQL logic error
```

Every service crash-loops on startup. The data is fine — this is a locking/
sidecar-file conflict, not corruption.

**Read-only from the host is always safe:**

```python
sqlite3.connect("file:data/toast.db?mode=ro", uri=True)   # do this
```

**Recovery** (verify integrity first, then drop the stale sidecars):

```bash
python -c "import sqlite3;c=sqlite3.connect('file:data/toast.db?mode=ro',uri=True);print(c.execute('PRAGMA integrity_check').fetchone())"
docker compose --profile desktop stop api worker scheduler litestream
rm -f data/toast.db-wal data/toast.db-shm     # only when -wal is 0 bytes
docker compose --profile desktop up -d
```

If `-wal` is NOT empty it holds uncommitted transactions: check it out with a
host **read-only** open (which replays the WAL), `VACUUM INTO` a fresh file,
and swap that in rather than deleting the WAL.

For ad-hoc queries against live data, prefer the API or
`docker compose exec worker python …` so the container owns the handle.

## Backup / restore

**Full procedure, with the drill that was actually executed and its recorded
output: [docs/BACKUP-RESTORE.md](docs/BACKUP-RESTORE.md).** Read that one, not
this summary.

What exists **today**, stated exactly (verified 20JUL2026 by a full round trip:
upload → list → download → sha256 match → integrity_check → row counts):

| | status |
|---|---|
| Nightly `VACUUM INTO` snapshot, verified + secret-stripped | **working**, local `data/backups/` |
| Snapshot uploaded to Backblaze B2 | **working** — restore verified from the B2 copy, not just its presence |
| `data/raw/` (every archived source response) offsite | **working** — 1,023 blobs / 93 MB replicated; syncs every 2h in bounded batches |
| Restore drill on a clean database | **executed**, output recorded in docs/BACKUP-RESTORE.md |
| Litestream continuous replication | **local disk only** — the s3 block in `infra/litestream.yml` is still commented out |

So: the ledger and its evidence are both offsite. What is NOT covered is the gap
between nightly snapshots — lose the machine at 23:00 and you lose that day's
facts back to the last snapshot. Closing that needs Litestream pointed at B2,
which reads `.env` rather than the credentials table (that container is not the
API), so it needs `B2_KEY_ID` / `B2_APPLICATION_KEY` / `B2_BUCKET` in `.env` too.

Raw sync runs automatically every 2 hours and on demand:

```sh
curl -X POST http://127.0.0.1:8400/backups/raw-sync   # a batch of up to 250 blobs
```

Progress is shown in OPTIONS → BACKUPS, which reports the raw archive separately
from the snapshot on purpose: "backed up" must never quietly mean half of it.

Secrets never ride along: `_strip_secrets` deletes the credentials table and
scrubs known key values out of `jobs.last_error` before a snapshot is uploaded.
A restore therefore comes back with no credentials — re-enter them in OPTIONS.

## Adding a source (Phase 1 manual, Phase 3 AI probe)

1. Operator UI → SOURCES → ADD SOURCE, or insert a `sources` row.
2. Set adapter, config, schedule, starting tier, and `tos_notes` (legal posture +
   review date). robots.txt override only via explicit `robots_override` flag.
3. Phase 3+: paste a URL and the AI probe detects what it is (YouTube channel →
   live-stream watcher; article site → scraper; audio URL → Whisper pipeline).

## PSPS degraded mode

A free-tier cloud VM runs the same codebase with
`docker compose --profile degraded up`. The profile contains `api`, `worker`,
and `scheduler` — enough to keep ingesting official feeds and to answer the
operator UI. It deliberately excludes the GPU-adjacent and research services
(`searxng`, `ldr`), which need hardware the sentinel does not have.

**What this does NOT do yet, so nobody plans around it:**

- **No shared replication target.** Litestream's only replica is a local file
  path, so there is no "Litestream-restore the delta" step — the sentinel's
  ledger and the desktop's ledger are separate databases that will diverge.
  Reconciling them is unsolved and blocked on B2 (above).
- **No public-site cutover.** Publishing is manual by operator decision, so the
  sentinel does not push a site anywhere.

Treat degraded mode as *"keep watching the fires while the power is out"*, not
as failover. Run it, use the UI, and when power returns, stop the sentinel and
go back to the desktop profile — knowing the two ledgers diverged.

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

## Deep research (Local Deep Research + SearXNG)

On-demand agentic web research: DEEP RESEARCH buttons on WATCH candidates and
the OPS incident panel queue a background run; the cited dossier lands as a
`kind='analysis'` **draft in NEWSROOM** (never directly in the ledger — feed its
source URLs through point-at-URL to ledger-ize facts).

- Services: `searxng` + `ldr` (desktop profile). LDR web UI: http://localhost:5000
- **One-time setup**: register the service account in the LDR web UI using
  `LDR_USERNAME`/`LDR_PASSWORD` from `.env` (its registration form is
  browser-only; done 2026-07-20 for this machine). Fresh installs: open
  http://localhost:5000/auth/register once.
- Research runs poll at low priority — live ingest preempts them. LDR's login
  rate-limiter is aggressive; jobs park for 10 min when limited.

## Publishing

**The newsroom does not push to any website.** Publishing to the web is a
manual act by the operator, by decision — `published` means "cleared for
release", not "sent somewhere".

- **Take the text:** NEWSROOM → **⧉ COPY**, or
  `GET /drafts/<id>/export?fmt=markdown|html|text` for a clean copy with the
  source attribution intact. Paste it into your own site.
- **Preview the rendered version:** `POST /site/build` builds the local static
  site on demand; read it at `http://127.0.0.1:8400/site/index.html`. This job
  only ever runs when you ask for it.
- **Social channels** (Bluesky, X, Facebook) DO fire automatically on
  publishing a `kind='social'` draft — but only for channels whose credentials
  exist in OPTIONS → API KEYS. With none set, publishing sends nothing
  anywhere.

When you are ready to wire the public site into your own hosting, the generator
in `toast/sitegen/` already produces a self-contained `data/site/` directory
(index + per-report pages, each with sources, a burned-in timestamp, and a
"verify with officials" notice) — point your deploy at that.

## Incident freeze / archive (Phase 5)

Contained + N days → automatic freeze. Frozen incidents reject all writes except
supersession corrections (DB trigger). Retrospective export produces a
self-contained incident bundle (ledger JSONL, media manifest, GeoJSON perimeters).
