"""Read-only operator API routes (Phase 1).

All queries parameterized; responses are plain dicts serialized by FastAPI.
Stale detection: a fact is stale-flagged when ingested_at trails observed_at
by more than STALE_THRESHOLD_S (news reporting old information as new).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

STALE_THRESHOLD_S = 6 * 3600

router = APIRouter()


def _conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn


def _fact_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["structured"] = json.loads(d.get("structured") or "{}")
    if "stale_s" in d:
        d["stale"] = (d["stale_s"] or 0) > STALE_THRESHOLD_S
    return d


FACT_COLS = """f.id, f.incident_id, f.source_id, f.tier, f.content, f.kind,
    f.acreage, f.containment_pct, f.evac_status, f.structured, f.confidence,
    f.observed_at, f.ingested_at, f.lat, f.lon, f.geo_method, f.supersedes,
    f.starred, f.raw_ref,
    (unixepoch(f.ingested_at) - unixepoch(f.observed_at)) AS stale_s,
    s.name AS source_name,
    (SELECT id FROM facts sup WHERE sup.supersedes = f.id) AS superseded_by,
    (SELECT effective_confidence FROM fact_assessments a WHERE a.fact_id = f.id)
        AS effective_confidence,
    (SELECT corroborating_sources FROM fact_assessments a WHERE a.fact_id = f.id)
        AS corroborating_sources,
    (SELECT COALESCE(json_extract(e.payload, '$.url'), json_extract(e.payload, '$.link'))
       FROM envelopes e WHERE e.id = f.envelope_id) AS source_url,
    (SELECT e.kind FROM envelopes e WHERE e.id = f.envelope_id) AS media_kind"""


@router.get("/incidents")
def list_incidents(request: Request, status: str | None = None, include_rx: bool = False) -> dict:
    clauses, params = [], []
    if status:
        clauses.append("i.status = ?")
        params.append(status)
    if not include_rx:
        clauses.append("COALESCE(i.incident_type, 'WF') != 'RX'")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    # latest non-superseded value fact for a metric on an incident
    latest = """(SELECT {col} FROM facts v WHERE v.incident_id = i.id AND v.kind = '{kind}'
                 AND v.id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
                 ORDER BY v.observed_at DESC LIMIT 1)"""
    rows = (
        _conn(request)
        .execute(
            f"""SELECT i.*, COUNT(f.id) AS fact_count, MAX(f.observed_at) AS last_observed,
                   MIN(f.observed_at) AS started_at,
                   {latest.format(col="acreage", kind="acreage")} AS acreage,
                   {latest.format(col="containment_pct", kind="containment")} AS containment_pct,
                   -- active evacuation = latest non-superseded evac fact still
                   -- ordering/warning (not lifted). Drives the map's evac color.
                   {latest.format(col="evac_status", kind="evac")} AS evac_status
            FROM incidents i LEFT JOIN facts f ON f.incident_id = i.id
            {where}
            GROUP BY i.id ORDER BY last_observed DESC NULLS LAST""",
            params,
        )
        .fetchall()
    )
    return {"incidents": [dict(r) for r in rows]}


@router.get("/incidents/{incident_id}")
def get_incident(request: Request, incident_id: str) -> dict:
    row = _conn(request).execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "incident not found")
    aliases = [
        r["alias"]
        for r in _conn(request).execute(
            "SELECT alias FROM incident_aliases WHERE incident_id = ?", (incident_id,)
        )
    ]
    return {"incident": dict(row), "aliases": aliases}


@router.get("/incidents/{incident_id}/facts")
def incident_facts(
    request: Request,
    incident_id: str,
    limit: int = Query(200, le=1000),
) -> dict:
    rows = (
        _conn(request)
        .execute(
            f"""SELECT {FACT_COLS} FROM facts f JOIN sources s ON s.id = f.source_id
            WHERE f.incident_id = ?
            ORDER BY f.observed_at DESC LIMIT ?""",
            (incident_id, limit),
        )
        .fetchall()
    )
    return {"facts": [_fact_dict(r) for r in rows]}


@router.get("/intake")
def intake(
    request: Request,
    limit: int = Query(50, le=500),
    min_tier: int = Query(5, ge=1, le=5),
) -> dict:
    """Recent facts across all incidents — the OPS intake stream."""
    rows = (
        _conn(request)
        .execute(
            f"""SELECT {FACT_COLS}, i.canonical_name AS incident_name,
                i.county AS incident_county
            FROM facts f JOIN sources s ON s.id = f.source_id
            LEFT JOIN incidents i ON i.id = f.incident_id
            WHERE f.tier <= ?
            ORDER BY f.ingested_at DESC LIMIT ?""",
            (min_tier, limit),
        )
        .fetchall()
    )
    return {"facts": [_fact_dict(r) for r in rows]}


@router.post("/facts/{fact_id}/star")
def star_fact(request: Request, fact_id: str, body: dict) -> dict:
    """Pin a fact. `starred` is one of only two columns the append-only trigger
    lets the operator change — the claim itself stays exactly as observed."""
    starred = 1 if body.get("starred") else 0
    cur = _conn(request).execute(
        "UPDATE facts SET starred = ? WHERE id = ? AND starred <> ?",
        (starred, fact_id, starred),
    )
    if cur.rowcount == 0:
        exists = _conn(request).execute("SELECT 1 FROM facts WHERE id = ?", (fact_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "fact not found")
    return {"ok": True, "fact_id": fact_id, "starred": bool(starred)}


@router.get("/incidents/{incident_id}/intel")
def get_incident_intel(request: Request, incident_id: str) -> dict:
    """Aggregated intelligence for the OPS panel: header metrics with
    supersession deltas, source-confidence meter, situation bullets, and
    evac/road impacts (verbatim — the UI renders them untouched)."""
    from toast.pipeline.intel import incident_intel

    conn = _conn(request)
    if conn.execute("SELECT 1 FROM incidents WHERE id = ?", (incident_id,)).fetchone() is None:
        raise HTTPException(404, "incident not found")
    return incident_intel(conn, incident_id)


@router.post("/incidents/{incident_id}/resources/refresh")
def refresh_resources(request: Request, incident_id: str) -> dict:
    """Pull deployed-resource figures from WFIGS for this fire. Results land in
    the ledger as ordinary facts, so they supersede and carry provenance."""
    from toast.core.fetch import FetchError
    from toast.pipeline.resources import enrich_incident

    try:
        return enrich_incident(_conn(request), incident_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FetchError as exc:
        raise HTTPException(502, f"could not reach WFIGS: {exc}") from exc


@router.get("/incidents/{incident_id}/origin-check")
def check_origin(request: Request, incident_id: str) -> dict:
    """Re-verify an incident's coordinates against CAL FIRE before the operator
    treats them as a precise location. Never rewrites the ledger — a
    coordinate is evidence, and this reports on it."""
    from toast.pipeline.geoverify import verify_origin

    try:
        return verify_origin(_conn(request), incident_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/incidents/{incident_id}/weather")
def get_incident_weather(request: Request, incident_id: str, refresh: bool = False) -> dict:
    """Current conditions + active NWS alerts at the fire (free, no key).
    Cached ~20 minutes; `refresh=true` forces a fetch."""
    from toast.pipeline.weather import WeatherError, incident_weather

    try:
        return incident_weather(_conn(request), incident_id, refresh=refresh)
    except WeatherError as exc:
        # a weather outage must not look like a broken workstation
        return {"available": False, "reason": str(exc)}


@router.get("/incidents/{incident_id}/timeline")
def get_incident_timeline(request: Request, incident_id: str) -> dict:
    from toast.pipeline.intel import incident_timeline

    return {"timeline": incident_timeline(_conn(request), incident_id)}


@router.post("/incidents/{incident_id}/follow")
def follow_incident(request: Request, incident_id: str, body: dict) -> dict:
    """Toggle the operator's ★ FOLLOW flag on an incident."""
    followed = 1 if body.get("followed") else 0
    cur = _conn(request).execute(
        "UPDATE incidents SET followed = ? WHERE id = ?", (followed, incident_id)
    )
    if cur.rowcount == 0:
        raise HTTPException(404, "incident not found")
    return {"ok": True, "followed": bool(followed)}


