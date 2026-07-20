"""Deployed-resource enrichment from WFIGS/IRWIN.

CAL FIRE's incident API — our source of truth for which fires are burning —
carries no resource information at all. WFIGS does: total assigned personnel,
management complexity, cost to date, and suppression strategy.

Two honesty constraints shape this:

1. WFIGS publishes TOTAL PERSONNEL, not the engines/crews/dozers/aircraft
   breakdown. That breakdown comes from ICS-209 situation reports, which are
   only filed for larger incidents. We show the total we can prove and do not
   fabricate a grid to fill the shape of a mockup.
2. Our incidents come from CAL FIRE and carry no IRWIN id, so matching is by
   name AND proximity. A name alone is not enough — California has repeated
   fire names — so a candidate must also sit within MATCH_RADIUS_KM of where
   we already believe the fire is.

Results land in the ledger as ordinary T1 facts, so they supersede, carry
provenance, and appear in the incident panel like everything else.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from urllib.parse import urlencode

from toast.core.db import utcnow
from toast.core.envelope import Envelope, insert_envelope
from toast.core.fetch import fetch
from toast.core.ids import ulid
from toast.pipeline.attribution import _norm_name
from toast.pipeline.incidents import haversine_km
from toast.pipeline.ledger import write_fact

WFIGS_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Incident_Locations_Current/FeatureServer/0/query"
)
FIELDS = [
    "IrwinID",
    "IncidentName",
    "TotalIncidentPersonnel",
    "IncidentComplexityLevel",
    "FireMgmtComplexity",
    "EstimatedCostToDate",
    "IncidentSize",
    "PercentContained",
    "ModifiedOnDateTime_dt",
]
MATCH_RADIUS_KM = 50.0  # a same-named fire further away is a different fire
SOURCE_NAME = "WFIGS resource enrichment"


def _wfigs_source(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT id FROM sources WHERE adapter = 'wfigs_resources'").fetchone()
    if row:
        return str(row["id"])
    sid, now = ulid(), utcnow()
    conn.execute(
        """INSERT INTO sources (id, name, type, adapter, tier, config, schedule, enabled,
                                tos_notes, created_at, updated_at)
           VALUES (?, ?, 'api', 'wfigs_resources', 1, '{}', NULL, 1, ?, ?, ?)""",
        (
            sid,
            SOURCE_NAME,
            "Public interagency fire data (NIFC/WFIGS). On-demand per incident.",
            now,
            now,
        ),
    )
    return sid


def find_match(name: str, lat: float | None, lon: float | None) -> dict[str, Any] | None:
    """The WFIGS record for this fire, or None when we can't be sure."""
    # WFIGS names the fire without the word "Fire" ("BROOK", not "Brook Fire"),
    # so search on the distinctive part and compare normalized names after.
    target = _norm_name(name)
    if not target:
        return None
    safe = target.replace("'", "''")
    query = urlencode(
        {
            "where": f"UPPER(IncidentName) LIKE UPPER('%{safe}%')",
            "outFields": ",".join(FIELDS),
            "f": "json",
            "resultRecordCount": 25,
        }
    )
    body, _ctype = fetch(f"{WFIGS_URL}?{query}", accept_types=("application/json", "text/plain"))
    features = json.loads(body).get("features", [])

    best: tuple[float, dict[str, Any]] | None = None
    for feature in features:
        attrs = feature.get("attributes", {})
        if _norm_name(str(attrs.get("IncidentName") or "")) != target:
            continue  # substring hit but not the same fire name
        geom = feature.get("geometry") or {}
        if lat is not None and lon is not None and geom.get("y") is not None:
            distance = haversine_km(lat, lon, float(geom["y"]), float(geom["x"]))
            if distance > MATCH_RADIUS_KM:
                continue  # same name, wrong place
        else:
            distance = 999.0
        if best is None or distance < best[0]:
            best = (distance, {**attrs, "match_distance_km": round(distance, 1)})
    return best[1] if best else None


