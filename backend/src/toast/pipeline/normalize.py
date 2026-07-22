"""Normalize stage — envelope → incidents + facts.

Deterministic for official sources (no LLM in Phase 1): WFIGS reports upsert
incidents and write value facts with supersession; FIRMS hotspots geo-attach
to nearby incidents; CAP alerts become unattributed weather/alert facts.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.pipeline import incidents as inc
from toast.pipeline.ledger import write_fact, write_value_fact

# the WFIGS creation gate: below BOTH thresholds, a new dispatch record does
# not become an incident (its later growth will, via the modified-record poll)
WFIGS_MIN_ACRES = 10.0
WFIGS_MIN_PERSONNEL = 15


def _envelope(conn: sqlite3.Connection, envelope_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM envelopes WHERE id = ?", (envelope_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"envelope {envelope_id} not found")
    return row


def _source_tier(conn: sqlite3.Connection, source_id: str) -> int:
    return conn.execute("SELECT tier FROM sources WHERE id = ?", (source_id,)).fetchone()["tier"]


def normalize_envelope(conn: sqlite3.Connection, envelope_id: str) -> list[str]:
    """Process one envelope; returns fact ids written. Idempotent enough for
    retries: value facts skip unchanged values, report re-processing yields
    no new facts."""
    env = _envelope(conn, envelope_id)
    payload: dict[str, Any] = json.loads(env["payload"])
    tier = _source_tier(conn, env["source_id"])
    common = {
        "envelope_id": env["id"],
        "source_id": env["source_id"],
        "tier": tier,
        "raw_ref": env["raw_ref"],
    }
    observed = payload.get("modified_at") or payload.get("observed_at") or env["fetched_at"]

    if env["kind"] == "report":
        return _normalize_report(conn, payload, observed, common)
    if env["kind"] == "hotspot":
        return _normalize_hotspot(conn, payload, observed, common)
    if env["kind"] == "alert":
        return _normalize_alert(conn, payload, common)
    return []


def _normalize_report(conn, payload, observed, common) -> list[str]:
    name = (payload.get("name") or "UNNAMED").strip()
    # ref_type/ref_id are the official cross-ref; fall back to legacy irwin_id.
    ref_type = payload.get("ref_type") or ("wfigs" if payload.get("irwin_id") else None)
    ref_id = payload.get("ref_id") or payload.get("irwin_id")
    if not ref_id:
        return []
    ref_col = "calfire_id" if ref_type == "calfire" else "irwin_id"

    # WFIGS is RAW DISPATCH, not a curated list: every LAC-###### ticket,
    # prescribed burn and tenth-acre initial attack in the state is "current".
    # Gate incident CREATION; a fire already tracked (by irwin id or adoptable
    # by name) always gets its update, and a small fire that grows past the
    # gate creates itself on the next modified-record poll.
    if ref_col == "irwin_id":
        typ = (payload.get("type") or "").upper()
        if typ == "RX" or "RX" in name.upper().split():
            return []  # prescribed burns are not wildfires — standing doctrine
        known = conn.execute(
            "SELECT 1 FROM incidents WHERE irwin_id = ?", (ref_id,)
        ).fetchone()
        if not known and inc._adopt_cross_ref(
            conn, ref_col="irwin_id", name=name,
            lat=payload.get("lat"), lon=payload.get("lon"),
        ) is None:
            acres = payload.get("acreage") or 0
            crew = payload.get("personnel") or 0
            if acres < WFIGS_MIN_ACRES and crew < WFIGS_MIN_PERSONNEL:
                return []  # dispatch-ticket noise, not yet a fire worth a row

    # Status from the source: CAL FIRE reports is_active; WFIGS has none (assume
    # active). Historical imports pass is_active=false → contained.
    if "is_active" in payload:
        status = "active" if payload["is_active"] else "contained"
    else:
        status = "active"

    incident_id, created = inc.upsert_incident(
        conn,
        ref_col=ref_col,
        ref_id=ref_id,
        name=name,
        lat=payload.get("lat"),
        lon=payload.get("lon"),
        county=payload.get("county"),
        incident_type=payload.get("type"),
        status=status,
        via=ref_type or "wfigs",
    )
    facts: list[str] = []
    if created:
        fact_id = write_fact(
            conn,
            incident_id=incident_id,
            kind="start",
            content=f"{name} reported by official source"
            + (f" in {payload['county']} County" if payload.get("county") else ""),
            observed_at=payload.get("discovered_at") or observed,
            lat=payload.get("lat"),
            lon=payload.get("lon"),
            geo_method="source_fixed" if payload.get("lat") is not None else None,
            structured={"ref_type": ref_type, "ref_id": ref_id, "type": payload.get("type")},
            **common,
        )
        inc.set_first_detection(conn, incident_id, fact_id)
        facts.append(fact_id)

    acreage = payload.get("acreage")
    if acreage is not None:
        fid = write_value_fact(
            conn,
            incident_id=incident_id,
            kind="acreage",
            value_column="acreage",
            value=float(acreage),
            content=f"{name} mapped at {acreage:,.0f} acres",
            observed_at=observed,
            **common,
        )
        if fid:
            facts.append(fid)

    containment = payload.get("containment_pct")
    if containment is not None:
        fid = write_value_fact(
            conn,
            incident_id=incident_id,
            kind="containment",
            value_column="containment_pct",
            value=float(containment),
            content=f"{name} {containment:.0f}% contained",
            observed_at=observed,
            **common,
        )
        if fid:
            facts.append(fid)
    return facts


def _normalize_hotspot(conn, payload, observed, common) -> list[str]:
    lat, lon = payload.get("lat"), payload.get("lon")
    if lat is None or lon is None:
        return []
    incident_id = inc.nearest_active_incident(conn, lat, lon)
    frp = payload.get("frp")
    sat = payload.get("satellite") or "satellite"
    content = f"Hotspot detected by {sat} at {lat:.4f}, {lon:.4f}" + (
        f" (FRP {frp:.0f} MW)" if frp else ""
    )
    fid = write_fact(
        conn,
        incident_id=incident_id,
        kind="other" if incident_id else "start",
        content=content,
        observed_at=observed,
        confidence=0.9,
        lat=lat,
        lon=lon,
        geo_method="source_fixed",
        structured={"frp": frp, "confidence": payload.get("confidence")},
        **common,
    )
    return [fid]


def _normalize_alert(conn, payload, common) -> list[str]:
    observed = payload.get("sent") or payload.get("effective") or utcnow()
    event = payload.get("event") or "Alert"
    area = payload.get("area_desc") or ""
    headline = payload.get("headline") or event
    fid = write_fact(
        conn,
        incident_id=None,
        kind="weather",
        content=f"{event}: {headline}",
        observed_at=observed,
        confidence=0.99,
        structured={
            "event": event,
            "area_desc": area,
            "severity": payload.get("severity"),
            "onset": payload.get("onset"),
            "expires": payload.get("expires"),
        },
        **common,
    )
    return [fid]
