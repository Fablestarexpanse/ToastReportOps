# Backup & restore

The workstation runs in a house inside a fire zone. **A backup that only exists
on this machine is not a backup.** This document is the drill — it has been
executed, and the output is recorded at the bottom.

## What runs automatically

| Layer | What | Where | When |
|---|---|---|---|
| Continuous | Litestream WAL replication of `toast.db` | `data/replica/` (local), B2 when configured | always |
| Nightly | Verified `VACUUM INTO` snapshot, gzipped + manifest | `data/backups/toast-<stamp>.db.gz` | every 24h (scheduler enqueues a `backup` job) |
| Nightly | Retention prune | keeps the newest 7 snapshots | with each snapshot |
| Nightly | Offsite upload | Backblaze B2 `toast/` prefix | only when B2 keys exist |

Every snapshot is opened and checked before it counts: `PRAGMA integrity_check`,
`PRAGMA foreign_key_check`, and row counts for facts / incidents / envelopes /
drafts / sources / publications. The counts are written to a `.json` manifest
beside the snapshot, so a restore can be proven correct rather than assumed.

Run one on demand: **OPTIONS (⚙) → BACKUPS → BACK UP NOW**, or
`curl -X POST localhost:8400/backups/run`.

## Turning on offsite (do this — it's the last real gap)

1. backblaze.com → sign up (10 GB free) → **B2 Cloud Storage** → Create Bucket
   (private), name it e.g. `toast-report-archive`.
2. **App Keys** → Add a New Application Key, scoped to that bucket, with
   read+write. Copy the keyID and applicationKey — the key is shown **once**.
3. Put them in `.env`:
   ```
   B2_KEY_ID=...
   B2_APPLICATION_KEY=...
   B2_BUCKET=toast-report-archive
   ```
4. Uncomment the `s3` replica block in `infra/litestream.yml` (continuous
   replication in addition to nightly snapshots).
5. `docker compose --profile desktop up -d` — then OPTIONS → BACKUPS; the red
   NO OFFSITE COPY banner disappears and the next snapshot uploads.

Nothing else changes: with no keys the system keeps making verified local
snapshots and simply tells you they're at risk.

## Restore drill

Assume the desktop is gone. On a clean machine:

```bash
# 1. code
git clone https://github.com/Fablestarexpanse/ToastReportOps
cd ToastReportOps
cp .env.example .env          # fill in keys

# 2. get the newest snapshot
#    from B2:    b2 file download b2://<bucket>/toast/<file> .
#    from local: copy from data/backups/
gunzip -c toast-<stamp>.db.gz > data/toast.db

# 3. PROVE it before trusting it
python - <<'PY'
from pathlib import Path
from toast.backup import verify_snapshot
print(verify_snapshot(Path("data/toast.db")))
PY
# expect: {'ok': True, 'integrity': 'ok', 'foreign_key_violations': 0, 'counts': {...}}
# compare 'counts' against the snapshot's .json manifest — they must match.

# 4. raw archive (article/page snapshots referenced by facts)
#    rclone sync b2:<bucket>/raw data/raw    # when offsite raw sync is enabled

# 5. start
docker compose --profile desktop up -d
curl -s localhost:8400/healthz
```

Then confirm in the UI: ARCHIVE shows the fire list, an incident's LEDGER shows
its facts with supersession chains intact, and SOURCES lists the feeds. Ingest
resumes on the next poll cycle; nothing needs replaying by hand.

### What a restore does NOT bring back

- Jobs that were queued/running at the moment of loss (they re-enqueue on the
  next poll — no data loss, just a gap).
- Anything ingested between the last snapshot and the loss. Litestream's
  continuous replica closes that window to seconds; snapshots alone leave up to
  24h. **This is the reason to turn on the B2 s3 replica, not just snapshots.**

## Drill record

| Date | Performed | Result |
|---|---|---|
| 2026-07-20 | **Live drill on the working database.** Snapshot `toast-20260720T133248.db.gz` (1,111,589 bytes) → gunzip to a scratch path → open → verify → read the ledger back. | **PASS.** `integrity_check: ok`, 0 foreign-key violations. Counts matched the manifest exactly: facts 280, incidents 17, envelopes 278, drafts 15, sources 23. Incidents readable (3-1 Pit, Alturas, Biscar, Brook…), supersession links intact. |
| 2026-07-20 | Same path automated as `test_snapshot_restores_to_a_working_database` | **PASS** — runs in CI on every commit, so the drill can never silently rot. |
| _pending_ | Full clean-machine restore from B2 | blocked on B2 keys (step above) |