# ---------------- backups ----------------


@router.get("/backups")
def get_backups(request: Request) -> dict:
    """Backup posture. `at_risk` is true whenever no offsite copy is
    configured — local-only copies die with the machine."""
    from toast.backup import backup_status, list_snapshots

    return {**backup_status(conn=_conn(request)), "all": list_snapshots()[:10]}


@router.post("/backups/raw-sync")
def run_raw_sync(request: Request) -> dict:
    """Push a batch of the raw archive offsite now. The ledger snapshot covers
    every claim; this covers the evidence behind them."""
    from toast.core.credentials import settings_with_credentials
    from toast.rawsync import sync_raw

    conn = _conn(request)
    return sync_raw(conn, settings_with_credentials(conn))


@router.post("/backups/run")
def run_backup_now(request: Request) -> dict:
    """Operator-triggered verified snapshot (same path as the nightly job)."""
    from toast.backup import run_backup

    try:
        return run_backup(_conn(request))
    except Exception as exc:  # surfaced to the operator, never silent
        raise HTTPException(500, f"backup failed: {exc}") from exc


@router.get("/drafts/{draft_id}/export")
def export_draft(request: Request, draft_id: str, fmt: str = "markdown") -> dict:
    """Draft text for manual posting to the operator's own website.

    Publishing is a human act here: nothing is pushed anywhere automatically,
    so this hands back exactly what to paste, with the source attribution
    intact."""
    conn = _conn(request)
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    if fmt not in ("markdown", "html", "text"):
        raise HTTPException(400, "fmt must be markdown, html or text")

    body = row["body_md"] or ""
    title = row["title"] or "Untitled"
    if fmt == "html":
        import html as _html

        import markdown as _md

        rendered = _md.markdown(body, extensions=["extra"])
        content = f"<article>\n<h1>{_html.escape(title)}</h1>\n{rendered}\n</article>"
    elif fmt == "text":
        content = f"{title}\n\n{body}"
    else:
        content = f"# {title}\n\n{body}"
    return {
        "draft_id": draft_id,
        "title": title,
        "format": fmt,
        "content": content,
        "status": row["status"],
    }


@router.get("/jobs/dead")
def list_dead_jobs(request: Request, limit: int = Query(50, le=200)) -> dict:
    """The dead-letter queue. It was always preserved 'for inspection'; this is
    the inspection. A transient LLM outage silently cost 20 articles' claims."""
    from toast.queue.jobs import dead_summary

    return dead_summary(_conn(request), limit=limit)


@router.post("/jobs/requeue")
def requeue_dead_jobs(request: Request, body: dict) -> dict:
    """Retry dead jobs. Publishing is never included — a dead publish job may
    already have posted, and re-running it is the duplicate."""
    from toast.queue.jobs import NEVER_AUTO_REQUEUE, requeue_dead

    stage = body.get("stage")
    if stage in NEVER_AUTO_REQUEUE:
        raise HTTPException(
            400, f"{stage} jobs are never bulk-retried: re-send deliberately from NEWSROOM"
        )
    n = requeue_dead(_conn(request), stage=stage, job_ids=body.get("job_ids"))
    return {"ok": True, "requeued": n}


@router.get("/air/focus/{incident_id}")
def air_focus(request: Request, incident_id: str) -> dict:
    """AIR mode: the selected fire's airspace, throttled upstream, read-through
    otherwise — a fast UI poll degrades to store reads, never to API abuse."""
    from toast.pipeline.air import focus_poll

    try:
        return focus_poll(_conn(request), incident_id)
    except ValueError:
        raise HTTPException(404, "incident not found") from None


@router.get("/air/bases")
def air_bases(request: Request) -> dict:
    """Tanker-base watch: fire/military hulls at or recently departed from
    CAL FIRE and USFS bases, one base scanned per window, round-robin."""
    from toast.pipeline.air import base_watch

    return base_watch(_conn(request))


@router.get("/air/mil")
def air_mil(request: Request) -> dict:
    """Military aircraft over California from the dedicated /mil feed."""
    from toast.pipeline.air import mil_snapshot

    return mil_snapshot(_conn(request))


@router.get("/air/log/{incident_id}")
def air_log(request: Request, incident_id: str) -> dict:
    """Per-fire aviation timeline derived from our stored positions."""
    from toast.pipeline.air import activity_log

    return activity_log(_conn(request), incident_id)


@router.get("/incidents/{incident_id}/history")
def incident_history(request: Request, incident_id: str, shapes: bool = False) -> dict:
    """How the fire has moved: every reported acreage and containment value in
    time order (supersession IS the history), plus archived perimeter
    snapshots. "How fast is it growing?" is a reporter's first question, and
    the ledger has always held the answer — this makes it visible."""
    conn = _conn(request)
    if conn.execute("SELECT 1 FROM incidents WHERE id = ?", (incident_id,)).fetchone() is None:
        raise HTTPException(404, "incident not found")

    def series(kind: str, col: str) -> list[dict]:
        rows = conn.execute(
            f"""SELECT {col} AS value, observed_at, tier FROM facts
                WHERE incident_id = ? AND kind = ? AND {col} IS NOT NULL
                ORDER BY observed_at""",
            (incident_id, kind),
        ).fetchall()
        out: list[dict] = []
        for r in rows:  # collapse repeats — flat stretches carry no new signal
            if out and out[-1]["value"] == r["value"]:
                continue
            out.append({"value": r["value"], "observed_at": r["observed_at"], "tier": r["tier"]})
        return out

    perims = conn.execute(
        """SELECT mapped_at, acres, captured_at{geom} FROM perimeter_history
           WHERE incident_id = ? ORDER BY mapped_at""".format(
            geom=", geometry" if shapes else ""
        ),
        (incident_id,),
    ).fetchall()
    perimeters = [dict(r) for r in perims]
    if shapes:
        for pr in perimeters:
            pr["geometry"] = json.loads(pr["geometry"])
    return {
        "acreage": series("acreage", "acreage"),
        "containment": series("containment", "containment_pct"),
        "perimeters": perimeters,
        "perimeter_count": len(perimeters),
    }


@router.get("/incidents/{incident_id}/aqi")
def incident_aqi(request: Request, incident_id: str, refresh: bool = False) -> dict:
    """Smoke/AQI near this fire from AirNow — the nearest reporting site, named,
    because the monitor can be a town over from the flames."""
    from toast.core.fetch import FetchError
    from toast.pipeline.airnow import aqi_for

    try:
        return aqi_for(_conn(request), incident_id, refresh=refresh)
    except ValueError:
        raise HTTPException(404, "incident not found") from None
    except FetchError as exc:
        raise HTTPException(502, f"AirNow unreachable: {exc}") from exc


@router.get("/mesh/nodes")
def mesh_nodes(request: Request, hours: int = Query(24, le=168)) -> dict:
    """NorCal Meshtastic nodes heard recently, freshest first."""
    rows = _conn(request).execute(
        """SELECT * FROM mesh_nodes
           WHERE unixepoch('now') - unixepoch(last_heard) < ? * 3600
           ORDER BY last_heard DESC LIMIT 300""",
        (hours,),
    ).fetchall()
    return {
        "nodes": [dict(r) for r in rows],
        "count": len(rows),
        "note": "heard via the public Meshtastic MQTT broker — the mesh itself "
        "needs no internet; this VIEW of it does",
    }


