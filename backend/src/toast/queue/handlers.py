"""Stage handler registry.

A handler is `(conn, payload) -> None`; raising marks the job failed (retry
with backoff, then dead-letter). Pipeline stages register here as they are
built in later phases. Phase 0 ships demo handlers used by tests and the
compose demo.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

Handler = Callable[[sqlite3.Connection, dict[str, Any]], None]

_REGISTRY: dict[str, Handler] = {}


def register(stage: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _REGISTRY[stage] = fn
        return fn

    return deco


def get(stage: str) -> Handler | None:
    return _REGISTRY.get(stage)


def payload_of(job: sqlite3.Row) -> dict[str, Any]:
    return json.loads(job["payload"])


@register("demo_noop")
def demo_noop(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Succeeds immediately."""


@register("demo_flaky")
def demo_flaky(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """Fails until the job's attempt count reaches payload['succeed_on_attempt'].

    Exercises retry/backoff and, with a high enough threshold, dead-lettering.
    """
    job_id = payload["job_id"]
    succeed_on = int(payload.get("succeed_on_attempt", 3))
    row = conn.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or row["attempts"] < succeed_on:
        raise RuntimeError(f"demo_flaky failing on attempt {row['attempts'] if row else '?'}")
