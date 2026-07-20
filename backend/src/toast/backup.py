"""Backups — the copy that survives the house.

Litestream handles continuous replication; this module makes the *verified,
restorable* copies: a consistent `VACUUM INTO` snapshot, an integrity check and
row-count manifest proving the snapshot is actually usable, retention pruning,
and (when credentials exist) offsite upload to Backblaze B2.

Design rule: a backup that has never been restored is a rumor. Every snapshot
is opened, integrity-checked, and counted before it is considered good; the
manifest records what a restore should produce.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from toast.core.config import Settings
from toast.core.db import utcnow
from toast.core.logging import get_logger

# Tables whose row counts a restore must reproduce — the ledger's core.
COUNTED_TABLES = ("facts", "incidents", "envelopes", "drafts", "sources", "publications")

KEEP_SNAPSHOTS = 7  # daily snapshots retained locally


def snapshot_dir(settings: Settings | None = None) -> Path:
    s = settings or Settings()
    return s.toast_data_dir / "backups"


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for table in COUNTED_TABLES:
        try:
            out[table] = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        except sqlite3.Error:
            out[table] = -1
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_snapshot(path: Path) -> dict[str, Any]:
    """Open a snapshot and prove it is restorable: integrity check + counts.

    Read-only; never touches the live database."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        counts = _counts(conn)
    finally:
        conn.close()
    return {
        "ok": integrity == "ok" and not fk,
        "integrity": integrity,
        "foreign_key_violations": len(fk),
        "counts": counts,
    }


def create_snapshot(
    conn: sqlite3.Connection, settings: Settings | None = None, *, compress: bool = True
) -> dict[str, Any]:
    """Consistent snapshot of the live DB, verified and manifested.

    VACUUM INTO takes a transactionally consistent copy without stopping
    writers — safe to run against the live workstation."""
    s = settings or Settings()
    log = get_logger("backup")
    out_dir = snapshot_dir(s)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = utcnow().replace(":", "").replace("-", "")[:15]  # 20260720T132500
    raw_path = out_dir / f"toast-{stamp}.db"
    if raw_path.exists():
        raw_path.unlink()
    # sqlite needs a literal path here; the value is ours, never user input
    conn.execute("VACUUM INTO ?", (str(raw_path),))

    verification = verify_snapshot(raw_path)
    if not verification["ok"]:
        raw_path.unlink(missing_ok=True)
        raise RuntimeError(f"snapshot failed verification: {verification['integrity']}")

    final_path = raw_path
    if compress:
        gz_path = out_dir / f"{raw_path.name}.gz"
        with raw_path.open("rb") as src, gzip.open(gz_path, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        raw_path.unlink()
        final_path = gz_path

    manifest = {
        "file": final_path.name,
        "created_at": utcnow(),
        "bytes": final_path.stat().st_size,
        "sha256": _sha256(final_path),
        "compressed": compress,
        "verified": verification,
        "restore_hint": (
            f"gunzip -c {final_path.name} > toast.db"
            if compress
            else f"cp {final_path.name} toast.db"
        ),
    }
    (out_dir / f"{final_path.name}.json").write_text(json.dumps(manifest, indent=2))
    log.info(
        "snapshot_created",
        file=final_path.name,
        bytes=manifest["bytes"],
        facts=verification["counts"].get("facts"),
    )
    return manifest


def prune_snapshots(settings: Settings | None = None, *, keep: int = KEEP_SNAPSHOTS) -> list[str]:
    """Keep the newest `keep` snapshots; delete older ones with their manifests."""
    out_dir = snapshot_dir(settings)
    if not out_dir.exists():
        return []
    snaps = sorted(
        [
            p
            for p in out_dir.iterdir()
            if p.name.startswith("toast-") and not p.name.endswith(".json")
        ],
        key=lambda p: p.name,
        reverse=True,
    )
    removed = []
    for old in snaps[keep:]:
        old.unlink(missing_ok=True)
        (out_dir / f"{old.name}.json").unlink(missing_ok=True)
        removed.append(old.name)
    if removed:
        get_logger("backup").info("snapshots_pruned", removed=len(removed))
    return removed


def list_snapshots(settings: Settings | None = None) -> list[dict[str, Any]]:
    out_dir = snapshot_dir(settings)
    if not out_dir.exists():
        return []
    out = []
    for man in sorted(out_dir.glob("toast-*.json"), reverse=True):
        try:
            out.append(json.loads(man.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def backup_status(settings: Settings | None = None) -> dict[str, Any]:
    """What the operator needs to know: is there a recent, verified, OFFSITE copy?"""
    s = settings or Settings()
    snaps = list_snapshots(s)
    offsite = bool(s.b2_key_id and s.b2_application_key and s.b2_bucket)
    latest = snaps[0] if snaps else None
    return {
        "snapshots": len(snaps),
        "latest": latest,
        "latest_at": latest["created_at"] if latest else None,
        "offsite_configured": offsite,
        # The house is in a fire zone: local-only copies do not count as backed up.
        "at_risk": not offsite,
        "snapshot_dir": str(snapshot_dir(s)),
    }


def run_backup(conn: sqlite3.Connection, settings: Settings | None = None) -> dict[str, Any]:
    """Full nightly cycle: snapshot → verify → prune → offsite (when configured)."""
    s = settings or Settings()
    manifest = create_snapshot(conn, s)
    pruned = prune_snapshots(s)
    uploaded = None
    if s.b2_key_id and s.b2_application_key and s.b2_bucket:
        from toast.backup_b2 import upload_snapshot

        uploaded = upload_snapshot(snapshot_dir(s) / manifest["file"], s)
    return {"manifest": manifest, "pruned": pruned, "offsite": uploaded}