@router.get("/mesh/messages")
def mesh_messages(request: Request, limit: int = Query(100, le=500)) -> dict:
    """Recent mesh text traffic (48 h retained). Community text: shown inert,
    never rendered as markup, never treated as fact."""
    rows = _conn(request).execute(
        """SELECT m.*, n.long_name, n.short_name FROM mesh_messages m
           LEFT JOIN mesh_nodes n ON n.node_id = m.node_id
           ORDER BY m.rx_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return {"messages": [dict(r) for r in rows], "count": len(rows)}


@router.get("/radio/calls")
def radio_calls(request: Request, limit: int = Query(100, le=500)) -> dict:
    """Captured scanner traffic, newest first, transcript state shown honestly."""
    rows = _conn(request).execute(
        "SELECT * FROM radio_calls ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    pend = _conn(request).execute(
        "SELECT COUNT(*) AS n FROM radio_calls WHERE transcript_state = 'pending'"
    ).fetchone()["n"]
    return {"calls": [dict(r) for r in rows], "count": len(rows), "pending_transcripts": pend}


@router.get("/radio/calls/{call_id}/audio")
def radio_audio(request: Request, call_id: str):
    """The recording itself, from the CAS — the evidence behind the row."""
    from fastapi import Response

    conn = _conn(request)
    call = conn.execute("SELECT * FROM radio_calls WHERE id = ?", (call_id,)).fetchone()
    if call is None:
        raise HTTPException(404, "call not found")
    blob = conn.execute(
        "SELECT path FROM raw_blobs WHERE sha256 = ?", (call["audio_sha"],)
    ).fetchone()
    if blob is None:
        raise HTTPException(404, "audio missing from store")
    from pathlib import Path as _Path

    from toast.core.config import Settings as _Settings

    data = (_Path(_Settings().toast_data_dir) / blob["path"]).read_bytes()
    return Response(content=data, media_type=call["audio_type"])


@router.post("/radio/ingest")
def radio_ingest_now(request: Request) -> dict:
    """Manual inbox sweep — the scheduler runs the same thing every 30 s."""
    from toast.pipeline.radio import ingest_inbox

    return ingest_inbox(_conn(request))


@router.get("/evac-zones")
def evac_zones(request: Request, refresh: bool = False) -> dict:
    """Elevated evacuation zones statewide from Cal OES — statuses VERBATIM.
    Every polygon in this layer is an active instruction to someone."""
    from toast.core.fetch import FetchError
    from toast.pipeline.evaczones import get_evac_zones

    try:
        return get_evac_zones(_conn(request), refresh=refresh)
    except FetchError as exc:
        raise HTTPException(502, f"Cal OES layer unreachable: {exc}") from exc


@router.get("/fire-weather")
def fire_weather(request: Request, refresh: bool = False) -> dict:
    """Red flag / fire-weather alert polygons — a FORECAST layer, always
    labelled as such, with each zone's expiry."""
    from toast.core.fetch import FetchError
    from toast.pipeline.firewx import get_fire_weather

    try:
        return get_fire_weather(_conn(request), refresh=refresh)
    except FetchError as exc:
        raise HTTPException(502, f"NWS unreachable: {exc}") from exc


@router.get("/closures")
def closures(request: Request, refresh: bool = False) -> dict:
    """Caltrans full closures near active fires. State highways only — the
    payload note says so, and the UI must keep saying so."""
    from toast.pipeline.closures import get_closures

    return get_closures(_conn(request), refresh=refresh)


@router.get("/cameras")
def get_cameras(request: Request, lat: float, lon: float, refresh: bool = False) -> dict:
    """ALERTCalifornia cameras near a point — metadata only, so the operator can
    see which lenses might see the fire. Frames load in the BROWSER, straight
    from ALERTCalifornia; this backend never touches imagery (see cameras.py)."""
    from toast.pipeline.cameras import cameras_near

    return cameras_near(_conn(request), lat, lon, refresh=refresh)


@router.get("/wind")
def get_wind(request: Request) -> dict:
    """Current surface wind at each active fire, from cached NWS observations —
    for the map's wind layer. Wind drives fire spread, so this is the single
    most operationally loaded overlay on the board."""
    rows = _conn(request).execute(
        """SELECT i.id, i.canonical_name, i.lat, i.lon, w.payload, w.fetched_at
           FROM incidents i JOIN incident_weather w ON w.incident_id = i.id
           WHERE i.status = 'active' AND i.lat IS NOT NULL"""
    ).fetchall()
    winds = []
    for r in rows:
        p = json.loads(r["payload"])
        if p.get("wind_degrees") is None:
            continue
        winds.append({
            "incident_id": r["id"],
            "name": r["canonical_name"],
            "lat": r["lat"],
            "lon": r["lon"],
            "wind_degrees": p.get("wind_degrees"),
            "wind_compass": p.get("wind_compass"),
            "wind_mph": p.get("wind_mph"),
            "gust_mph": p.get("gust_mph"),
            "station_km": (p.get("station") or {}).get("distance_km"),
            "forecast": p.get("forecast"),
            "fetched_at": r["fetched_at"],
        })
    return {
        "winds": winds,
        "count": len(winds),
        "note": "wind_degrees is the direction the wind blows FROM (NWS convention).",
    }


@router.get("/aircraft/{hex_id}/photo")
def aircraft_photo(request: Request, hex_id: str) -> dict:
    """A photo of one aircraft by its ICAO hex — its assigned ID. Found on
    planespotters.net (credit attached), downloaded and saved to this hull's ID
    so it is ours; cached per hex."""
    from toast.pipeline.aircraft_photo import get_photo

    return get_photo(_conn(request), hex_id)


@router.get("/aircraft/{hex_id}/image")
def aircraft_image(request: Request, hex_id: str):
    """The saved photo bytes for one aircraft, served from our own store rather
    than hotlinked — the picture belongs to the hull's ID now."""
    from fastapi import Response

    from toast.pipeline.aircraft_photo import get_photo, image_bytes

    conn = _conn(request)
    result = image_bytes(conn, hex_id)
    if result is None:
        # not fetched yet — do the lookup+save, then try once more
        get_photo(conn, hex_id)
        result = image_bytes(conn, hex_id)
    if result is None:
        raise HTTPException(404, "no image for this aircraft")
    body, media_type = result
    return Response(
        content=body,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/aircraft")
def get_aircraft(request: Request) -> dict:
    """Aircraft currently near active fires, each with a short orbit trail.
    A live layer — positions in the sky, not ledger facts."""
    from toast.pipeline.aircraft import current_aircraft

    try:
        return current_aircraft(_conn(request))
    except Exception as exc:  # a feed outage must not break the map
        return {"aircraft": [], "count": 0, "fire_ops": 0, "error": str(exc)[:200]}


@router.get("/hotspots")
def get_hotspots(request: Request, hours: int = Query(24, ge=1, le=168)) -> dict:
    """Recent satellite fire detections (NASA FIRMS) as map points.

    A VIIRS pixel is a ~375 m patch that was hot when a satellite passed over —
    not a confirmed fire, and hours stale by the next overpass. So each point
    carries its detection time and FRP (fire radiative power, MW = how much heat
    it radiated), and the layer's own age is reported, because a bright dot from
    this morning's pass is not a live flame front.
    """
    rows = _conn(request).execute(
        """SELECT f.lat, f.lon, f.observed_at, f.structured, f.incident_id
           FROM facts f JOIN sources s ON s.id = f.source_id
           WHERE s.type = 'satellite' AND f.lat IS NOT NULL
             AND unixepoch('now') - unixepoch(f.observed_at) < ? * 3600
           ORDER BY f.observed_at DESC""",
        (hours,),
    ).fetchall()
    points = []
    latest = None
    for r in rows:
        s = json.loads(r["structured"] or "{}")
        points.append(
            {
                "lat": r["lat"],
                "lon": r["lon"],
                "observed_at": r["observed_at"],
                "frp": s.get("frp"),
                "confidence": s.get("confidence"),
                "attached": r["incident_id"] is not None,
            }
        )
        if latest is None or r["observed_at"] > latest:
            latest = r["observed_at"]
    return {
        "hotspots": points,
        "count": len(points),
        "latest_pass": latest,
        "window_hours": hours,
        "note": (
            "NASA FIRMS VIIRS 375 m — a hot pixel at satellite overpass, "
            "not a confirmed live fire."
        ),
    }


@router.get("/perimeters")
def get_map_perimeters(request: Request, refresh: bool = False) -> dict:
    """Current California fire perimeters (WFIGS/NIFC, free) as GeoJSON,
    tagged with our incident ids where they match. Cached ~30 minutes."""
    from toast.pipeline.perimeters import get_perimeters

    try:
        return get_perimeters(_conn(request), refresh=refresh)
    except Exception as exc:
        # a perimeter outage must not break the map
        return {"type": "FeatureCollection", "features": [], "error": str(exc)[:200]}


@router.post("/site/build")
def build_site_now(request: Request) -> dict:
    """Build the LOCAL static site on demand (preview at /site).

    Never triggered by publishing — the operator runs it when they want to see
    or hand-copy the rendered output."""
    from toast.queue import jobs

    return {"ok": True, "job_id": jobs.enqueue(_conn(request), "build_site", {}, priority=4)}


@router.get("/analytics")
def get_analytics(request: Request, days: int = Query(14, ge=1, le=365)) -> dict:
    """Ledger self-report: volume, quality, coverage, source performance."""
    from toast.pipeline.analytics import overview

    return overview(_conn(request), days=days)


# ---------------- prompts ----------------


@router.get("/prompts")
def list_prompts(request: Request) -> dict:
    """Operator-editable prompts, with the shipped default alongside."""
    from toast.llm.prompts import list_editable

    return {"prompts": list_editable(_conn(request))}


@router.put("/prompts/{prompt_id}")
def put_prompt(request: Request, prompt_id: str, body: dict) -> dict:
    """Save an edited prompt. Rejected if a required placeholder is missing —
    the prompt would still run, but silently without that data."""
    from toast.llm.prompts import list_editable, save_override

    conn = _conn(request)
    try:
        save_override(conn, prompt_id, str(body.get("template") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "prompts": list_editable(conn)}


@router.delete("/prompts/{prompt_id}")
def reset_prompt(request: Request, prompt_id: str) -> dict:
    """Revert to the shipped prompt."""
    from toast.llm.prompts import clear_override, list_editable

    conn = _conn(request)
    clear_override(conn, prompt_id)
    return {"ok": True, "prompts": list_editable(conn)}


# ---------------- GNIS gazetteer ----------------


@router.get("/gnis")
def gnis_status(request: Request) -> dict:
    """Gazetteer status — how many place names are loaded."""
    from toast.pipeline.geocode import gnis_count

    return {"features": gnis_count(_conn(request))}


@router.post("/gnis/load")
def gnis_load(request: Request) -> dict:
    """Download and load the California place-name gazetteer (public domain,
    no key). Runs as a job — it is a few seconds of work, not a request."""
    from toast.queue import jobs

    return {"ok": True, "job_id": jobs.enqueue(_conn(request), "gnis_load", {}, priority=8)}


@router.get("/gnis/resolve")
def gnis_resolve(request: Request, q: str, county: str | None = None) -> dict:
    """Look up a place or fire name — useful for checking what the geocoder
    will do before trusting it."""
    from dataclasses import asdict

    from toast.pipeline.geocode import resolve_fire_name, resolve_place

    conn = _conn(request)
    place = resolve_place(conn, q, county=county)
    fire = resolve_fire_name(conn, q, county=county)
    return {
        "query": q,
        "county": county,
        "place": asdict(place) if place else None,
        "as_fire_name": asdict(fire) if fire else None,
    }


# ---------------- API credentials ----------------


@router.get("/credentials")
def list_credentials(request: Request) -> dict:
    """Service registry + configuration/connection status.

    Deliberately returns NO secret values — only whether each field is set, a
    masked hint, and the last test result. There is no read-back endpoint."""
    from toast.core.credentials import service_status

    return {"services": service_status(_conn(request))}


@router.put("/credentials")
def put_credentials(request: Request, body: dict) -> dict:
    """Store credentials. An empty value clears it (falling back to .env)."""
    from toast.core.credentials import service_status, set_credential

    updates = body.get("credentials")
    if not isinstance(updates, dict):
        raise HTTPException(400, "credentials object required")
    conn = _conn(request)
    try:
        for name, value in updates.items():
            set_credential(conn, str(name), "" if value is None else str(value))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "services": service_status(conn)}


@router.post("/credentials/{service}/test")
def test_credentials(request: Request, service: str) -> dict:
    """Make the cheapest real call the provider offers and report the verdict.
    Read-only: nothing is posted, uploaded or published while testing."""
    from toast.core.credcheck import run_check

    try:
        return run_check(_conn(request), service)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------- operator settings ----------------


@router.get("/settings")
def get_settings(request: Request) -> dict:
    """Effective runtime settings + the models the LLM endpoint actually has."""
    from toast.core.appsettings import effective_settings
    from toast.llm.client import LLMClient

    return {
        "settings": effective_settings(_conn(request)),
        "models": LLMClient().list_models(),
    }


@router.put("/settings")
def put_settings(request: Request, body: dict) -> dict:
    """Update runtime settings. Only allowlisted keys; empty value reverts to
    the .env default."""
    from toast.core.appsettings import effective_settings, set_setting

    conn = _conn(request)
    updates = body.get("settings")
    if not isinstance(updates, dict):
        raise HTTPException(400, "settings object required")
    try:
        for key, value in updates.items():
            set_setting(conn, key, "" if value is None else str(value))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "settings": effective_settings(conn)}


# ---------------- analyst notes & tasks ----------------


@router.get("/notes")
def list_notes(request: Request, incident_id: str | None = None) -> dict:
    """Operator notes: incident-scoped plus global ones (incident_id NULL)."""
    conn = _conn(request)
    if incident_id:
        rows = conn.execute(
            """SELECT * FROM operator_notes
               WHERE incident_id = ? OR incident_id IS NULL
               ORDER BY done, created_at DESC""",
            (incident_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM operator_notes ORDER BY done, created_at DESC"
        ).fetchall()
    return {"notes": [dict(r) for r in rows]}


@router.post("/notes")
def create_note(request: Request, body: dict) -> dict:
    from toast.core.db import utcnow
    from toast.core.ids import ulid

    text = (body.get("body") or "").strip()
    if not text:
        raise HTTPException(400, "body required")
    note_id = ulid()
    _conn(request).execute(
        "INSERT INTO operator_notes (id, incident_id, body, created_at) VALUES (?, ?, ?, ?)",
        (note_id, body.get("incident_id"), text[:2000], utcnow()),
    )
    return {"ok": True, "note_id": note_id}


@router.post("/notes/{note_id}/toggle")
def toggle_note(request: Request, note_id: str) -> dict:
    cur = _conn(request).execute(
        "UPDATE operator_notes SET done = 1 - done WHERE id = ?", (note_id,)
    )
    if cur.rowcount == 0:
        raise HTTPException(404, "note not found")
    return {"ok": True}


@router.delete("/notes/{note_id}")
def delete_note(request: Request, note_id: str) -> dict:
    cur = _conn(request).execute("DELETE FROM operator_notes WHERE id = ?", (note_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "note not found")
    return {"ok": True}


@router.get("/adjudication")
def adjudication_queue(request: Request, limit: int = Query(50, le=200)) -> dict:
    """Unattributed non-official facts awaiting operator attribution, with
    scored incident candidates for one-click resolve."""
    from toast.pipeline.attribution import REVIEW_THRESHOLD, score_candidates

    conn = _conn(request)
    rows = conn.execute(
        f"""SELECT {FACT_COLS} FROM facts f JOIN sources s ON s.id = f.source_id
            WHERE f.incident_id IS NULL AND f.kind NOT IN ('weather')
              -- Satellite pixels are not adjudication work. A VIIRS hotspot that
              -- did not geo-attach carries no name, no text and no context: there
              -- is nothing for a human to match it against, and one FIRMS poll
              -- adds hundreds. They are new-start SIGNALS, which is what the
              -- WATCH board clusters — it reads unattributed facts too, so they
              -- are still seen, just not as a queue of 126 identical pixels.
              AND s.type != 'satellite'
              AND f.id NOT IN (SELECT fact_id FROM fact_triage)
              -- A dismissal applies to the CLAIM, not the row id. Re-polling a
              -- changed page re-extracts the same sentence as a fresh fact, and
              -- without this check the operator dismisses the same "new fire in
              -- LA County" every cycle forever (observed: one claim triaged 7
              -- times). Same source, same kind, same words = already answered.
              AND NOT EXISTS (
                SELECT 1 FROM facts f2 JOIN fact_triage t ON t.fact_id = f2.id
                WHERE f2.source_id = f.source_id AND f2.kind = f.kind
                  AND f2.content = f.content
              )
            ORDER BY f.ingested_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    items = []
    for row in rows:
        fact = _fact_dict(row)
        structured = fact["structured"]
        claimed_name = structured.get("incident_name")
        candidates = score_candidates(
            conn,
            observed_at=row["observed_at"],
            incident_name=claimed_name,
            lat=row["lat"],
            lon=row["lon"],
        )
        # A candidate carried only by the time signal is noise, not a match:
        # every open incident is "recent", so it would rank identically for
        # every fact. Requiring name or geo evidence keeps the queue honest.
        scored: list[dict[str, Any]] = [
            {
                "incident_id": c.incident_id,
                "incident_name": c.incident_name,
                "score": round(c.score, 3),
                "name_score": round(c.name_score, 2),
                "geo_score": round(c.geo_score, 2),
                "time_score": round(c.time_score, 2),
            }
            for c in candidates
            if c.score >= REVIEW_THRESHOLD * 0.5 and (c.name_score > 0.15 or c.geo_score > 0)
        ]
        proposal = conn.execute(
            "SELECT * FROM triage_proposals WHERE fact_id = ?", (row["id"],)
        ).fetchone()
        items.append(
            {
                "fact": fact,
                "candidates": scored,
                "proposal": dict(proposal) if proposal else None,
                "claimed_name": claimed_name,
                # names a fire we don't track — belongs on the WATCH board as a
                # possible new start, not attributed to an existing incident
                "unknown_fire": bool(claimed_name)
                and not any(float(c["name_score"]) >= 0.6 for c in scored),
            }
        )
    return {"queue": items}


@router.post("/adjudication/autotriage")
def autotriage_now(request: Request) -> dict:
    """Queue an AI triage pass over unproposed adjudication items. Proposals
    are advice; nothing is applied until the operator accepts."""
    from toast.queue import jobs

    return {"ok": True, "job_id": jobs.enqueue(_conn(request), "autotriage", {}, priority=5)}


@router.post("/adjudication/reattribute")
def reattribute_now(request: Request) -> dict:
    """Retry attribution on orphaned facts against the current incident list.
    Runs automatically every 30 minutes; this is the manual nudge."""
    from toast.queue import jobs

    return {"ok": True, "job_id": jobs.enqueue(_conn(request), "reattribute", {}, priority=6)}


@router.post("/adjudication/{fact_id}/triage")
def triage_fact(request: Request, fact_id: str, body: dict) -> dict:
    """Dispose of a fact that belongs to no tracked incident.

    The fact is never modified — triage is recorded in a derived table, so the
    claim stays exactly as observed while leaving the operator's queue."""
    from toast.core.db import utcnow

    state = (body.get("state") or "").strip()
    if state not in ("not_incident", "watch", "deferred"):
        raise HTTPException(400, "state must be not_incident, watch or deferred")
    conn = _conn(request)
    row = conn.execute("SELECT incident_id FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "fact not found")
    if row["incident_id"] is not None:
        raise HTTPException(409, "fact is already attributed")
    conn.execute(
        """INSERT INTO fact_triage (fact_id, state, note, at) VALUES (?, ?, ?, ?)
           ON CONFLICT(fact_id) DO UPDATE SET state = excluded.state,
                                              note = excluded.note, at = excluded.at""",
        (fact_id, state, (body.get("note") or "")[:500] or None, utcnow()),
    )
    return {"ok": True, "fact_id": fact_id, "state": state}


@router.post("/adjudication/triage")
def triage_bulk(request: Request, body: dict) -> dict:
    """Triage many facts at once — clearing a backlog one row at a time is how
    backlogs stay."""
    from toast.core.db import utcnow

    state = (body.get("state") or "").strip()
    fact_ids = body.get("fact_ids") or []
    if state not in ("not_incident", "watch", "deferred"):
        raise HTTPException(400, "state must be not_incident, watch or deferred")
    if not isinstance(fact_ids, list) or not fact_ids:
        raise HTTPException(400, "fact_ids required")
    conn = _conn(request)
    now = utcnow()
    done = 0
    for fact_id in fact_ids[:500]:
        row = conn.execute("SELECT incident_id FROM facts WHERE id = ?", (str(fact_id),)).fetchone()
        if row is None or row["incident_id"] is not None:
            continue
        conn.execute(
            """INSERT INTO fact_triage (fact_id, state, at) VALUES (?, ?, ?)
               ON CONFLICT(fact_id) DO UPDATE SET state = excluded.state, at = excluded.at""",
            (str(fact_id), state, now),
        )
        done += 1
    return {"ok": True, "triaged": done, "state": state}


@router.post("/adjudication/{fact_id}/untriage")
def untriage_fact(request: Request, fact_id: str) -> dict:
    """Put a triaged fact back in the queue (nothing is ever destroyed)."""
    _conn(request).execute("DELETE FROM fact_triage WHERE fact_id = ?", (fact_id,))
    return {"ok": True}


@router.get("/adjudication/deferred")
def deferred_count(request: Request) -> dict:
    """How many facts the operator has set aside. 'Deferred' means 'later', so
    there must always be a way back to them — this is what the restore control
    counts against."""
    n = _conn(request).execute(
        "SELECT COUNT(*) AS n FROM fact_triage WHERE state = 'deferred'"
    ).fetchone()["n"]
    return {"deferred": n}


@router.post("/adjudication/restore-deferred")
def restore_deferred(request: Request) -> dict:
    """Bring every deferred fact back into the queue. The counterpart to DEFER
    ALL — without it, 'set aside, recoverable' would be a promise the UI cannot
    keep."""
    cur = _conn(request).execute("DELETE FROM fact_triage WHERE state = 'deferred'")
    return {"ok": True, "restored": cur.rowcount}


@router.post("/adjudication/{fact_id}")
def resolve_adjudication(request: Request, fact_id: str, body: dict) -> dict:
    """Operator resolves attribution: {"incident_id": "..."} attaches the fact
    (the one UPDATE the append-only trigger permits: NULL → value)."""
    conn = _conn(request)
    incident_id = body.get("incident_id")
    if not incident_id:
        raise HTTPException(400, "incident_id required")
    fact = conn.execute("SELECT incident_id FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if fact is None:
        raise HTTPException(404, "fact not found")
    if fact["incident_id"] is not None:
        raise HTTPException(409, "fact already attributed")
    target = conn.execute("SELECT status FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if target is None:
        raise HTTPException(404, "incident not found")
    # the DB refuses this too (facts_frozen_update_guard); saying it here turns
    # an opaque 500 into an answer the operator can act on
    if target["status"] == "frozen":
        raise HTTPException(409, "incident is frozen; corrections require a superseding fact")
    conn.execute("UPDATE facts SET incident_id = ? WHERE id = ?", (incident_id, fact_id))
    return {"ok": True, "fact_id": fact_id, "incident_id": incident_id}


# ---------------- watch board: new starts ----------------


@router.get("/watch")
def watch_board(request: Request) -> dict:
    """Candidate new-start fires — unattributed named-fire signals not in the
    active-incident list, clustered and scored by corroboration."""
    from dataclasses import asdict

    from toast.pipeline.watch import build_watchlist

    return {"candidates": [asdict(c) for c in build_watchlist(_conn(request))]}


@router.post("/watch/triage")
def watch_triage(request: Request, body: dict) -> dict:
    """Dispose of a watch candidate: ignored (not a real start), archived (a
    real fire, but old), or keep. Underlying facts are never modified."""
    from toast.pipeline.watch_triage import set_state

    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key required")
    try:
        return set_state(
            _conn(request), key, (body.get("state") or "").strip(), note=body.get("note")
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/watch/untriage")
def watch_untriage(request: Request, body: dict) -> dict:
    """Put a filed candidate back on the board."""
    from toast.pipeline.watch_triage import clear_state

    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key required")
    clear_state(_conn(request), key)
    return {"ok": True, "key": key}


@router.get("/watch/archived")
def watch_archived(request: Request) -> dict:
    from toast.pipeline.watch_triage import archived_candidates

    return {"archived": archived_candidates(_conn(request))}


@router.post("/watch/classify")
def watch_classify(request: Request, body: dict) -> dict:
    """Age check: ask the local model whether this candidate is burning now or
    is an old fire being referenced. Suggests only — the operator decides."""
    from toast.llm.client import LLMError
    from toast.pipeline.watch_triage import classify_candidate

    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key required")
    try:
        return classify_candidate(_conn(request), key)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, f"age check failed: {exc}") from exc


@router.post("/watch/promote")
def promote_candidate(request: Request, body: dict) -> dict:
    """Promote a watch candidate to a real (candidate-status) incident."""
    from toast.pipeline.watch import promote

    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key required")
    result = promote(_conn(request), key)
    if not result.get("promoted"):
        raise HTTPException(404, result.get("reason", "could not promote"))
    return result


# ---------------- active research (Local Deep Research) ----------------


@router.post("/research")
def start_research(request: Request, body: dict) -> dict:
    """Queue a deep-research run. The result lands as a kind='analysis' draft
    in the NEWSROOM for review — never directly in the ledger."""
    from toast.queue import jobs

    query = (body.get("query") or "").strip()
    if not query or len(query) < 8:
        raise HTTPException(400, "query required (be specific)")
    incident_id = body.get("incident_id")
    conn = _conn(request)
    if (
        incident_id
        and not conn.execute("SELECT 1 FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    ):
        raise HTTPException(404, "incident not found")
    job_id = jobs.enqueue(
        conn, "deep_research", {"query": query, "incident_id": incident_id}, priority=8
    )
    return {"queued": True, "job_id": job_id}


@router.post("/research/cancel")
def cancel_research(request: Request, body: dict) -> dict:
    """Cancel/dismiss a research run: pending jobs are closed out (audit trail
    kept in last_error); a failed entry is dismissed the same way."""
    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key required")
    conn = _conn(request)
    cancelled = 0
    for row in conn.execute(
        """SELECT id, payload FROM jobs
           WHERE stage IN ('deep_research', 'deep_research_poll')
             AND state IN ('queued', 'running', 'dead')"""
    ).fetchall():
        payload = json.loads(row["payload"])
        if (payload.get("research_id") or payload.get("query")) == key:
            conn.execute(
                "UPDATE jobs SET state='done', last_error='cancelled by operator' WHERE id = ?",
                (row["id"],),
            )
            cancelled += 1
    if not cancelled:
        raise HTTPException(404, "no matching research run")
    return {"ok": True, "cancelled_jobs": cancelled}


# ---------------- newsroom: drafts ----------------

DRAFT_TRANSITIONS = {
    "draft": {"needs_review", "rejected"},
    "needs_review": {"approved", "draft", "rejected"},
    "approved": {"published", "draft"},
}
OPERATOR = "operator"  # single-operator system; auth arrives if UI is ever exposed


@router.post("/incidents/{incident_id}/draft")
def create_draft(request: Request, incident_id: str, body: dict) -> dict:
    """Generate an AI draft update for an incident over an observed_at window."""
    from toast.core.fetch import FetchError
    from toast.llm.client import LLMError
    from toast.pipeline.drafting import generate_draft

    try:
        result = generate_draft(
            _conn(request),
            incident_id,
            window_start=body.get("window_start"),
            window_end=body.get("window_end"),
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (LLMError, FetchError) as exc:
        raise HTTPException(503, f"LLM unavailable: {exc}") from exc
    if not result.get("created"):
        raise HTTPException(422, result.get("reason", "nothing to draft"))
    return result


@router.get("/drafts")
def list_drafts(
    request: Request, status: str | None = None, archived: bool = False
) -> dict:
    clauses, params = [f"d.archived = {1 if archived else 0}"], []
    if status:
        clauses.append("d.status = ?")
        params.append(status)
    where = "WHERE " + " AND ".join(clauses)
    conn = _conn(request)
    rows = conn.execute(
        f"""SELECT d.id, d.incident_id, d.kind, d.status, d.title, d.window_start,
               d.window_end, d.approved_by, d.approved_at, d.created_at, d.updated_at,
               d.parent_id, i.canonical_name AS incident_name,
               json_array_length(d.cited_fact_ids) AS cited_count
        FROM drafts d LEFT JOIN incidents i ON i.id = d.incident_id
        {where}
        ORDER BY d.updated_at DESC""",
        params,
    ).fetchall()
    return {"drafts": [dict(r) for r in rows], "research_jobs": _active_research(conn)}


def _active_research(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """In-flight deep-research runs for the draft queue — so the operator can
    see a research is working before its draft lands. Dead runs from the last
    24h surface as 'failed' so silent losses are visible."""
    rows = conn.execute(
        """SELECT id, stage, state, payload, run_after, created_at, last_error
           FROM jobs
           WHERE stage IN ('deep_research', 'deep_research_poll')
             AND (state IN ('queued', 'running')
                  OR (state = 'dead'
                      AND unixepoch('now') - unixepoch(created_at) < 86400))
           ORDER BY created_at ASC"""
    ).fetchall()
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = json.loads(row["payload"])
        query = payload.get("query", "research")
        key = payload.get("research_id") or query  # poll hops share research_id
        if row["state"] == "dead":
            state = "failed"
        elif payload.get("rl_hops") and row["state"] == "queued":
            state = "parked"  # waiting out LDR's rate limiter
        elif row["stage"] == "deep_research":
            state = "starting"
        else:
            state = "researching"
        incident_id = payload.get("incident_id")
        incident_name = None
        if incident_id:
            inc = conn.execute(
                "SELECT canonical_name FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            incident_name = inc["canonical_name"] if inc else None
        # later rows (newer hops) overwrite earlier ones; failed wins over stale
        prior = items.get(key)
        if prior and prior["state"] == "failed" and state != "failed":
            continue
        items[key] = {
            "key": key,
            "query": query,
            "state": state,
            "incident_id": incident_id,
            "incident_name": incident_name,
            "since": row["created_at"],
            "error": (row["last_error"] or "")[:140] if row["state"] == "dead" else None,
        }
    return list(items.values())


@router.get("/drafts/{draft_id}")
def get_draft(request: Request, draft_id: str) -> dict:
    conn = _conn(request)
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    draft = dict(row)
    cited_ids = json.loads(draft.get("cited_fact_ids") or "[]")
    cited = []
    if cited_ids:
        placeholders = ",".join("?" * len(cited_ids))
        cited = [
            dict(r)
            for r in conn.execute(
                f"""SELECT f.id, f.content, f.kind, f.tier, f.observed_at, s.name AS source_name
                    FROM facts f JOIN sources s ON s.id = f.source_id
                    WHERE f.id IN ({placeholders})
                    -- newest observation first: without this, IN() returns
                    -- index-walk order and the cited rail reads as random
                    ORDER BY f.observed_at DESC""",
                cited_ids,
            )
        ]
    pubs = [
        dict(r)
        for r in conn.execute(
            """SELECT id, channel, status, external_url, sent_at
               FROM publications WHERE draft_id = ? ORDER BY sent_at""",
            (draft_id,),
        )
    ]
    return {"draft": draft, "cited_facts": cited, "publications": pubs}


@router.post("/drafts/statewide")
def statewide_draft(request: Request) -> dict:
    """One roundup draft covering every active fire — composed mechanically
    from the ledger (zero invention risk), every figure cited. The statewide
    graphic attaches via the normal GRAPHIC button."""
    from toast.pipeline.drafting import generate_statewide_roundup

    return generate_statewide_roundup(_conn(request))


@router.post("/drafts/{draft_id}/archive")
def archive_draft(request: Request, draft_id: str, body: dict) -> dict:
    """Move a draft out of (or back into) the working queue. Orthogonal to
    status: nothing about the state machine or immutability changes."""
    conn = _conn(request)
    val = 1 if body.get("archived", True) else 0
    cur = conn.execute("UPDATE drafts SET archived = ? WHERE id = ?", (val, draft_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "draft not found")
    return {"ok": True, "draft_id": draft_id, "archived": bool(val)}


@router.delete("/drafts/{draft_id}")
def delete_draft(request: Request, draft_id: str) -> dict:
    """Hard-delete a draft that never mattered: never published, no delivery
    attempts. Anything with a public footprint archives instead — the record
    of what went out is not deletable."""
    conn = _conn(request)
    row = conn.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    if row["status"] == "published":
        raise HTTPException(409, "published drafts cannot be deleted — archive instead")
    pubs = conn.execute(
        "SELECT COUNT(*) AS n FROM publications WHERE draft_id = ?", (draft_id,)
    ).fetchone()["n"]
    if pubs:
        raise HTTPException(409, "this draft has delivery records — archive instead")
    conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
    conn.commit()
    return {"ok": True, "deleted": draft_id}


@router.post("/drafts/{draft_id}/social")
def create_social(request: Request, draft_id: str) -> dict:
    """Second writing layer: rewrite a report draft in a human voice for
    posting (full + X-length). Lands as a linked kind='social' draft."""
    from toast.core.fetch import FetchError
    from toast.llm.client import LLMError
    from toast.pipeline.social import generate_social

    try:
        return generate_social(_conn(request), draft_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (LLMError, FetchError) as exc:
        raise HTTPException(503, f"LLM unavailable: {exc}") from exc


@router.put("/drafts/{draft_id}")
def edit_draft(request: Request, draft_id: str, body: dict) -> dict:
    """Edit the draft body/title. Only allowed before publish."""
    from toast.core.db import utcnow

    conn = _conn(request)
    row = conn.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    if row["status"] == "published":
        raise HTTPException(409, "published drafts are immutable")
    conn.execute(
        "UPDATE drafts SET body_md = COALESCE(?, body_md), title = COALESCE(?, title),"
        " updated_at = ? WHERE id = ?",
        (body.get("body_md"), body.get("title"), utcnow(), draft_id),
    )
    return {"ok": True, "draft_id": draft_id}


@router.post("/drafts/{draft_id}/status")
def transition_draft(request: Request, draft_id: str, body: dict) -> dict:
    """Advance a draft through the approval workflow. The DB trigger is the
    real guard; this validates + sets approver fields before publish is possible."""
    from toast.core.db import utcnow

    conn = _conn(request)
    target = body.get("status")
    row = conn.execute("SELECT status, kind FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    current = row["status"]
    if target not in DRAFT_TRANSITIONS.get(current, set()):
        raise HTTPException(409, f"cannot go {current} → {target}")
    now = utcnow()
    if target == "approved":
        conn.execute(
            "UPDATE drafts SET approved_by = ?, approved_at = ?, updated_at = ? WHERE id = ?",
            (OPERATOR, now, now, draft_id),
        )
    try:
        conn.execute(
            "UPDATE drafts SET status = ?, updated_at = ? WHERE id = ?", (target, now, draft_id)
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, f"illegal transition: {exc}") from exc

    # Publishing to the static site needs no credentials — record the delivery
    # and rebuild the site. External channels (X/Facebook/email) remain deferred
    # until their credentials are configured; they are not triggered here.
    if target == "published":
        from toast.core.ids import ulid
        from toast.queue import jobs

        # NOTE: publishing does NOT push to any website. The operator publishes
        # to their own site by hand (NEWSROOM → ⧉ COPY / EXPORT); 'published'
        # here means "cleared for release", and the local static site is built
        # on demand from ARCHIVE, never automatically.

        # SOCIAL drafts fan out to every credentialed channel.
        channels: list[str] = []
        if row["kind"] == "social":
            from toast.core.credentials import settings_with_credentials
            from toast.publishers import configured_publishers

            for p in configured_publishers(settings_with_credentials(conn)):
                pub_id = ulid()
                conn.execute(
                    "INSERT INTO publications (id, draft_id, channel, status)"
                    " VALUES (?, ?, ?, 'queued')",
                    (pub_id, draft_id, p.channel),
                )
                jobs.enqueue(
                    conn,
                    "publish",
                    {"draft_id": draft_id, "channel": p.channel, "publication_id": pub_id},
                    priority=3,
                )
                channels.append(p.channel)
        return {"ok": True, "draft_id": draft_id, "status": target, "channels": channels}
    return {"ok": True, "draft_id": draft_id, "status": target}


@router.post("/drafts/{draft_id}/graphic")
def make_graphic(request: Request, draft_id: str) -> dict:
    """Render the incident card (map + ledger numbers) and attach it."""
    from toast.pipeline.graphics import attach_card

    try:
        return attach_card(_conn(request), draft_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/drafts/{draft_id}/graphic.png")
def get_graphic(request: Request, draft_id: str):
    from pathlib import Path as _Path

    from fastapi import Response

    from toast.core.config import Settings as _Settings

    conn = _conn(request)
    row = conn.execute("SELECT graphic_sha FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None or not row["graphic_sha"]:
        raise HTTPException(404, "no graphic attached")
    blob = conn.execute(
        "SELECT path FROM raw_blobs WHERE sha256 = ?", (row["graphic_sha"],)
    ).fetchone()
    if blob is None:
        raise HTTPException(404, "graphic missing from store")
    data = (_Path(_Settings().toast_data_dir) / blob["path"]).read_bytes()
    return Response(content=data, media_type="image/png")


@router.get("/publish/channels")
def publish_channels(request: Request) -> dict:
    """Which social channels have working credentials — the release checkboxes."""
    from toast.core.credentials import settings_with_credentials
    from toast.publishers import configured_publishers

    chans = [p.channel for p in configured_publishers(settings_with_credentials(_conn(request)))]
    return {"channels": chans}


@router.post("/drafts/{draft_id}/release")
def release_draft_route(request: Request, draft_id: str, body: dict) -> dict:
    """Release an approved social draft to SELECTED channels, now or scheduled.
    The human decision this endpoint records is the whole point — nothing
    publishes itself."""
    from toast.core.timestamps import normalize_iso
    from toast.pipeline.release import release_draft

    at = None
    if body.get("at"):
        at = normalize_iso(str(body["at"]))
        if at is None:
            raise HTTPException(400, "'at' must be an ISO timestamp")
    try:
        return release_draft(
            _conn(request), draft_id, list(body.get("channels") or []), at=at
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/publications/{publication_id}/retry")
def retry_publication_route(request: Request, publication_id: str) -> dict:
    """Re-send a definitively failed publication. 'unknown' is refused — check
    the account first; retrying an unknown is how duplicate posts happen."""
    from toast.pipeline.release import retry_publication

    try:
        return retry_publication(_conn(request), publication_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/analyze")
def analyze(request: Request, body: dict) -> dict:
    """Operator points the AI at a URL; claims are extracted, written as T4
    facts, and attributed. Safe fetcher enforces SSRF/size limits."""
    from toast.core.fetch import BlockedURLError, FetchError
    from toast.llm.client import LLMError
    from toast.pipeline.analyze import analyze_url

    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    try:
        return analyze_url(_conn(request), url)
    except BlockedURLError as exc:
        raise HTTPException(400, f"URL blocked: {exc}") from exc
    except FetchError as exc:
        raise HTTPException(502, f"fetch failed: {exc}") from exc
    except LLMError as exc:
        raise HTTPException(503, f"LLM unavailable: {exc}") from exc


@router.get("/search/fires")
def search_fires_route(request: Request, q: str = "", year: int | None = None) -> dict:
    """Search the external CAL FIRE catalog for historical fires (read-only)."""
    from toast.core.fetch import FetchError
    from toast.pipeline.fire_search import search_fires

    try:
        return {"results": search_fires(_conn(request), q, year=year)}
    except FetchError as exc:
        raise HTTPException(502, f"CAL FIRE catalog unavailable: {exc}") from exc


@router.post("/import/fire")
def import_fire_route(request: Request, body: dict) -> dict:
    """Import one historical fire (by CAL FIRE ref_id) into the ledger."""
    from toast.core.fetch import FetchError
    from toast.pipeline.fire_search import import_fire

    ref_id = (body.get("ref_id") or "").strip()
    if not ref_id:
        raise HTTPException(400, "ref_id required")
    try:
        result = import_fire(_conn(request), ref_id)
    except FetchError as exc:
        raise HTTPException(502, f"CAL FIRE catalog unavailable: {exc}") from exc
    if not result.get("imported") and result.get("reason") == "not found in CAL FIRE catalog":
        raise HTTPException(404, "fire not found in CAL FIRE catalog")
    return result


@router.post("/sources/probe")
def probe_source(request: Request, body: dict) -> dict:
    """Detect what a URL is and propose an adapter + config (add-source flow)."""
    from dataclasses import asdict

    from toast.core.fetch import BlockedURLError, FetchError
    from toast.sources.probe import probe_url

    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    try:
        return asdict(probe_url(url))
    except BlockedURLError as exc:
        raise HTTPException(400, f"URL blocked: {exc}") from exc
    except FetchError as exc:
        raise HTTPException(502, f"could not fetch URL: {exc}") from exc


@router.post("/sources")
def create_source(request: Request, body: dict) -> dict:
    """Create a source from a probe result. Only known adapters are accepted."""
    import json as _json

    from toast.core.db import utcnow
    from toast.core.ids import ulid
    from toast.sources import get_adapter

    adapter = (body.get("adapter") or "").strip()
    name = (body.get("name") or "").strip()
    config = body.get("config") or {}
    tier = int(body.get("tier", 5))
    enabled = 1 if body.get("enabled", True) else 0
    schedule = body.get("schedule", "600")
    if not adapter or get_adapter(adapter) is None:
        raise HTTPException(400, f"unknown adapter {adapter!r}")
    if not name:
        raise HTTPException(400, "name required")
    if not (1 <= tier <= 5):
        raise HTTPException(400, "tier must be 1-5")

    conn = _conn(request)
    source_id = ulid()
    now = utcnow()
    conn.execute(
        """INSERT INTO sources (id, name, type, adapter, tier, config, schedule, enabled,
                                tos_notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            source_id,
            name,
            body.get("type", "scrape"),
            adapter,
            tier,
            _json.dumps(config),
            schedule,
            enabled,
            body.get("tos_notes", "Operator-added via URL probe."),
            now,
            now,
        ),
    )
    return {"ok": True, "source_id": source_id, "name": name, "adapter": adapter}


@router.get("/sources/suggestions")
def source_suggestions(request: Request) -> dict:
    """Cross-reference: rescan dossiers/article links and list unknown domains
    awaiting the operator's APPROVE/DISMISS."""
    from toast.pipeline.source_discovery import pending_suggestions, scan_for_suggestions

    conn = _conn(request)
    scan_for_suggestions(conn)
    return {"suggestions": pending_suggestions(conn)}


@router.post("/sources/suggestions/{suggestion_id}/approve")
def approve_suggestion(request: Request, suggestion_id: str, body: dict | None = None) -> dict:
    """Approve a suggested domain: discover its feed, add it as a real source.
    Fails (suggestion stays pending) when no feed can be found."""
    import json as _json

    from toast.core.db import utcnow
    from toast.core.ids import ulid
    from toast.sources.probe import discover_feed

    conn = _conn(request)
    row = conn.execute("SELECT * FROM source_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "suggestion not found")
    if row["status"] != "pending":
        raise HTTPException(409, f"suggestion already {row['status']}")

    tier = int((body or {}).get("tier", 4))
    if not (3 <= tier <= 5):
        raise HTTPException(400, "suggested sources start at tier 3-5")
    feed = discover_feed(f"https://{row['domain']}/")
    if feed is None:
        raise HTTPException(
            422,
            f"no RSS/Atom feed found on {row['domain']} — "
            "add it manually with a specific feed URL if you know one",
        )

    source_id = ulid()
    now = utcnow()
    conn.execute(
        """INSERT INTO sources (id, name, type, adapter, tier, config, schedule, enabled,
                                tos_notes, created_at, updated_at)
           VALUES (?, ?, 'scrape', 'rss', ?, ?, '900', 1, ?, ?, ?)""",
        (
            source_id,
            row["domain"],
            tier,
            _json.dumps({"feed_url": feed, "keywords": "fire", "max_entries": 10}),
            "Added via cross-reference suggestion (operator-approved).",
            now,
            now,
        ),
    )
    conn.execute(
        "UPDATE source_suggestions SET status = 'approved', source_id = ? WHERE id = ?",
        (source_id, suggestion_id),
    )
    return {"ok": True, "source_id": source_id, "feed_url": feed, "domain": row["domain"]}


@router.post("/sources/suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(request: Request, suggestion_id: str) -> dict:
    conn = _conn(request)
    cur = conn.execute(
        "UPDATE source_suggestions SET status = 'dismissed' WHERE id = ? AND status = 'pending'",
        (suggestion_id,),
    )
    if cur.rowcount == 0:
        raise HTTPException(404, "no pending suggestion with that id")
    return {"ok": True}


@router.get("/sources")
def list_sources(request: Request) -> dict:
    rows = (
        _conn(request)
        .execute(
            """SELECT s.id, s.name, s.type, s.adapter, s.tier, s.schedule, s.enabled,
                  s.tos_notes, s.cursor,
                  COUNT(f.id) AS fact_count, MAX(f.ingested_at) AS last_fact_at,
                  st.lead_time_median_s, st.superseded_rate, st.yield_facts_per_day,
                  st.suggested_tier, st.computed_at AS stats_computed_at
           FROM sources s
           LEFT JOIN facts f ON f.source_id = s.id
           LEFT JOIN source_stats st ON st.source_id = s.id
           GROUP BY s.id ORDER BY s.tier, s.name"""
        )
        .fetchall()
    )
    return {"sources": [dict(r) for r in rows]}


@router.post("/sources/{source_id}/tier")
def set_source_tier(request: Request, source_id: str, body: dict) -> dict:
    """Operator approves a tier change. Past facts keep the tier they were
    written with — a promotion never rewrites history."""
    from toast.pipeline.trust import apply_tier

    try:
        return apply_tier(_conn(request), source_id, int(body.get("tier", 0)))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/sources/{source_id}/tier/dismiss")
def dismiss_tier_suggestion(request: Request, source_id: str) -> dict:
    from toast.pipeline.trust import dismiss_suggestion

    dismiss_suggestion(_conn(request), source_id)
    return {"ok": True}


@router.post("/facts/reassess")
def reassess_facts(request: Request) -> dict:
    """Recompute corroboration for every embedded fact under the current rules.
    fact_assessments is derived — rebuilding it never touches the ledger."""
    from toast.queue import jobs

    job_id = jobs.enqueue(_conn(request), "reassess", {}, priority=8)
    return {"ok": True, "job_id": job_id}


@router.post("/sources/stats/recompute")
def recompute_source_stats(request: Request) -> dict:
    """Run the trust loop now instead of waiting for the nightly job."""
    from toast.pipeline.trust import compute_source_stats

    stats = compute_source_stats(_conn(request))
    return {
        "computed": len(stats),
        "suggestions": [s for s in stats if s["suggested_tier"] is not None],
    }
