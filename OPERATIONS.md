# Operations Runbook

Sections below are seeded now and filled in as each capability lands (phase noted).

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

## Incident freeze / archive (Phase 5)

Contained + N days → automatic freeze. Frozen incidents reject all writes except
supersession corrections (DB trigger). Retrospective export produces a
self-contained incident bundle (ledger JSONL, media manifest, GeoJSON perimeters).
