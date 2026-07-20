"""Ship the raw archive offsite, incrementally.

The ledger snapshot is small and goes up whole every night. `data/raw/` is the
other half of the story: every response we ever fetched, 92 MB and growing about
25 MB a day. It is not reconstructable — a news page that moves or a feed that
rotates is gone, and with it the ability to show what a claim actually said when
we read it. A ledger whose evidence burned with the house is a ledger nobody can
check.

Content-addressed, so this is naturally incremental and idempotent: the key IS
the sha256, an existing key is by definition the same bytes, and nothing is ever
overwritten. Work is bounded per run — a first sync of thousands of blobs should
take several quiet nights, not one long stall that blocks the queue.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

import httpx

from toast.backup_b2 import BackupUploadError, _authorize, _bucket_id
from toast.core.config import Settings
from toast.core.db import utcnow
from toast.core.logging import get_logger

BATCH = 250  # blobs per run; keeps the worker responsive to live fire traffic
MAX_BLOB_BYTES = 25 * 1024 * 1024  # a single response bigger than this is skipped, loudly


def pending(conn: sqlite3.Connection) -> dict[str, Any]:
    """How much of the archive is not yet offsite."""
    row = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS bytes
           FROM raw_blobs WHERE sha256 NOT IN (SELECT sha256 FROM raw_offsite)"""
    ).fetchone()
    done = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS bytes FROM raw_offsite"
    ).fetchone()
    return {
        "pending": row["n"],
        "pending_bytes": row["bytes"],
        "offsite": done["n"],
        "offsite_bytes": done["bytes"],
    }


def sync_raw(
    conn: sqlite3.Connection,
    settings: Settings | None = None,
    *,
    limit: int = BATCH,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Upload up to `limit` not-yet-offsite blobs. Returns what moved."""
    s = settings or Settings()
    if not (s.b2_key_id and s.b2_application_key and s.b2_bucket):
        # not an error: an operator without keys is a documented state, not a
        # failure, and it must not dead-letter a nightly job
        return {"skipped": "b2 not configured", **pending(conn)}

    rows = conn.execute(
        """SELECT sha256, path, size_bytes FROM raw_blobs
           WHERE sha256 NOT IN (SELECT sha256 FROM raw_offsite)
           ORDER BY fetched_at LIMIT ?""",
        (limit,),
    ).fetchall()
    if not rows:
        return {"uploaded": 0, "bytes": 0, **pending(conn)}

    log = get_logger("rawsync")
    data_dir = Path(s.toast_data_dir)
    own = client is None
    client = client or httpx.Client(timeout=300)
    uploaded = skipped = 0
    total = 0
    try:
        auth = _authorize(s, client)
        bucket_id = _bucket_id(auth, s, client)
        api = auth["apiInfo"]["storageApi"]
        upload_url = None

        for row in rows:
            local = data_dir / row["path"]
            if not local.exists():
                # the index knows about a file the disk does not have. Recording
                # it as offsite would be a lie; leaving it pending forever would
                # wedge the queue. Say so and move on.
                log.warning("raw_blob_missing_locally", sha256=row["sha256"], path=row["path"])
                skipped += 1
                continue
            if row["size_bytes"] > MAX_BLOB_BYTES:
                log.warning("raw_blob_too_large", sha256=row["sha256"], bytes=row["size_bytes"])
                skipped += 1
                continue

            if upload_url is None:
                r = client.post(
                    f"{api['apiUrl']}/b2api/v3/b2_get_upload_url",
                    headers={"Authorization": auth["authorizationToken"]},
                    json={"bucketId": bucket_id},
                )
                if r.status_code != 200:
                    raise BackupUploadError(f"b2 get_upload_url failed: HTTP {r.status_code}")
                upload_url = r.json()

            data = local.read_bytes()
            sha = row["sha256"]
            r = client.post(
                upload_url["uploadUrl"],
                headers={
                    "Authorization": upload_url["authorizationToken"],
                    # mirrors the local layout: raw/<first two>/<sha256>
                    "X-Bz-File-Name": f"raw/{sha[:2]}/{sha}",
                    "Content-Type": "application/octet-stream",
                    "X-Bz-Content-Sha1": hashlib.sha1(data).hexdigest(),  # noqa: S324 - B2 requires SHA1
                },
                content=data,
            )
            if r.status_code == 401:
                upload_url = None  # token expired mid-run; the next loop re-fetches
                continue
            if r.status_code != 200:
                raise BackupUploadError(f"b2 upload failed: HTTP {r.status_code} {r.text[:120]}")

            conn.execute(
                "INSERT OR IGNORE INTO raw_offsite (sha256, bytes, uploaded_at) VALUES (?, ?, ?)",
                (sha, len(data), utcnow()),
            )
            uploaded += 1
            total += len(data)
    finally:
        if own:
            client.close()

    log.info("raw_synced", uploaded=uploaded, bytes=total, skipped=skipped)
    return {"uploaded": uploaded, "bytes": total, "skipped": skipped, **pending(conn)}
