"""Silence detector: tells the operator when the system has quietly stopped.

The health dots only help if someone is looking at the screen. The failure this
tool must not have is the quiet one — a feed that stopped answering, a worker
that died, a model that went away — because "no alerts" then reads as "no news".

Each pass computes the set of current problems, compares it with the set already
reported, and pushes to the ops chat only on change: a problem appearing, a
problem still present after REMIND_S, or everything recovering. State lives in
map_cache, like the other Telegram baselines, so a restart does not re-alert.
"""

from __future__ import annotations

import html
import json
import sqlite3
from typing import Any

import httpx

from toast.core.config import Settings
from toast.core.db import utcnow
from toast.core.logging import get_logger
from toast.pipeline import telegram_ops
from toast.queue.jobs import WAITING_FOR_MODEL

STATE_KEY = "watchdog:state"
REMIND_S = 6 * 3600
DEAD_WINDOW_S = 6 * 3600
DEAD_THRESHOLD = 3  # one dead job is noise; a cluster of them is a broken stage
STALL_S = 20 * 60  # a runnable job waiting this long means no worker is draining
WATER_STALE_S = 30 * 60
RESERVOIR_STALE_S = 12 * 3600
SILENT_FACTOR = 3  # a source is silent after this many missed intervals...
SILENT_FLOOR_S = 15 * 60  # ...and never sooner than this


