"""Content-addressed raw archive.

Every fetched body is preserved verbatim under data/raw/<aa>/<sha256> before
any processing, and indexed in raw_blobs. Idempotent: same content, same key.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from toast.core.db import utcnow


def store_raw(
    conn: sqlite3.Connection,
    data_dir: Path,
    body: bytes,
    media_type: str,
    url: str | None = None,
) -> str:
    sha = hashlib.sha256(body).hexdigest()
    dest = data_dir / "raw" / sha[:2] / sha
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(dest)
    conn.execute(
        """INSERT OR IGNORE INTO raw_blobs (sha256, media_type, size_bytes, storage, path,
                                            fetched_at, url)
           VALUES (?, ?, ?, 'local', ?, ?, ?)""",
        (sha, media_type, len(body), str(dest.relative_to(data_dir)), utcnow(), url),
    )
    return sha
