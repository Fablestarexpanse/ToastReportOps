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


def latest_fact(conn: sqlite3.Connection, incident_id: str, kind: str) -> sqlite3.Row | None:
    """Newest non-superseded fact of `kind` for the incident."""
    return conn.execute(
        """SELECT * FROM facts
           WHERE incident_id = ? AND kind = ?
             AND id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
           ORDER BY observed_at DESC, ingested_at DESC LIMIT 1""",
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
    return fact_id


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