def _now_epoch(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT unixepoch(?) AS n", (utcnow(),)).fetchone()["n"])


def find_problems(
    conn: sqlite3.Connection, *, llm_models: list[str] | None = None
) -> dict[str, str]:
    """{problem key: one-line description} for everything wrong right now."""
    problems: dict[str, str] = {}
    now = _now_epoch(conn)

    # a stage whose jobs keep exhausting their retries is broken, not unlucky
    for r in conn.execute(
        """SELECT stage, COUNT(*) AS n, MAX(last_error) AS err FROM jobs
           WHERE state = 'dead' AND ? - unixepoch(COALESCE(claimed_at, created_at)) < ?
           GROUP BY stage HAVING COUNT(*) >= ?""",
        (now, DEAD_WINDOW_S, DEAD_THRESHOLD),
    ):
        problems[f"dead:{r['stage']}"] = (
            f"{r['n']} '{r['stage']}' jobs gave up in 6h — {(r['err'] or '')[:80]}"
        )

    # nothing has drained the queue: worker down or wedged
    stalled = conn.execute(
        """SELECT MIN(unixepoch(created_at)) AS oldest FROM jobs
           WHERE state = 'queued' AND (run_after IS NULL OR unixepoch(run_after) <= ?)
             AND COALESCE(last_error, '') NOT LIKE ?""",
        (now, f"{WAITING_FOR_MODEL}%"),
    ).fetchone()["oldest"]
    # old is not enough: a requeued job keeps its created_at, so also ask whether
    # anything has been claimed lately
    claimed = conn.execute("SELECT MAX(unixepoch(claimed_at)) AS t FROM jobs").fetchone()["t"]
    draining = claimed is not None and now - claimed <= STALL_S
    if stalled is not None and now - stalled > STALL_S and not draining:
        problems["queue_stalled"] = (
            f"oldest runnable job has waited {(now - stalled) // 60} min — is the worker running?"
        )

    # jobs parked for a model are waiting on the operator, not on the worker
    waiting = conn.execute(
        """SELECT COUNT(*) AS n, MAX(last_error) AS err FROM jobs
           WHERE state = 'queued' AND last_error LIKE ?""",
        (f"{WAITING_FOR_MODEL}%",),
    ).fetchone()
    if waiting["n"]:
        problems["llm_model_missing"] = f"{waiting['n']} jobs waiting — {waiting['err'][:120]}"

    # an enabled, scheduled source that stopped completing polls
    for s in conn.execute(
        """SELECT s.id, s.name, s.tier, CAST(s.schedule AS INTEGER) AS every,
                  unixepoch(s.created_at) AS born,
                  (SELECT MAX(unixepoch(COALESCE(j.claimed_at, j.created_at))) FROM jobs j
                   WHERE j.stage = 'poll' AND j.state = 'done'
                     AND j.payload ->> 'source_id' = s.id) AS last_ok
           FROM sources s
           WHERE s.enabled = 1 AND CAST(s.schedule AS INTEGER) > 0"""
    ):
        limit = max(SILENT_FACTOR * s["every"], SILENT_FLOOR_S)
        last = s["last_ok"] or s["born"]
        if now - last > limit:
            problems[f"silent:{s['id']}"] = (
                f"{s['name']} (T{s['tier']}) has not completed a poll in {(now - last) // 60} min"
            )

    # flood season only: the WATER board is useless if its feed went quiet
    from toast.core.hazard import includes_flood

    if includes_flood(conn):
        row = conn.execute(
            "SELECT unixepoch(fetched_at) AS t FROM map_cache WHERE key = 'water:ca'"
        ).fetchone()
        if row is None or now - row["t"] > WATER_STALE_S:
            problems["water_stale"] = "flood gauges/alerts have not refreshed in over 30 min"

    # reservoir history is gathered in every season; if it was flowing, it should still be
    res = conn.execute("SELECT unixepoch(MAX(captured_at)) AS t FROM reservoir_readings").fetchone()
    if res["t"] is not None and now - res["t"] > RESERVOIR_STALE_S:
        problems["reservoirs_stale"] = "reservoir levels have not refreshed in over 12 h"

    # an empty sky and a refused feed look the same on the map
    from toast.pipeline.aircraft import feed_error

    air_error = feed_error(conn)
    if air_error:
        problems["aircraft_feed"] = f"aircraft feed is failing — {air_error}"

    if llm_models is not None and not llm_models:
        problems["llm"] = "the language model endpoint is unreachable or has no models"

    return problems


def run_watchdog(
    conn: sqlite3.Connection,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    *,
    llm_models: list[str] | None = None,
) -> dict[str, Any]:
    """One pass. Returns what it found and whether it notified."""
    if llm_models is None:
        from toast.llm.client import client_for

        llm_models = client_for(conn, settings).list_models(timeout_s=5.0)
    problems = find_problems(conn, llm_models=llm_models)

    state = telegram_ops._state(conn, STATE_KEY) or {"active": {}}
    active: dict[str, int] = state["active"]  # key -> epoch last alerted
    now = _now_epoch(conn)

    fresh = [k for k in problems if k not in active or now - active[k] >= REMIND_S]
    recovered = [k for k in active if k not in problems]

    cfg = telegram_ops._cfg(settings, conn)
    sent = False
    if cfg and (fresh or (recovered and not problems)):
        token, chat = cfg
        lines: list[str] = []
        if fresh:
            lines.append("🚨 <b>SYSTEM ATTENTION</b>")
            lines += [f"• {html.escape(problems[k])}" for k in sorted(fresh)[:8]]
            if len(fresh) > 8:
                lines.append(f"…and {len(fresh) - 8} more")
        else:
            lines.append("✅ <b>System recovered</b> — all checks clear.")
        own = client is None
        client = client or httpx.Client(timeout=30)
        try:
            sent = telegram_ops._send(client, token, chat, "\n".join(lines))
        finally:
            if own:
                client.close()

    # only advance state for what was actually delivered (or when nobody to tell)
    if sent or not cfg:
        for k in fresh:
            active[k] = now
    new_active = {k: v for k, v in active.items() if k in problems}
    if sent or not cfg:
        state["active"] = new_active
        telegram_ops._save(conn, STATE_KEY, state)
    if problems:
        get_logger("watchdog").warning("watchdog_problems", problems=json.dumps(sorted(problems)))
    return {"problems": sorted(problems), "notified": sent, "recovered": sorted(recovered)}
