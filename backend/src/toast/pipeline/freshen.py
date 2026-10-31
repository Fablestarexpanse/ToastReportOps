"""Pre-drafting freshness: a report starts with a fresh pull, never yesterday's.

The scheduler polls official sources on their own cadence, but a report is a
moment of publication — the operator hitting DRAFT deserves the current
numbers, not the last poll's. This re-polls the fast official APIs (CAL FIRE,
WFIGS, NWS CAP) inline and drains the normalize jobs those polls enqueued —
using the SAME atomic job claim the worker uses, so the two never collide —
then reports how fresh the ledger actually is. Bounded by a timeout: a slow or
down source delays a draft by seconds, never blocks it.
"""

from __future__ import annotations

import json
import sqlite3
from time import monotonic
from typing import Any

from toast.core.logging import get_logger
from toast.queue import jobs

OFFICIAL_ADAPTERS = ("calfire", "wfigs", "nws_cap")


def freshen_official_sources(
    conn: sqlite3.Connection, *, timeout_s: float = 25.0
) -> dict[str, Any]:
    """Poll official sources now and fold the results into the ledger. Returns
    what happened — the caller stamps it into the draft's honesty line."""
    from toast.pipeline.handlers import normalize_handler, poll_handler

    log = get_logger("freshen")
    t0 = monotonic()
    polled: list[str] = []
    errors: list[str] = []
    for src in conn.execute(
        "SELECT id, name FROM sources WHERE enabled = 1 AND adapter IN "
        f"({','.join('?' * len(OFFICIAL_ADAPTERS))})",
        OFFICIAL_ADAPTERS,
    ).fetchall():
        if monotonic() - t0 > timeout_s:
            errors.append("timeout before all sources were re-polled")
            break
        try:
            poll_handler(conn, {"source_id": src["id"]})
            polled.append(src["name"])
        except Exception as exc:  # a down source must not block the draft
            errors.append(f"{src['name']}: {str(exc)[:80]}")

    # fold the fresh envelopes into facts: drain normalize with the worker's own
    # atomic claim, so whoever grabs a job first simply owns it
    drained = 0
    while monotonic() - t0 < timeout_s:
        job = jobs.claim(conn, "api-freshen", stages=["normalize"])
        if job is None:
            break
        try:
            normalize_handler(conn, json.loads(job["payload"]))
            jobs.complete(conn, job["id"])
            drained += 1
        except Exception as exc:
            jobs.fail(conn, job, str(exc)[:200])

    newest = conn.execute("SELECT MAX(observed_at) AS t FROM facts WHERE tier <= 2").fetchone()["t"]
    out = {
        "polled": polled,
        "normalized": drained,
        "errors": errors,
        "data_as_of": newest,
        "took_s": round(monotonic() - t0, 1),
    }
    log.info("freshen_done", **out)
    return out
