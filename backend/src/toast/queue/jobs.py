"""Job queue on the SQLite jobs table.

Claim = single UPDATE…RETURNING inside an IMMEDIATE transaction, so concurrent
workers never double-claim. Failure requeues with exponential backoff until
max_attempts, then the job goes to 'dead' and is preserved for inspection.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from toast.core.db import transaction, utcnow
from toast.core.ids import ulid


def enqueue(
    conn: sqlite3.Connection,
    stage: str,
    payload: dict[str, Any],
    *,
    priority: int = 5,
    max_attempts: int = 5,
    run_after: str | None = None,
) -> str:
    job_id = ulid()
    conn.execute(
        """INSERT INTO jobs (id, stage, payload, priority, state, max_attempts, run_after,
                             created_at)
           VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)""",
        (job_id, stage, json.dumps(payload), priority, max_attempts, run_after, utcnow()),
    )
    return job_id


def claim(
    conn: sqlite3.Connection, worker_id: str, stages: list[str] | None = None
) -> sqlite3.Row | None:
    """Claim the highest-priority runnable job, or None."""
    now = utcnow()
    stage_filter = ""
    params: list[Any] = [now]
    if stages:
        stage_filter = f"AND stage IN ({','.join('?' * len(stages))})"
        params.extend(stages)
    with transaction(conn):
        return conn.execute(
            f"""UPDATE jobs
                SET state='running', claimed_by=?, claimed_at=?, attempts=attempts+1
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE state='queued' AND (run_after IS NULL OR run_after <= ?)
                    {stage_filter}
                    ORDER BY priority, created_at LIMIT 1)
                RETURNING *""",
            [worker_id, now, *params],
        ).fetchone()


def complete(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("UPDATE jobs SET state='done', last_error=NULL WHERE id=?", (job_id,))


def fail(
    conn: sqlite3.Connection, job: sqlite3.Row, error: str, *, backoff_base_s: float = 5.0
) -> str:
    """Requeue with exponential backoff, or dead-letter after max_attempts.

    Returns the new state ('queued' or 'dead').
    """
    if job["attempts"] >= job["max_attempts"]:
        conn.execute("UPDATE jobs SET state='dead', last_error=? WHERE id=?", (error, job["id"]))
        return "dead"
    delay = backoff_base_s * (2 ** (job["attempts"] - 1))
    run_after = (datetime.now(UTC) + timedelta(seconds=delay)).strftime("%Y-%m-%dT%H:%M:%S.%f")[
        :-3
    ] + "Z"
    conn.execute(
        "UPDATE jobs SET state='queued', run_after=?, last_error=? WHERE id=?",
        (run_after, error, job["id"]),
    )
    return "queued"


def counts_by_state(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {
        r["state"]: r["n"]
        for r in conn.execute("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")
    }
    # `dead` is a lifetime total, so a health light driven by it goes red once
    # and stays red forever — an alarm that cannot clear is one you stop
    # reading. `dead_recent` is the number that means "something is wrong now".
    counts["dead_recent"] = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE state = 'dead'"
        " AND unixepoch('now') - unixepoch(created_at) < ?",
        (DEAD_RECENT_S,),
    ).fetchone()["n"]
    return counts


# Publishing is excluded from bulk revival on purpose: a dead publish job may
# have already put a post in front of the public, and re-running it is the
# duplicate we spent the publications state machine avoiding.
NEVER_AUTO_REQUEUE = ("publish",)

DEAD_RECENT_S = 6 * 3600


def dead_summary(conn: sqlite3.Connection, *, limit: int = 50) -> dict[str, Any]:
    """What died, why, and how long ago — the dead-letter queue was preserved
    'for inspection' but nothing ever inspected it."""
    by_stage = [
        dict(r)
        for r in conn.execute(
            """SELECT stage, COUNT(*) AS n,
                      MAX(created_at) AS latest,
                      SUM(CASE WHEN unixepoch('now') - unixepoch(created_at) < ?
                               THEN 1 ELSE 0 END) AS recent
               FROM jobs WHERE state = 'dead'
               GROUP BY stage ORDER BY n DESC""",
            (DEAD_RECENT_S,),
        )
    ]
    jobs_ = [
        dict(r)
        for r in conn.execute(
            """SELECT id, stage, payload, attempts, last_error, created_at
               FROM jobs WHERE state = 'dead'
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
    ]
    return {
        "by_stage": by_stage,
        "jobs": jobs_,
        "recoverable": [s["stage"] for s in by_stage if s["stage"] not in NEVER_AUTO_REQUEUE],
    }


def requeue_dead(
    conn: sqlite3.Connection, *, stage: str | None = None, job_ids: list[str] | None = None
) -> int:
    """Give dead jobs another life, with their attempt counter reset.

    Most dead jobs are the residue of a transient outage — the LLM being briefly
    unreachable took 20 articles' claims out of the ledger with nothing to say
    so. Their raw envelopes were archived, so the work is recoverable; it just
    needed a way to ask.
    """
    clauses = ["state = 'dead'", f"stage NOT IN ({','.join('?' * len(NEVER_AUTO_REQUEUE))})"]
    params: list[Any] = list(NEVER_AUTO_REQUEUE)
    if stage:
        clauses.append("stage = ?")
        params.append(stage)
    if job_ids:
        clauses.append(f"id IN ({','.join('?' * len(job_ids))})")
        params.extend(job_ids)
    cur = conn.execute(
        f"UPDATE jobs SET state = 'queued', attempts = 0, run_after = NULL,"
        f" claimed_by = NULL, claimed_at = NULL WHERE {' AND '.join(clauses)}",
        params,
    )
    return cur.rowcount
