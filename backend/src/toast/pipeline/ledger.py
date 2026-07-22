"""Ledger writes — the only module that inserts facts.

Supersession, not edits: when a structured value (acreage, containment, …)
changes for an incident, the new fact records `supersedes` = the fact it
replaces. Unchanged values are skipped (no duplicate facts).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.ids import ulid
from toast.core.timestamps import require_iso


# THE canonical order for "what is the current number?". Recency alone was
# wrong twice over: a media article with a future-stamped observed_at owned a
# fire's acreage for hours, and Google News figures outranked CAL FIRE between
# official polls. So: facts claiming to be from the future rank last; official
# sources (tier 1-2) beat media/community (tier 3+); within a group, newest
# first. Every roundup, graphic, draft and panel number flows through this.
def value_order(alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    return (
        f"({p}observed_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')) ASC, "
        f"({p}tier >= 3) ASC, {p}observed_at DESC, {p}ingested_at DESC"
    )


def latest_fact(conn: sqlite3.Connection, incident_id: str, kind: str) -> sqlite3.Row | None:
    """The current non-superseded fact of `kind` — official-first, see value_order."""
    return conn.execute(
        f"""SELECT * FROM facts
           WHERE incident_id = ? AND kind = ?
             AND id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
           ORDER BY {value_order()} LIMIT 1""",
        (incident_id, kind),
    ).fetchone()


def write_fact(
    conn: sqlite3.Connection,
    *,
    incident_id: str | None,
    envelope_id: str,
    source_id: str,
    tier: int,
    content: str,
    kind: str,
    observed_at: str,
    confidence: float = 0.99,
    acreage: float | None = None,
    containment_pct: float | None = None,
    evac_status: str | None = None,
    structured: dict[str, Any] | None = None,
    lat: float | None = None,
    lon: float | None = None,
    geo_method: str | None = None,
    supersedes: str | None = None,
    raw_ref: str | None = None,
    prompt_version: str | None = None,
) -> str:
    fact_id = ulid()
    # every fact write funnels through here, so this is where a timestamp the
    # database cannot do arithmetic on gets stopped
    observed_at = require_iso(observed_at, field="observed_at")
    conn.execute(
        """INSERT INTO facts
           (id, incident_id, envelope_id, source_id, tier, content, kind,
            acreage, containment_pct, evac_status, structured, confidence,
            observed_at, ingested_at, lat, lon, geo_method, supersedes, raw_ref,
            prompt_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fact_id,
            incident_id,
            envelope_id,
            source_id,
            tier,
            content,
            kind,
            acreage,
            containment_pct,
            evac_status,
            json.dumps(structured or {}),
            confidence,
            observed_at,
            utcnow(),
            lat,
            lon,
            geo_method,
            supersedes,
            raw_ref,
            prompt_version,
        ),
    )
    if kind == "evac" and evac_status in ("order", "warning", "shelter_in_place"):
        # a new evacuation instruction means the Cal OES zone polygons may be
        # changing RIGHT NOW — resync immediately and re-check shortly after,
        # instead of waiting out the cache TTL
        _queue_evac_zone_sync(conn, incident_id)
    return fact_id


def _queue_evac_zone_sync(conn: sqlite3.Connection, incident_id: str | None) -> None:
    from datetime import UTC, datetime, timedelta

    from toast.queue import jobs

    try:
        jobs.enqueue(conn, "evac_zone_sync", {"incident_id": incident_id}, priority=2)
        # zones often lag the first news of an order — check again in 5 minutes
        later = (
            (datetime.now(tz=UTC) + timedelta(minutes=5))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        jobs.enqueue(
            conn, "evac_zone_sync", {"incident_id": incident_id, "followup": True},
            priority=2, run_after=later,
        )
    except Exception:
        # the fact write must never fail because a convenience sync could not
        # be queued
        pass


def write_value_fact(
    conn: sqlite3.Connection,
    *,
    incident_id: str,
    kind: str,
    value_column: str,
    value: float | str | None,
    content: str,
    observed_at: str,
    **fact_kwargs: Any,
) -> str | None:
    """Write a fact for a structured value, superseding the previous one iff
    the value changed. Returns the new fact id, or None if unchanged."""
    if value is None:
        return None
    from toast.core.db import transaction

    # read-prev + write-superseding must be one unit: two writers racing the
    # same chain could otherwise both supersede the same prev fact
    with transaction(conn):
        prev = latest_fact(conn, incident_id, kind)
        if prev is not None:
            if prev[value_column] == value:
                return None
            # never supersede with older information
            if prev["observed_at"] >= observed_at:
                return None
        merged: dict[str, Any] = {value_column: value, **fact_kwargs}
        return write_fact(
            conn,
            incident_id=incident_id,
            kind=kind,
            content=content,
            observed_at=observed_at,
            supersedes=prev["id"] if prev is not None else None,
            **merged,
    )
