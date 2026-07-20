"""Origin verification — prove the point we zoom to is the official one.

Zooming to street level makes a coordinate look authoritative. That is exactly
when it must be checked, because a position can be:

- CAL FIRE's own reported point (what we want),
- stale, if CAL FIRE has since refined it,
- INFERRED from a fire's name via GNIS (approximate by construction), or
- outside the county the reporting claims.

So the origin is re-fetched from CAL FIRE live and cross-checked. The result is
shown to the operator before they act on it; nothing is silently corrected,
because the ledger is append-only and a coordinate is evidence, not a setting.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from toast.pipeline.incidents import haversine_km

# California envelope — the same backstop the extractor uses.
CA_BBOX = (32.3, 42.1, -124.6, -114.0)

# How far our stored point may sit from CAL FIRE's before it matters. CAL FIRE
# publishes to ~4 decimals; a few hundred metres is refinement, kilometres is a
# different place.
MATCH_TOLERANCE_KM = 1.0


def _check(name: str, ok: bool | None, detail: str) -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail}


def verify_origin(conn: sqlite3.Connection, incident_id: str) -> dict[str, Any]:
    """Cross-check an incident's coordinates against CAL FIRE and sanity rules."""
    inc = conn.execute(
        """SELECT id, canonical_name, lat, lon, county, calfire_id, irwin_id,
                  first_detected_fact_id
           FROM incidents WHERE id = ?""",
        (incident_id,),
    ).fetchone()
    if inc is None:
        raise ValueError("incident not found")

    checks: list[dict[str, Any]] = []
    official: dict[str, Any] | None = None

    if inc["lat"] is None or inc["lon"] is None:
        return {
            "incident_id": incident_id,
            "verified": False,
            "confidence": "none",
            "lat": None,
            "lon": None,
            "checks": [_check("coordinates", False, "this incident has no coordinates")],
        }

    lat, lon = float(inc["lat"]), float(inc["lon"])

    # 1. inside California
    lat_min, lat_max, lon_min, lon_max = CA_BBOX
    in_ca = lat_min <= lat <= lat_max and lon_min <= lon <= lon_max
    checks.append(
        _check(
            "in California",
            in_ca,
            "inside the state envelope"
            if in_ca
            else "OUTSIDE California — do not trust this point",
        )
    )

    # 2. did any fact actually report coordinates, or is this inferred?
    reported = conn.execute(
        """SELECT COUNT(*) AS n FROM facts
           WHERE incident_id = ? AND lat IS NOT NULL
             AND COALESCE(geo_method, '') NOT IN ('named_feature', 'geocoded_text')""",
        (incident_id,),
    ).fetchone()["n"]
    inferred_only = conn.execute(
        """SELECT COUNT(*) AS n FROM facts
           WHERE incident_id = ? AND geo_method IN ('named_feature', 'geocoded_text')""",
        (incident_id,),
    ).fetchone()["n"]
    if inc["calfire_id"] or inc["irwin_id"] or reported:
        checks.append(
            _check("reported position", True, "coordinates come from an official incident record")
        )
    else:
        checks.append(
            _check(
                "reported position",
                False,
                f"no source reported coordinates ({inferred_only} inferred from place names) — "
                "this position is approximate, not a surveyed origin",
            )
        )

    # 3. live re-check against CAL FIRE
    if inc["calfire_id"]:
        try:
            from toast.sources.calfire import fetch_incidents
            from toast.sources.capture import RawCapture

            capture = RawCapture()
            match = None
            for inactive in (False, True):  # active list first; closed as fallback
                for candidate in fetch_incidents(capture, inactive=inactive):
                    if str(candidate.get("UniqueId")) == str(inc["calfire_id"]):
                        match = candidate
                        break
                if match:
                    break
            if match and match.get("Latitude") and match.get("Longitude"):
                olat, olon = float(match["Latitude"]), float(match["Longitude"])
                delta = round(haversine_km(lat, lon, olat, olon), 3)
                official = {
                    "lat": olat,
                    "lon": olon,
                    "delta_km": delta,
                    "name": match.get("Name"),
                    "county": match.get("County"),
                    "url": match.get("Url"),
                    "updated": match.get("Updated"),
                }
                agrees = delta <= MATCH_TOLERANCE_KM
                checks.append(
                    _check(
                        "matches CAL FIRE",
                        agrees,
                        (
                            f"agrees with CAL FIRE to {delta} km"
                            if agrees
                            else f"differs from CAL FIRE by {delta} km — CAL FIRE has refined or "
                            "moved this point since we recorded it"
                        ),
                    )
                )
            else:
                checks.append(
                    _check(
                        "matches CAL FIRE",
                        None,
                        "CAL FIRE no longer lists this incident (likely closed) — cannot re-verify",
                    )
                )
        except Exception as exc:  # a CAL FIRE outage is not a bad coordinate
            checks.append(
                _check("matches CAL FIRE", None, f"could not reach CAL FIRE to re-check: {exc}")
            )
    else:
        checks.append(
            _check("matches CAL FIRE", None, "no CAL FIRE id — nothing official to compare against")
        )

    # 4. does the position land in the county the reporting names?
    county_claim = (inc["county"] or "").replace(" County", "").strip()
    if county_claim:
        near = conn.execute(
            """SELECT county, name,
                      ((lat - ?) * (lat - ?)) + ((lon - ?) * (lon - ?)) AS d2
               FROM gnis_features
               WHERE feature_class IN ('Populated Place', 'Locale', 'Summit', 'Valley')
                 AND lat BETWEEN ? - 0.5 AND ? + 0.5
                 AND lon BETWEEN ? - 0.5 AND ? + 0.5
               ORDER BY d2 LIMIT 1""",
            (lat, lat, lon, lon, lat, lat, lon, lon),
        ).fetchone()
        if near is None:
            checks.append(
                _check("county", None, "no gazetteer coverage nearby to cross-check the county")
            )
        else:
            same = (near["county"] or "").strip().lower() == county_claim.lower()
            checks.append(
                _check(
                    "county",
                    same,
                    (
                        f"lands in {near['county']} County, as reported"
                        if same
                        else f"reported as {county_claim} County but the point falls in "
                        f"{near['county']} County (nearest feature: {near['name']})"
                    ),
                )
            )

    failed = [c for c in checks if c["ok"] is False]
    unknown = [c for c in checks if c["ok"] is None]
    confidence = "high" if not failed and not unknown else "low" if failed else "medium"

    return {
        "incident_id": incident_id,
        "name": inc["canonical_name"],
        "lat": lat,
        "lon": lon,
        "verified": not failed,
        "confidence": confidence,
        "official": official,
        "checks": checks,
    }
