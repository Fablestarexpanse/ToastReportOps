"""Channel-selected, optionally scheduled release of approved social drafts.

The human decision stays exactly where it was — nothing publishes itself, and
the drafts state machine still enforces approval before anything reaches a
publisher. What this adds is control over the fan-out: WHICH channels, and
WHEN. A queued publication with a future not_before sits visibly scheduled;
the release_due sweep (scheduler, every minute) creates its publish job when
the time comes. The `enqueued` flag is the idempotency guard — a sweep can run
a hundred times and each publication gets exactly one job.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.ids import ulid
from toast.core.logging import get_logger
from toast.queue import jobs


def release_draft(
    conn: sqlite3.Connection,
    draft_id: str,
    channels: list[str],
    *,
    at: str | None = None,
) -> dict[str, Any]:
    """Clear an approved social draft for release to the SELECTED channels.

    `at` (ISO, already validated) holds delivery until then. A draft already
    published can release to ADDITIONAL channels — a queued/sent channel is
    never doubled."""
    from toast.core.credentials import settings_with_credentials
    from toast.publishers import configured_publishers

    draft = conn.execute("SELECT status, kind FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if draft is None:
        raise ValueError("draft not found")
    if draft["kind"] != "social":
        raise ValueError("only social drafts release to channels")
    if draft["status"] not in ("approved", "published"):
        raise ValueError(f"draft is {draft['status']} — approve it first")

    available = {p.channel for p in configured_publishers(settings_with_credentials(conn))}
    unknown = [c for c in channels if c not in available]
    if unknown:
        raise ValueError(f"channel(s) not configured: {', '.join(unknown)}")
    if not channels:
        raise ValueError("no channels selected")

    already = {
        r["channel"]
        for r in conn.execute(
            """SELECT channel FROM publications
               WHERE draft_id = ? AND status IN ('queued', 'sent', 'unknown')""",
            (draft_id,),
        )
    }
    now = utcnow()
    if draft["status"] == "approved":
        conn.execute(
            "UPDATE drafts SET status = 'published', updated_at = ? WHERE id = ?",
            (now, draft_id),
        )

    created = []
    for channel in channels:
        if channel in already:
            continue  # queued/sent/unknown — doubling is the one unforgivable bug
        pub_id = ulid()
        conn.execute(
            """INSERT INTO publications (id, draft_id, channel, status, not_before, enqueued)
               VALUES (?, ?, ?, 'queued', ?, 0)""",
            (pub_id, draft_id, channel, at),
        )
        created.append({"publication_id": pub_id, "channel": channel})
    conn.commit()
    enqueued = release_due(conn)  # immediate releases go out on this same call
    get_logger("release").info(
        "draft_released",
        draft=draft_id,
        channels=[c["channel"] for c in created],
        scheduled_for=at,
        enqueued_now=enqueued,
    )
    return {
        "draft_id": draft_id,
        "created": created,
        "skipped_already_active": sorted(already & set(channels)),
        "scheduled_for": at,
        "enqueued_now": enqueued,
    }


def release_due(conn: sqlite3.Connection) -> int:
    """Create publish jobs for every queued publication whose time has come."""
    rows = conn.execute(
        """SELECT id, draft_id, channel FROM publications
           WHERE status = 'queued' AND enqueued = 0
             AND (not_before IS NULL OR unixepoch(not_before) <= unixepoch('now'))"""
    ).fetchall()
    for r in rows:
        # flag first: if enqueue then crashed we retry the job layer, not re-flag
        conn.execute("UPDATE publications SET enqueued = 1 WHERE id = ?", (r["id"],))
        jobs.enqueue(
            conn,
            "publish",
            {"draft_id": r["draft_id"], "channel": r["channel"], "publication_id": r["id"]},
            priority=3,
        )
    conn.commit()
    return len(rows)


def retry_publication(conn: sqlite3.Connection, publication_id: str) -> dict[str, Any]:
    """Deliberate re-send of a DEFINITIVELY FAILED publication.

    'failed' means the platform answered no (bad token, 4xx) — nothing went
    out, so a retry is safe. 'unknown' means we never heard back and the post
    may be live: retrying that is how duplicate posts happen, so it is refused
    here exactly as it is refused in the job layer."""
    pub = conn.execute(
        "SELECT status, draft_id, channel FROM publications WHERE id = ?", (publication_id,)
    ).fetchone()
    if pub is None:
        raise ValueError("publication not found")
    if pub["status"] != "failed":
        raise ValueError(
            f"only failed publications can retry (this one is '{pub['status']}' — "
            "check the account before re-sending an unknown)"
        )
    conn.execute(
        "UPDATE publications SET status = 'queued', enqueued = 1, receipt = NULL WHERE id = ?",
        (publication_id,),
    )
    jobs.enqueue(
        conn,
        "publish",
        {"draft_id": pub["draft_id"], "channel": pub["channel"], "publication_id": publication_id},
        priority=3,
    )
    conn.commit()
    get_logger("release").info("publication_retried", publication=publication_id)
    return {"publication_id": publication_id, "channel": pub["channel"], "state": "queued"}
