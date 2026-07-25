"""Incident graphic: a stats card with a real map, attached to a post.

A social post with a map travels farther than text, and the operator should
not need a design tool mid-incident. One call renders a 1200x675 card: fire
name, the ledger's current numbers, the evacuation state VERBATIM when one is
active, and a map strip — USGS satellite imagery for a single fire (real
terrain reads better under a fire ring; operator decision), the CARTO dark
base for the statewide/All-California card — with the fire ring and (when we
have it) the mapped perimeter drawn on top.

Honesty carries into the pixels: numbers come from the ledger with their
supersession chain (never invented), the evac banner is the official status
word, each map credits its tile source, and the footer timestamps the
generation — a card screenshotted three days later says when it was true.
"""

from __future__ import annotations

import io
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from toast.core.db import utc_ago, utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger

W, H = 1200, 675
MAP_H = 300
TILE = 256
TILE_URL = "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"
# USGS National Map orthoimagery — public domain, no key. The INCIDENT card
# uses this (operator decision: a single fire's snapshot reads better on real
# terrain); the statewide/All-California card stays on the dark base. Note the
# {z}/{y}/{x} order — USGS puts row before column.
SAT_TILE_URL = (
    "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/"
    "MapServer/tile/{z}/{y}/{x}"
)

BG = (16, 20, 26)
PANEL = (27, 33, 40)
TXT = (232, 236, 241)
DIM = (150, 160, 172)
EMBER = (255, 122, 61)
EVAC = (255, 77, 143)
OK = (74, 222, 128)
LINE = (44, 52, 62)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _tile_frac(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2**z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def _stitch_map(
    lat: float, lon: float, *, zoom: int = 11, w: int, h: int, url: str = TILE_URL
) -> Image.Image:
    """Centre a (w x h) crop of map tiles on the fire. A failed tile leaves a
    dark square, never fails the card — the numbers still publish."""
    img = Image.new("RGB", (w, h), BG)
    cx, cy = _tile_frac(lat, lon, zoom)
    px, py = cx * TILE, cy * TILE  # fire position in global pixel space
    left, top = px - w / 2, py - h / 2
    tx0, ty0 = int(left // TILE), int(top // TILE)
    tx1, ty1 = int((left + w) // TILE), int((top + h) // TILE)
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            try:
                body, _ct = fetch(
                    url.format(z=zoom, x=tx, y=ty),
                    accept_types=("image/png", "image/jpeg", "application/octet-stream"),
                    max_bytes=1 * 1024 * 1024,
                )
                tile = Image.open(io.BytesIO(body)).convert("RGB")
            except Exception:
                continue  # dark square beats no card
            img.paste(tile, (int(tx * TILE - left), int(ty * TILE - top)))
    return img


def _draw_perimeter(
    conn: sqlite3.Connection,
    draw: ImageDraw.ImageDraw,
    incident_id: str,
    lat: float,
    lon: float,
    *,
    zoom: int,
    w: int,
    h: int,
    off_y: int,
    off_x: int = 0,
    scale: float = 1.0,
) -> bool:
    """Overlay the cached mapped perimeter, if this fire has one. `scale`
    matches a fractionally-zoomed strip (stitched at `zoom`, downscaled by
    `scale`) — deltas from the centre shrink by the same factor. off_x/off_y
    are where the strip sits on the card."""
    import json as _json

    row = conn.execute("SELECT payload FROM map_cache WHERE key = 'perimeters:ca'").fetchone()
    if not row:
        return False
    cx, cy = _tile_frac(lat, lon, zoom)

    def to_px(pt: list[float]) -> tuple[float, float]:
        fx, fy = _tile_frac(pt[1], pt[0], zoom)
        return (
            (fx - cx) * TILE * scale + w / 2 + off_x,
            (fy - cy) * TILE * scale + h / 2 + off_y,
        )

    drew = False
    for feature in _json.loads(row["payload"]).get("features", []):
        if (feature.get("properties") or {}).get("incident_id") != incident_id:
            continue
        geom = feature.get("geometry") or {}
        rings = geom.get("coordinates") or []
        if geom.get("type") == "Polygon":
            rings = [rings]
        for poly in rings:
            for ring in poly:
                pts = [to_px(p) for p in ring if isinstance(p, list) and len(p) >= 2]
                if len(pts) > 2:
                    draw.polygon(pts, outline=EMBER, width=3)
                    drew = True
    return drew


def _perimeter_bbox(
    conn: sqlite3.Connection, incident_id: str
) -> tuple[float, float, float, float] | None:
    """(min_lat, min_lon, max_lat, max_lon) of this fire's mapped perimeter."""
    row = conn.execute("SELECT payload FROM map_cache WHERE key = 'perimeters:ca'").fetchone()
    if not row:
        return None
    lats: list[float] = []
    lons: list[float] = []
    try:
        features = json.loads(row["payload"]).get("features", [])
    except ValueError:
        return None
    for feature in features:
        if (feature.get("properties") or {}).get("incident_id") != incident_id:
            continue
        geom = feature.get("geometry") or {}
        rings = geom.get("coordinates") or []
        if geom.get("type") == "Polygon":
            rings = [rings]
        for poly in rings:
            for ring in poly:
                for p in ring:
                    if isinstance(p, list) and len(p) >= 2:
                        lons.append(p[0])
                        lats.append(p[1])
    if not lats:
        return None
    return min(lats), min(lons), max(lats), max(lons)


def _latest(conn: sqlite3.Connection, incident_id: str, kind: str, col: str) -> Any:
    row = conn.execute(
        f"""SELECT {col} AS v FROM facts
            WHERE incident_id = ? AND kind = ? AND {col} IS NOT NULL
              AND id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
            ORDER BY (observed_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')) ASC,
              (tier >= 3) ASC, observed_at DESC LIMIT 1""",
        (incident_id, kind),
    ).fetchone()
    return row["v"] if row else None


def _wx_line(conn: sqlite3.Connection, incident_id: str) -> str | None:
    """Wind now, forecast peak, AQI — the operational context, from caches."""
    import json as _json

    parts: list[str] = []
    row = conn.execute(
        "SELECT payload FROM incident_weather WHERE incident_id = ?", (incident_id,)
    ).fetchone()
    if row:
        try:
            wx = _json.loads(row["payload"])
        except ValueError:
            wx = {}
        if wx.get("wind_mph") is not None:
            gust = f" g {int(wx['gust_mph'])}" if wx.get("gust_mph") else ""
            compass = wx.get("wind_compass") or ""
            parts.append(f"WIND {compass} {int(wx['wind_mph'])}{gust} MPH".strip())
        fc = wx.get("forecast") or {}
        if fc.get("peak_wind_mph") is not None:
            rising = " RISING" if fc.get("rising") else ""
            parts.append(f"NEXT 12H PEAK {int(fc['peak_wind_mph'])}{rising}")
    aqi_row = conn.execute(
        "SELECT payload FROM map_cache WHERE key = ?", (f"airnow:{incident_id}",)
    ).fetchone()
    if aqi_row:
        try:
            worst = (_json.loads(aqi_row["payload"]) or {}).get("worst")
        except ValueError:
            worst = None
        if worst:
            parts.append(f"AQI {worst['aqi']} {(worst.get('category') or '').upper()}".strip())
    return " | ".join(parts) if parts else None


def _newest_observed(conn: sqlite3.Connection, incident_id: str) -> str | None:
    row = conn.execute(
        """SELECT MAX(observed_at) AS m FROM facts
           WHERE incident_id = ? AND kind IN ('acreage', 'containment', 'evac')""",
        (incident_id,),
    ).fetchone()
    return row["m"] if row and row["m"] else None


def generate_card(conn: sqlite3.Connection, incident_id: str) -> bytes:
    inc = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if inc is None:
        raise ValueError("incident not found")

    acreage = _latest(conn, incident_id, "acreage", "acreage")
    containment = _latest(conn, incident_id, "containment", "containment_pct")
    # evac is zone-authoritative: a graphic must never announce an order that a
    # county has already reduced or lifted (the zone has dropped from the layer)
    from toast.pipeline.evacstate import evac_state

    evac = evac_state(conn, incident_id).status
    evac_active = evac in ("order", "warning", "shelter_in_place")

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    y = 30

    # header (glyph-safe: the bundled font has no emoji or long dashes)
    d.text((40, y), inc["canonical_name"].upper(), font=_font(50), fill=TXT)
    y += 66
    sub = " - ".join(
        s for s in [f"{inc['county']} County" if inc["county"] else None, "California"] if s
    )
    d.text((40, y), sub, font=_font(20), fill=DIM)
    y += 38

    # evacuation banner: the official status word, verbatim, never paraphrased
    if evac_active:
        d.rectangle([(40, y), (W - 40, y + 50)], outline=EVAC, width=2)
        label = {
            "order": "EVACUATION ORDER IN EFFECT",
            "warning": "EVACUATION WARNING IN EFFECT",
            "shelter_in_place": "SHELTER IN PLACE IN EFFECT",
        }[evac]
        d.text((58, y + 11), f"! {label} - SEE OFFICIAL NOTICE", font=_font(25), fill=EVAC)
        y += 64

    # four stat tiles: the numbers plus WHEN it started. started_at is not a
    # column — it is the first observation, same as the incident list computes.
    srow = conn.execute(
        "SELECT MIN(observed_at) AS m FROM facts WHERE incident_id = ?", (incident_id,)
    ).fetchone()
    started = ((srow["m"] if srow else None) or inc["created_at"] or "")[:10]
    started_disp = started[5:].replace("-", "/") if started else "n/a"
    # how long it has burned — day 1 is the day it started
    days = None
    if started:
        from datetime import UTC, date, datetime

        try:
            days = (datetime.now(UTC).date() - date.fromisoformat(started)).days + 1
        except ValueError:
            days = None
    tiles = [
        ("ACRES", f"{int(acreage):,}" if acreage is not None else "n/a", EMBER),
        ("CONTAINED", f"{int(containment)}%" if containment is not None else "n/a", OK),
        (f"STARTED {started_disp}", f"DAY {days}" if days else started_disp, DIM),
        ("STATUS", (inc["status"] or "n/a").upper(), DIM),
    ]
    tw = (W - 80 - 3 * 14) // 4
    for i, (label, value, color) in enumerate(tiles):
        x0 = 40 + i * (tw + 14)
        d.rectangle([(x0, y), (x0 + tw, y + 84)], fill=PANEL, outline=LINE)
        d.text((x0 + 16, y + 10), label, font=_font(16), fill=DIM)
        d.text((x0 + 16, y + 32), value, font=_font(36), fill=color)
    y += 98

    # map takes whatever height remains above a fixed footer band — the evac
    # banner used to push the footer into the map and clip the badge
    footer_band = 96
    map_top = y
    map_h = H - map_top - footer_band
    if inc["lat"] is not None and inc["lon"] is not None and map_h > 120:
        # Fit the frame to the FIRE, not the origin point: a 69,000-acre
        # perimeter overflowed the fixed zoom-11 crop centred on where it
        # started. With a mapped perimeter, centre on its bounding box and
        # pick the deepest zoom that holds it with padding; without one,
        # back the zoom off as the acreage grows.
        map_w = W - 80
        c_lat, c_lon, zoom = inc["lat"], inc["lon"], 11
        scale = 1.0  # <1 when the strip is stitched deeper and downscaled
        bbox = _perimeter_bbox(conn, incident_id)
        if bbox:
            # FRACTIONAL zoom: integer tile zooms halve/double the scale, so a
            # fire that just misses one level fell a whole level out and floated
            # in an empty frame. Instead: compute the exact scale that makes the
            # perimeter fill ~90% of the strip, stitch at the next-deeper integer
            # zoom, and downscale — the fire always fills the frame.
            min_lat, min_lon, max_lat, max_lon = bbox
            c_lat, c_lon = (min_lat + max_lat) / 2, (min_lon + max_lon) / 2
            zr = 12  # reference zoom for measuring the bbox in pixels
            x0, y0 = _tile_frac(max_lat, min_lon, zr)
            x1, y1 = _tile_frac(min_lat, max_lon, zr)
            bw, bh = max((x1 - x0) * TILE, 1.0), max((y1 - y0) * TILE, 1.0)
            fit = min(map_w * 0.90 / bw, map_h * 0.86 / bh)
            ze = zr + math.log2(max(fit, 1e-6))  # exact (fractional) zoom
            ze = max(7.0, min(14.0, ze))  # sanity: stay within useful imagery
            zoom = min(15, math.ceil(ze))
            scale = 2 ** (ze - zoom)  # <= 1: how much to shrink the deep stitch
        elif acreage:
            zoom = 13 if acreage < 300 else 12 if acreage < 3000 else 10 if acreage < 20000 else 9
        # satellite imagery for a single fire's snapshot — real terrain reads
        # better than the dark base (operator decision; statewide stays dark)
        if scale < 0.999:
            big_w, big_h = math.ceil(map_w / scale), math.ceil(map_h / scale)
            big = _stitch_map(c_lat, c_lon, zoom=zoom, w=big_w, h=big_h, url=SAT_TILE_URL)
            strip = big.resize((map_w, map_h), Image.LANCZOS)
        else:
            strip = _stitch_map(c_lat, c_lon, zoom=zoom, w=map_w, h=map_h, url=SAT_TILE_URL)
        img.paste(strip, (40, map_top))
        d2 = ImageDraw.Draw(img)
        _draw_perimeter(
            conn, d2, incident_id, c_lat, c_lon,
            zoom=zoom, w=map_w, h=map_h, off_y=map_top, off_x=40, scale=scale,
        )
        # origin ring at the fire's true position in this frame
        ocx, ocy = _tile_frac(c_lat, c_lon, zoom)
        ofx, ofy = _tile_frac(inc["lat"], inc["lon"], zoom)
        cxp = 40 + map_w / 2 + (ofx - ocx) * TILE * scale
        cyp = map_top + map_h / 2 + (ofy - ocy) * TILE * scale
        if 40 < cxp < W - 40 and map_top < cyp < map_top + map_h:
            for r, col in ((14, EVAC if evac_active else EMBER), (7, TXT)):
                d2.ellipse([(cxp - r, cyp - r), (cxp + r, cyp + r)], outline=col, width=3)
        d2.rectangle([(40, map_top), (W - 40, map_top + map_h)], outline=LINE)
        d2.text(
            (W - 46, map_top + map_h - 24), "Imagery: USGS The National Map (public domain)",
            font=_font(14), fill=DIM, anchor="ra",
        )
    else:
        d.rectangle([(40, map_top), (W - 40, map_top + map_h)], fill=PANEL, outline=LINE)
        d.text((60, map_top + 20), "location not yet confirmed", font=_font(20), fill=DIM)

    # context line: wind now, forecast, smoke — then the brand footer
    fy = map_top + map_h + 10
    wx = _wx_line(conn, incident_id)
    if wx:
        d.text((40, fy), wx, font=_font(17), fill=DIM)
        fy += 26
    from toast.core.timefmt import civilian

    stamp = civilian(utcnow())
    newest = _newest_observed(conn, incident_id)
    asof = f"data as of {civilian(newest)} - " if newest else ""
    badge_path = Path(__file__).resolve().parents[1] / "assets" / "ops-badge.png"
    tx = 40
    if badge_path.exists():
        from PIL import Image as _Image

        badge = _Image.open(badge_path).convert("RGBA")
        badge.thumbnail((46, 46), _Image.LANCZOS)
        img.paste(badge, (40, fy - 4), badge)
        tx = 40 + badge.width + 12
    d.text((tx, fy + 8), "TOAST REPORT OPS", font=_font(22), fill=EMBER)
    from toast.core.appsettings import brand_line

    brand = brand_line(conn)
    if brand:
        d.text((tx, fy + 34), brand[:70], font=_font(14), fill=DIM)
    d.text(
        (W - 40, fy + 11), f"{asof}generated {stamp}Z",
        font=_font(15), fill=DIM, anchor="ra",
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    get_logger("graphics").info(
        "card_rendered", incident=incident_id, bytes=buf.tell(), evac=bool(evac_active)
    )
    return buf.getvalue()


def attach_card(conn: sqlite3.Connection, draft_id: str) -> dict[str, Any]:
    """Render the card for a draft's incident and attach it to the draft."""
    from pathlib import Path

    from toast.core.config import Settings
    from toast.core.rawstore import store_raw

    draft = conn.execute(
        "SELECT incident_id, status, title FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()
    if draft is None:
        raise ValueError("draft not found")
    if draft["incident_id"] is not None:
        png = generate_card(conn, draft["incident_id"])
    elif (draft["title"] or "").startswith("CALIFORNIA WILDFIRE ROUNDUP"):
        # the statewide roundup gets the statewide card
        png = generate_state_card(conn)
    else:
        raise ValueError("draft has no incident — nothing to draw")
    sha = store_raw(conn, Path(Settings().toast_data_dir), png, "image/png", url=None)
    conn.execute("UPDATE drafts SET graphic_sha = ? WHERE id = ?", (sha, draft_id))
    conn.commit()
    return {"draft_id": draft_id, "graphic_sha": sha, "bytes": len(png)}


# ------------------------------------------------------------- statewide card

CA_CENTER = (37.3, -119.6)


def generate_state_card(conn: sqlite3.Connection) -> bytes:
    """One card for the whole state: every active fire on a CA map, sized by
    acreage, evac fires in the alarm colour, aggregate tiles up top."""
    rows = conn.execute(
        """SELECT i.id, i.canonical_name, i.lat, i.lon, i.calfire_id, i.irwin_id,
             (SELECT v.acreage FROM facts v WHERE v.incident_id = i.id AND v.kind='acreage'
                AND v.id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
              ORDER BY (v.observed_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')) ASC,
                (v.tier >= 3) ASC, v.observed_at DESC LIMIT 1) AS acreage
           FROM incidents i
           WHERE i.status = 'active' AND COALESCE(i.incident_type, 'WF') != 'RX'"""
    ).fetchall()
    # evac is zone-authoritative, computed off the shared cache read, not the
    # facts pick — so "UNDER EVAC" counts and pink rings track reductions/lifts
    from toast.pipeline.evacstate import evac_state, zone_index

    zidx = zone_index(conn)
    fires = []
    for r in rows:
        d = dict(r)
        d["evac"] = evac_state(conn, d["id"], zone_index=zidx).status
        fires.append(d)

    # The published number is HARD FACT: only fires an official source lists
    # active RIGHT NOW — CAL FIRE's live list or NIFC's federal current layer,
    # checked at render time. Fires the workstation tracks beyond that
    # (watch-promoted, pending operator close) stay off the public card.
    # If a live list is unreachable, its half degrades to "carries that
    # official id" rather than dropping everyone.
    from toast.pipeline.activity import _calfire_live, _wfigs_live

    calfire = _calfire_live(conn)
    wfigs = _wfigs_live()

    def officially_listed(f: dict) -> bool:
        cfid = (f["calfire_id"] or "").lower()
        iid = (f["irwin_id"] or "").strip("{}").lower()
        if cfid:
            if calfire:
                entry = calfire.get(cfid)
                if entry and entry["is_active"]:
                    return True
            else:  # CAL FIRE unreachable — the id itself is the fallback fact
                return True
        if iid:
            return iid in wfigs if wfigs is not None else True
        return False

    dropped = [f["canonical_name"] for f in fires if not officially_listed(f)]
    fires = [f for f in fires if officially_listed(f)]
    if dropped:
        get_logger("graphics").info(
            "state_card_excluded_unlisted", count=len(dropped), names=dropped[:10]
        )
    evac_n = sum(1 for f in fires if f["evac"] in ("order", "warning", "shelter_in_place"))
    total = sum(int(f["acreage"]) for f in fires if f["acreage"] is not None)
    new24 = conn.execute(
        """SELECT COUNT(*) FROM incidents WHERE status='active'
           AND unixepoch('now') - unixepoch(created_at) < 86400"""
    ).fetchone()[0]

    # ---- the glance panel: what a reporter checks after the headline numbers
    # 24h growth: current acres minus each fire's newest official-first figure
    # from before the window; a fire with no prior figure is all new growth
    growth24 = 0
    for f in fires:
        if f["acreage"] is None:
            continue
        base = conn.execute(
            """SELECT acreage FROM facts
               WHERE incident_id = ? AND kind = 'acreage' AND acreage IS NOT NULL
                 AND observed_at <= strftime('%Y-%m-%dT%H:%M:%fZ','now','-24 hours')
               ORDER BY (tier >= 3) ASC, observed_at DESC LIMIT 1""",
            (f["id"],),
        ).fetchone()
        growth24 += int(f["acreage"]) - int(base["acreage"] if base else 0)
    # containment, weighted by acres — "how contained is the state's fire load"
    cw = [
        (int(f["acreage"]), c["containment_pct"])
        for f in fires
        if f["acreage"]
        and (c := conn.execute(
            """SELECT containment_pct FROM facts
               WHERE incident_id = ? AND kind='containment' AND containment_pct IS NOT NULL
                 AND id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
               ORDER BY (tier >= 3) ASC, observed_at DESC LIMIT 1""",
            (f["id"],),
        ).fetchone())
    ]
    contained_pct = round(sum(a * p for a, p in cw) / sum(a for a, _ in cw)) if cw else None
    aircraft_up = conn.execute(
        """SELECT COUNT(DISTINCT hex) FROM aircraft_positions
           WHERE fire_ops = 1 AND seen_at >= ?""",
        (utc_ago(3600),),
    ).fetchone()[0]
    redflag = 0
    fw = conn.execute("SELECT payload FROM map_cache WHERE key='firewx:ca'").fetchone()
    if fw:
        import contextlib

        with contextlib.suppress(ValueError):
            redflag = len(json.loads(fw["payload"]).get("features") or [])
    top5 = sorted(
        (f for f in fires if f["acreage"]), key=lambda f: f["acreage"], reverse=True
    )[:5]

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    y = 30
    d.text((40, y), "CALIFORNIA WILDFIRE SITUATION", font=_font(46), fill=TXT)
    y += 60
    d.text((40, y), utcnow()[:10], font=_font(20), fill=DIM)
    y += 36

    tiles = [
        ("ACTIVE FIRES", str(len(fires)), EMBER),
        ("TOTAL ACRES", f"{total:,}", EMBER),
        ("UNDER EVAC", str(evac_n), EVAC if evac_n else DIM),
        ("NEW IN 24H", str(new24), DIM),
    ]
    tw = (W - 80 - 3 * 14) // 4
    for i, (label, value, color) in enumerate(tiles):
        x0 = 40 + i * (tw + 14)
        d.rectangle([(x0, y), (x0 + tw, y + 84)], fill=PANEL, outline=LINE)
        d.text((x0 + 16, y + 10), label, font=_font(16), fill=DIM)
        d.text((x0 + 16, y + 32), value, font=_font(36), fill=color)
    y += 98

    footer_band = 78
    map_top = y
    map_h = H - map_top - footer_band
    # fit the view to where the fires actually are: a fixed statewide frame
    # cropped San Diego whenever NorCal was busy
    # fit only over plausible-CA positions: one bad geocode dragged the whole
    # frame to Mexico
    placed = [
        f for f in fires
        if f["lat"] is not None and 32.0 <= f["lat"] <= 42.5 and -125.0 <= f["lon"] <= -113.5
    ]
    if placed:
        lat_c = (max(f["lat"] for f in placed) + min(f["lat"] for f in placed)) / 2
        lon_c = (max(f["lon"] for f in placed) + min(f["lon"] for f in placed)) / 2
        lat_span = max(f["lat"] for f in placed) - min(f["lat"] for f in placed)
        zoom = 6 if lat_span <= 6.5 else 5
    else:
        (lat_c, lon_c), zoom = CA_CENTER, 6
    # the map gives up its right edge to a glance panel: biggest fires named,
    # growth, containment, air response and red-flag risk in one look
    panel_w = 318
    map_w = W - 80 - panel_w - 12
    map_r = 40 + map_w
    strip = _stitch_map(lat_c, lon_c, zoom=zoom, w=map_w, h=map_h)
    img.paste(strip, (40, map_top))
    d2 = ImageDraw.Draw(img)
    cx, cy = _tile_frac(lat_c, lon_c, zoom)
    px0, py0 = cx * TILE - map_w / 2, cy * TILE - map_h / 2
    import math as _math

    top3 = {f["id"] for f in top5[:3]}
    for f in fires:
        if f["lat"] is None or f["lon"] is None:
            continue
        fx, fyy = _tile_frac(f["lat"], f["lon"], zoom)
        x, ypix = fx * TILE - px0 + 40, fyy * TILE - py0 + map_top
        if not (40 < x < map_r and map_top < ypix < map_top + map_h):
            continue
        acres = f["acreage"] or 0
        r = max(4, min(16, 3 + _math.log10(acres + 1) * 2.6))
        col = EVAC if f["evac"] in ("order", "warning", "shelter_in_place") else EMBER
        d2.ellipse([(x - r, ypix - r), (x + r, ypix + r)], outline=col, width=3)
        if f["id"] in top3:
            name = (f["canonical_name"] or "").replace(" Fire", "").upper()[:14]
            lx = x + r + 5 if x < map_r - 110 else x - r - 5
            d2.text((lx, ypix - 8), name, font=_font(15), fill=TXT,
                    anchor="la" if x < map_r - 110 else "ra")
    d2.rectangle([(40, map_top), (map_r, map_top + map_h)], outline=LINE)
    d2.text(
        (map_r - 6, map_top + map_h - 24), "(c) OpenStreetMap - (c) CARTO",
        font=_font(14), fill=DIM, anchor="ra",
    )
    d2.text(
        (46, map_top + map_h - 24),
        "ring size = mapped acres - pink = evac in effect - CAL FIRE + NIFC listed fires only",
        font=_font(14), fill=DIM,
    )

    # ---- right glance panel
    gx = map_r + 12
    d2.rectangle([(gx, map_top), (W - 40, map_top + map_h)], fill=PANEL, outline=LINE)
    py = map_top + 12
    d2.text((gx + 16, py), "LARGEST FIRES", font=_font(16), fill=DIM)
    py += 26
    for f in top5:
        nm = (f["canonical_name"] or "?")[:18]
        col = EVAC if f["evac"] in ("order", "warning", "shelter_in_place") else TXT
        d2.text((gx + 16, py), nm, font=_font(19), fill=col)
        d2.text((W - 56, py), f"{int(f['acreage']):,} ac", font=_font(19), fill=EMBER, anchor="ra")
        py += 28
    py += 10
    stat_rows = [
        ("24H CHANGE", f"{growth24:+,} ac", EMBER if growth24 > 0 else OK),
        ("CONTAINMENT (ACRE-WTD)", f"{contained_pct}%" if contained_pct is not None else "n/a",
         OK if (contained_pct or 0) >= 50 else EMBER),
        ("FIRE AIRCRAFT UP (1H)", str(aircraft_up), TXT),
        ("RED FLAG ZONES", str(redflag), EVAC if redflag else DIM),
    ]
    for label, value, col in stat_rows:
        d2.text((gx + 16, py), label, font=_font(15), fill=DIM)
        d2.text((W - 56, py), value, font=_font(21), fill=col, anchor="ra")
        py += 31

    fy = map_top + map_h + 12
    badge_path = Path(__file__).resolve().parents[1] / "assets" / "ops-badge.png"
    tx = 40
    if badge_path.exists():
        from PIL import Image as _Image

        badge = _Image.open(badge_path).convert("RGBA")
        badge.thumbnail((46, 46), _Image.LANCZOS)
        img.paste(badge, (40, fy - 2), badge)
        tx = 40 + badge.width + 12
    d.text((tx, fy + 8), "TOAST REPORT OPS", font=_font(22), fill=EMBER)
    from toast.core.appsettings import brand_line

    brand = brand_line(conn)
    if brand:
        d.text((tx, fy + 34), brand[:70], font=_font(14), fill=DIM)
    from toast.core.timefmt import civilian as _civ

    d.text(
        (W - 40, fy + 11), f"ledger data - generated {_civ(utcnow())}",
        font=_font(15), fill=DIM, anchor="ra",
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    get_logger("graphics").info("state_card_rendered", fires=len(fires), bytes=buf.tell())
    return buf.getvalue()


# ------------------------------------------------------- live map snapshot

# basemap key -> (tile url, credit line). Mirrors the on-map basemap switcher so
# a snapshot looks like what the operator was looking at. USGS services put the
# row before the column ({z}/{y}/{x}); the XYZ tile servers use {z}/{x}/{y}.
SNAP_BASEMAPS: dict[str, tuple[str, str]] = {
    "dark": (TILE_URL, "(c) OpenStreetMap  (c) CARTO"),
    "satellite": (SAT_TILE_URL, "Imagery: USGS The National Map (public domain)"),
    "osm": ("https://a.tile.openstreetmap.org/{z}/{x}/{y}.png", "(c) OpenStreetMap"),
    "street": ("https://a.tile.openstreetmap.org/{z}/{x}/{y}.png", "(c) OpenStreetMap"),
    "terrain": ("https://a.tile.opentopomap.org/{z}/{x}/{y}.png", "(c) OpenTopoMap (CC-BY-SA)"),
    "topo": (
        "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/"
        "MapServer/tile/{z}/{y}/{x}",
        "USGS The National Map (public domain)",
    ),
}


def _evac_status_color(st: str) -> tuple[int, int, int]:
    s = (st or "").lower()
    if "order" in s:
        return EVAC
    if "warn" in s:
        return WARN
    if "shelter" in s:
        return SIP
    if "advis" in s:
        return ADVISORY
    return DIM


def _draw_overlays(
    conn: sqlite3.Connection,
    d: ImageDraw.ImageDraw,
    enabled: set[str],
    *,
    clat: float,
    clon: float,
    zoom: int,
    w: int,
    h: int,
) -> list[str]:
    """Draw the operator's enabled vector overlays into the snapshot, reusing the
    exact GeoJSON/point data the live layers fetch — so a layer turned on to show
    something survives into the image. Each layer is best-effort: an outage or a
    bad shape skips THAT layer, never the whole card. Returns the layers drawn."""
    cx, cy = _tile_frac(clat, clon, zoom)
    px0, py0 = cx * TILE - w / 2, cy * TILE - h / 2

    def to_px(la: float, lo: float) -> tuple[float, float]:
        fx, fy = _tile_frac(la, lo, zoom)
        return fx * TILE - px0, fy * TILE - py0

    def in_frame(pts: list[tuple[float, float]]) -> bool:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return max(xs) >= -30 and min(xs) <= w + 30 and max(ys) >= -30 and min(ys) <= h + 30

    def rings_of(geom: dict) -> list[list]:
        gt = geom.get("type")
        coords = geom.get("coordinates") or []
        if gt == "Polygon":
            return coords
        if gt == "MultiPolygon":
            return [ring for poly in coords for ring in poly]
        if gt == "LineString":
            return [coords]
        if gt == "MultiLineString":
            return coords
        return []

    def draw_shape(features, color, width, *, status_key=None, closed=True):
        for feat in features:
            col = color
            if status_key:
                col = _evac_status_color((feat.get("properties") or {}).get(status_key, ""))
            for ring in rings_of(feat.get("geometry") or {}):
                pts = [to_px(p[1], p[0]) for p in ring if isinstance(p, list) and len(p) >= 2]
                if len(pts) < 2 or not in_frame(pts):
                    continue
                d.line(pts + ([pts[0]] if closed and len(pts) > 2 else []), fill=col, width=width)

    def draw_points(rows, color, r=3):
        for row in rows:
            la, lo = row.get("lat"), row.get("lon")
            if la is None or lo is None:
                continue
            x, yv = to_px(la, lo)
            if -20 < x < w + 20 and -20 < yv < h + 20:
                d.ellipse([(x - r, yv - r), (x + r, yv + r)], fill=color)

    drawn: list[str] = []
    log = get_logger("graphics")

    # bottom-up: reference boundaries first, alarm layers last (most visible)
    def _try(name: str, fn) -> None:
        if name not in enabled:
            return
        try:
            fn()
            drawn.append(name)
        except Exception as exc:  # one layer's outage never breaks the card
            log.info("snapshot_overlay_skipped", layer=name, error=str(exc)[:100])

    _try("counties", lambda: draw_shape(
        __import__("toast.pipeline.countylines", fromlist=["get_county_lines"])
        .get_county_lines(conn).get("features", []), (92, 102, 116), 1))
    _try("perimeters", lambda: draw_shape(
        __import__("toast.pipeline.perimeters", fromlist=["get_perimeters"])
        .get_perimeters(conn).get("features", []), EMBER, 2))
    _try("firis", lambda: draw_shape(
        __import__("toast.pipeline.firis", fromlist=["get_firis"])
        .get_firis(conn).get("features", []), (255, 180, 70), 2))
    _try("firewx", lambda: draw_shape(
        __import__("toast.pipeline.firewx", fromlist=["get_fire_weather"])
        .get_fire_weather(conn).get("features", []), WARN, 2))
    _try("evacZones", lambda: draw_shape(
        __import__("toast.pipeline.evaczones", fromlist=["get_evac_zones"])
        .get_evac_zones(conn).get("features", []), DIM, 3, status_key="STATUS"))
    _try("closures", lambda: draw_points(
        __import__("toast.pipeline.closures", fromlist=["get_closures"])
        .get_closures(conn).get("closures", []), (255, 90, 90), 4))
    _try("hotspots", lambda: draw_points(
        [dict(r) for r in conn.execute(
            "SELECT f.lat, f.lon FROM facts f JOIN sources s ON s.id = f.source_id"
            " WHERE s.type = 'satellite' AND f.lat IS NOT NULL"
            " AND unixepoch('now') - unixepoch(f.observed_at) < 86400")],
        (255, 140, 40), 2))
    _try("aircraft", lambda: draw_points(
        __import__("toast.pipeline.aircraft", fromlist=["current_aircraft"])
        .current_aircraft(conn).get("aircraft", []), (120, 200, 255), 3))
    return drawn


def generate_map_snapshot(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
    zoom: int,
    w: int,
    h: int,
    basemap: str = "dark",
    layers: str = "",
) -> bytes:
    """A shareable grab of the OPS map exactly where the operator is looking —
    same centre, zoom and basemap, freshly stitched server-side so no browser
    tile-CORS taint. Active fires are drawn on top from the ledger (ring sized by
    acres, pink under evacuation), the brand badge rides the upper-left, and a
    tag band along the bottom carries the brand line, tile credit and the
    generation time so a screenshot seen later still says when it was true."""
    w = max(320, min(2400, int(w)))
    h = max(240, min(1600, int(h)))
    zoom = max(3, min(17, int(zoom)))
    url, credit = SNAP_BASEMAPS.get(basemap, SNAP_BASEMAPS["dark"])

    strip = _stitch_map(lat, lon, zoom=zoom, w=w, h=h, url=url)
    d = ImageDraw.Draw(strip)

    # the operator's enabled overlays, drawn under the fire pins so nothing they
    # turned on to SHOW something is missing from the image
    enabled = {s for s in (layers or "").split(",") if s}
    overlays_drawn = (
        _draw_overlays(conn, d, enabled, clat=lat, clon=lon, zoom=zoom, w=w, h=h)
        if enabled
        else []
    )

    # active fires from the ledger, placed in the frame — the same picture the
    # OPS map paints, so the snapshot matches what was on screen
    from toast.pipeline.evacstate import evac_state, zone_index

    rows = conn.execute(
        "SELECT id, canonical_name, lat, lon FROM incidents"
        " WHERE status = 'active' AND lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()
    zidx = zone_index(conn)
    cx, cy = _tile_frac(lat, lon, zoom)
    px0, py0 = cx * TILE - w / 2, cy * TILE - h / 2
    # place fires biggest-first so the largest fires win the scarce label slots
    placed: list[dict] = []
    for r in rows:
        fx, fy = _tile_frac(r["lat"], r["lon"], zoom)
        x, yv = fx * TILE - px0, fy * TILE - py0
        if not (0 < x < w and 0 < yv < h):
            continue
        acres = _latest(conn, r["id"], "acreage", "acreage") or 0
        evac = evac_state(conn, r["id"], zone_index=zidx).status
        placed.append({"x": x, "y": yv, "acres": acres, "evac": evac, "name": r["canonical_name"]})
    placed.sort(key=lambda p: p["acres"], reverse=True)

    lbl_font = _font(15)
    label_boxes: list[tuple[float, float, float, float]] = []
    for p in placed:
        x, yv, acres = p["x"], p["y"], p["acres"]
        col = EVAC if p["evac"] in ("order", "warning", "shelter_in_place") else EMBER
        rad = max(5, min(18, 4 + math.log10(acres + 1) * 2.7))
        for rr, cc, wdt in ((rad, col, 3), (max(3, rad - 7), TXT, 2)):
            d.ellipse([(x - rr, yv - rr), (x + rr, yv + rr)], outline=cc, width=wdt)
        name = (p["name"] or "").upper()[:22]
        right = x < w - 150
        tw = d.textlength(name, font=lbl_font)
        lx = x + rad + 5 if right else x - rad - 5 - tw
        bx0, by0, bx1, by1 = lx, yv - 9, lx + tw, yv + 9
        # skip a label that would land on one already drawn — the ring still shows
        if any(bx0 < b[2] and bx1 > b[0] and by0 < b[3] and by1 > b[1] for b in label_boxes):
            continue
        box = (bx0, by0, bx1, by1)
        label_boxes.append(box)
        d.text((lx if right else x - rad - 5, yv - 8), name, font=lbl_font, fill=TXT,
               anchor="la" if right else "ra")
    drawn = len(placed)

    # translucent chrome: a backing for the logo, a band for the tags — one
    # alpha-composite so both sit cleanly over any basemap
    base = strip.convert("RGBA")
    band_h = 34
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([(0, 0), (min(w, 360), 58)], fill=(16, 20, 26, 170))
    od.rectangle([(0, h - band_h), (w, h)], fill=(16, 20, 26, 200))
    base = Image.alpha_composite(base, overlay)
    d = ImageDraw.Draw(base)

    # logo, upper-left
    from toast.core.appsettings import brand_line
    from toast.core.timefmt import civilian

    badge_path = Path(__file__).resolve().parents[1] / "assets" / "ops-badge.png"
    tx = 14
    if badge_path.exists():
        badge = Image.open(badge_path).convert("RGBA")
        badge.thumbnail((40, 40), Image.LANCZOS)
        base.paste(badge, (14, 9), badge)
        tx = 14 + badge.width + 10
    d.text((tx, 14), "TOAST REPORT OPS", font=_font(20), fill=EMBER)
    d.text((tx, 38), "CALIFORNIA WILDFIRE OPS", font=_font(12), fill=DIM)

    # tag band, along the bottom: brand line left, tile credit centre-right,
    # generation stamp far right
    by = h - band_h + 9
    brand = brand_line(conn)
    d.text((14, by), (brand or "Toast Report OPS")[:64], font=_font(15), fill=TXT)
    d.text((w - 250, by + 2), credit, font=_font(12), fill=DIM, anchor="ra")
    d.text(
        (w - 14, by + 2), f"generated {civilian(utcnow())}",
        font=_font(13), fill=DIM, anchor="ra",
    )

    out = base.convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    get_logger("graphics").info(
        "map_snapshot_rendered", fires=drawn, zoom=zoom, size=f"{w}x{h}",
        overlays=",".join(overlays_drawn), bytes=buf.tell(),
    )
    return buf.getvalue()


# --------------------------------------------------------------- evacuation card

# status -> the public alarm convention, matching the map and the workstation:
# order is the alarm red, warning amber, shelter blue, advisory the calm green.
WARN = (245, 190, 70)
SIP = (120, 170, 255)
ADVISORY = (74, 222, 128)
_EVAC_RANK = {"order": 4, "shelter_in_place": 3, "warning": 2, "advisory": 1}


def _evac_color(status: str | None) -> tuple[int, int, int]:
    return {
        "order": EVAC,
        "warning": WARN,
        "shelter_in_place": SIP,
        "advisory": ADVISORY,
    }.get(status or "", DIM)


def _evac_word(status: str | None) -> str:
    return {
        "order": "EVACUATION ORDER",
        "warning": "EVACUATION WARNING",
        "shelter_in_place": "SHELTER IN PLACE",
        "advisory": "ADVISORY",
    }.get(status or "", (status or "UNKNOWN").upper())


def _pacific_tz():
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo("America/Los_Angeles")
    except Exception:  # tz database missing — fall back to UTC below
        return None


def _civilian_time(iso: str | None) -> str:
    """A UTC timestamp the way a Californian reads it: local Pacific, 12-hour
    with AM/PM — never military. The evac card is public-facing; a resident
    deciding whether to leave should not have to convert 20:09Z in their head."""
    if not iso:
        return ""
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return str(iso)[:16].replace("T", " ")
    tz = _pacific_tz()
    if tz is not None:
        dt = dt.astimezone(tz)
        zone = dt.strftime("%Z")
    else:
        zone = "UTC"
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%b')} {dt.day}, {hour}:{dt.minute:02d} {ampm} {zone}"


def generate_evac_card(conn: sqlite3.Connection, incident_id: str) -> bytes:
    """A shareable card of a fire's CURRENT evacuation zones, rendered verbatim
    from the Cal OES layer. This is the one graphic where every pixel is an
    official instruction — zone ids and statuses exactly as the county published
    them, no invention, with a 'verify with your county' line and a generation
    timestamp so a screenshot seen later says when it was true."""
    inc = conn.execute(
        "SELECT canonical_name, county FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if inc is None:
        raise ValueError("incident not found")

    from toast.pipeline.evacstate import evac_state

    state = evac_state(conn, incident_id)
    zones = sorted(
        state.zones,
        key=lambda z: (-_EVAC_RANK.get(z.get("status") or "", 0), z.get("zone_id") or ""),
    )

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    y = 34
    d.text((40, y), inc["canonical_name"].upper(), font=_font(46), fill=TXT)
    y += 60
    sub = " - ".join(
        s for s in [f"{inc['county']} County" if inc["county"] else None, "California"] if s
    )
    header = f"EVACUATION STATUS - {sub}" if sub else "EVACUATION STATUS"
    d.text((40, y), header, font=_font(20), fill=DIM)
    y += 44

    if not zones:
        d.rectangle([(40, y), (W - 40, y + 60)], outline=OK, width=2)
        d.text((58, y + 16), "NO ACTIVE EVACUATION ZONES", font=_font(26), fill=OK)
        y += 78
        d.text(
            (40, y),
            "No zones are currently elevated for this fire in the Cal OES layer.",
            font=_font(18), fill=DIM,
        )
    else:
        counts: dict[str, int] = {}
        for z in zones:
            s = z.get("status") or "other"
            counts[s] = counts.get(s, 0) + 1
        summary = "   ".join(
            f"{counts[k]} {_evac_word(k).replace('EVACUATION ', '').title()}"
            for k in ("order", "shelter_in_place", "warning", "advisory")
            if counts.get(k)
        )
        d.text((40, y), summary, font=_font(22), fill=TXT)
        y += 38

        # colour KEY — the full sequence, always shown, so there is no ambiguity
        # about what a colour means: order red, warning amber, shelter blue,
        # advisory green
        key_font = _font(15)
        lx = 40
        d.text((lx, y), "KEY:", font=key_font, fill=DIM)
        lx += int(d.textlength("KEY:  ", font=key_font))
        for st in ("order", "warning", "shelter_in_place", "advisory"):
            col = _evac_color(st)
            d.rectangle([(lx, y + 2), (lx + 15, y + 17)], fill=col)
            label = _evac_word(st)
            d.text((lx + 21, y), label, font=key_font, fill=DIM)
            lx += 21 + int(d.textlength(label, font=key_font)) + 26
        y += 34

        shown = zones[:9]
        for z in shown:
            col = _evac_color(z.get("status"))
            d.rectangle([(40, y + 4), (46, y + 30)], fill=col)
            zid = str(z.get("zone_id") or z.get("zone_name") or "zone")
            d.text((58, y), zid, font=_font(22), fill=TXT)
            d.text((58 + 470, y), _evac_word(z.get("status")), font=_font(20), fill=col)
            issued = z.get("issued_at")
            if issued:
                # date AND time it was issued, in plain civilian Pacific time so a
                # resident reads it at a glance — never military, never bare UTC
                d.text(
                    (W - 40, y + 3), f"issued {_civilian_time(issued)}",
                    font=_font(15), fill=DIM, anchor="ra",
                )
            y += 38
        if len(zones) > len(shown):
            d.text(
                (58, y), f"+ {len(zones) - len(shown)} more zone(s) — see the workstation",
                font=_font(16), fill=DIM,
            )
            y += 30

    # footer: source, verify line, generation stamp, brand — the honesty band
    fy = H - 78
    d.line([(40, fy - 6), (W - 40, fy - 6)], fill=LINE, width=1)
    d.text(
        (40, fy),
        "Source: Cal OES California Evacuation Aggregation Layer - official status,",
        font=_font(15), fill=DIM,
    )
    d.text(
        (40, fy + 20),
        "rendered verbatim. ALWAYS follow the official county notice, not this card.",
        font=_font(15), fill=DIM,
    )
    d.text((40, fy + 44), f"card created {_civilian_time(utcnow())}", font=_font(14), fill=EMBER)
    from toast.core.appsettings import brand_line

    brand = brand_line(conn)
    # the logo badge rides the bottom-right, with the brand name to its left, so
    # a screenshot of an evacuation is unmistakably ours (same badge as the
    # incident and statewide cards)
    bx = W - 40
    badge_path = Path(__file__).resolve().parents[1] / "assets" / "ops-badge.png"
    if badge_path.exists():
        badge = Image.open(badge_path).convert("RGBA")
        badge.thumbnail((50, 50), Image.LANCZOS)
        img.paste(badge, (bx - badge.width, fy + 4), badge)
        bx = bx - badge.width - 12
    d.text((bx, fy + 8), "TOAST REPORT OPS", font=_font(20), fill=EMBER, anchor="ra")
    if brand:
        d.text((bx, fy + 34), brand[:50], font=_font(13), fill=DIM, anchor="ra")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    get_logger("graphics").info(
        "evac_card_rendered", incident=incident_id, zones=len(zones), bytes=buf.tell()
    )
    return buf.getvalue()