def enrich_incident(conn: sqlite3.Connection, incident_id: str) -> dict[str, Any]:
    """Fetch and record deployed-resource figures for one incident."""
    inc = conn.execute(
        "SELECT id, canonical_name, lat, lon FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if inc is None:
        raise ValueError("incident not found")

    match = find_match(inc["canonical_name"], inc["lat"], inc["lon"])
    if match is None:
        return {
            "matched": False,
            "reason": (
                "no WFIGS record matches this fire by name and location — "
                "CAL FIRE lists many fires that never get an interagency record"
            ),
        }

    personnel = match.get("TotalIncidentPersonnel")
    complexity = match.get("IncidentComplexityLevel") or match.get("FireMgmtComplexity")
    cost = match.get("EstimatedCostToDate")
    if personnel in (None, 0) and not complexity:
        return {
            "matched": True,
            "recorded": 0,
            "irwin_id": match.get("IrwinID"),
            "reason": "matched, but WFIGS reports no personnel or complexity for it yet",
        }

    source_id = _wfigs_source(conn)
    now = utcnow()
    env_id = insert_envelope(
        conn,
        Envelope(
            source_id=source_id,
            external_id=f"wfigs-res:{match.get('IrwinID')}:{match.get('ModifiedOnDateTime_dt')}",
            kind="report",
            payload={"ref_type": "wfigs_resources", **match},
            url=None,
        ),
    )
    if env_id is None:  # identical figures already recorded
        return {"matched": True, "recorded": 0, "reason": "no change since the last check"}

    written: list[dict[str, Any]] = []

    def record(content: str, structured: dict[str, Any]) -> None:
        prior = conn.execute(
            """SELECT id FROM facts
               WHERE incident_id = ? AND kind = 'resource' AND source_id = ?
                 AND json_extract(structured, '$.metric') = ?
                 AND id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
               ORDER BY observed_at DESC LIMIT 1""",
            (incident_id, source_id, structured["metric"]),
        ).fetchone()
        fact_id = write_fact(
            conn,
            incident_id=incident_id,
            envelope_id=env_id,
            source_id=source_id,
            tier=1,
            content=content,
            kind="resource",
            observed_at=now,
            confidence=0.95,
            structured=structured,
            supersedes=prior["id"] if prior else None,
        )
        written.append({"fact_id": fact_id, "metric": structured["metric"]})

    if personnel:
        record(
            f"{int(personnel):,} total personnel assigned to the {inc['canonical_name']}",
            {"metric": "personnel", "value": int(personnel), "irwin_id": match.get("IrwinID")},
        )
    if complexity:
        record(
            f"Incident management complexity: {complexity}",
            {"metric": "complexity", "value": str(complexity)},
        )
    if cost:
        record(
            f"Estimated cost to date: ${int(cost):,}",
            {"metric": "cost_to_date", "value": int(cost)},
        )

    return {
        "matched": True,
        "recorded": len(written),
        "facts": written,
        "irwin_id": match.get("IrwinID"),
        "match_distance_km": match.get("match_distance_km"),
        "personnel": personnel,
        "complexity": complexity,
        "cost_to_date": cost,
    }


def current_resources(conn: sqlite3.Connection, incident_id: str) -> dict[str, Any]:
    """Latest non-superseded resource metrics for the incident panel."""
    rows = conn.execute(
        """SELECT f.content, f.structured, f.observed_at, s.name AS source_name, f.tier
           FROM facts f JOIN sources s ON s.id = f.source_id
           WHERE f.incident_id = ? AND f.kind = 'resource'
             AND f.id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
           ORDER BY f.observed_at DESC""",
        (incident_id,),
    ).fetchall()
    metrics: dict[str, Any] = {}
    notes: list[dict[str, Any]] = []
    for r in rows:
        try:
            structured = json.loads(r["structured"] or "{}")
        except json.JSONDecodeError:
            structured = {}
        metric = structured.get("metric")
        if metric and metric not in metrics:
            metrics[metric] = {
                "value": structured.get("value"),
                "observed_at": r["observed_at"],
                "source": r["source_name"],
                "tier": r["tier"],
            }
        elif not metric:
            notes.append(
                {
                    "content": r["content"],
                    "observed_at": r["observed_at"],
                    "source": r["source_name"],
                    "tier": r["tier"],
                }
            )
    return {"metrics": metrics, "notes": notes[:6]}
