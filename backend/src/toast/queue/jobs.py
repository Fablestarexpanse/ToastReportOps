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
    return {
        r["state"]: r["n"]
        for r in conn.execute("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")
    }
