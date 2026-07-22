"""Incident graphic: a stats card with a real map, attached to a post.

A social post with a map travels farther than text, and the operator should
not need a design tool mid-incident. One call renders a 1200x675 card: fire
name, the ledger's current numbers, the evacuation state VERBATIM when one is
active, and a map strip stitched from the same CARTO dark tiles the
workstation uses, with the fire ring and (when we have it) the mapped
perimeter drawn on top.

Honesty carries into the pixels: numbers come from the ledger with their
supersession chain (never invented), the evac banner is the official status
word, the map credits OpenStreetMap/CARTO, and the footer timestamps the
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

from toast.core.db import utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger

W, H = 1200, 675
MAP_H = 300
TILE = 256
TILE_URL = "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"

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


def _stitch_map(lat: float, lon: float, *, zoom: int = 11, w: int, h: int) -> Image.Image:
    """Centre a (w x h) crop of CARTO dark tiles on the fire. A failed tile
    leaves a dark square, never fails the card — the numbers still publish."""
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
                    TILE_URL.format(z=zoom, x=tx, y=ty),
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
) -> bool:
    """Overlay the cached mapped perimeter, if this fire has one."""
    import json as _json

    row = conn.execute("SELECT payload FROM map_cache WHERE key = 'perimeters:ca'").fetchone()
    if not row:
        return False
    cx, cy = _tile_frac(lat, lon, zoom)
    px0, py0 = cx * TILE - w / 2, cy * TILE - h / 2

    def to_px(pt: list[float]) -> tuple[float, float]:
        fx, fy = _tile_frac(pt[1], pt[0], zoom)
        return fx * TILE - px0, fy * TILE - py0 + off_y

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
    evac = _latest(conn, incident_id, "evac", "evac_status")
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
    tiles = [
        ("ACRES", f"{int(acreage):,}" if acreage is not None else "n/a", EMBER),
        ("CONTAINED", f"{int(containment)}%" if containment is not None else "n/a", OK),
        ("STARTED", started_disp, DIM),
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
        zoom = 11
        strip = _stitch_map(inc["lat"], inc["lon"], zoom=zoom, w=W - 80, h=map_h)
        img.paste(strip, (40, map_top))
        d2 = ImageDraw.Draw(img)
        _draw_perimeter(
            conn, d2, incident_id, inc["lat"], inc["lon"],
            zoom=zoom, w=W - 80, h=map_h, off_y=map_top,
        )
        cxp, cyp = 40 + (W - 80) // 2, map_top + map_h // 2
        for r, col in ((14, EVAC if evac_active else EMBER), (7, TXT)):
            d2.ellipse([(cxp - r, cyp - r), (cxp + r, cyp + r)], outline=col, width=3)
        d2.rectangle([(40, map_top), (W - 40, map_top + map_h)], outline=LINE)
        d2.text(
            (W - 46, map_top + map_h - 24), "(c) OpenStreetMap - (c) CARTO",
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
    stamp = utcnow()[:16].replace("T", " ")
    newest = _newest_observed(conn, incident_id)
    asof = f"data as of {newest[:16].replace('T', ' ')}Z - " if newest else ""
    badge_path = Path(__file__).resolve().parents[1] / "assets" / "ops-badge.png"
    tx = 40
    if badge_path.exists():
        from PIL import Image as _Image

        badge = _Image.open(badge_path).convert("RGBA")
        badge.thumbnail((46, 46), _Image.LANCZOS)
        img.paste(badge, (40, fy - 4), badge)
        tx = 40 + badge.width + 12
    d.text((tx, fy + 8), "TOAST REPORT", font=_font(22), fill=EMBER)
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
                (v.tier >= 3) ASC, v.observed_at DESC LIMIT 1) AS acreage,
             (SELECT v.evac_status FROM facts v WHERE v.incident_id = i.id AND v.kind='evac'
                AND v.id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
              ORDER BY (v.observed_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')) ASC,
                (v.tier >= 3) ASC, v.observed_at DESC LIMIT 1) AS evac
           FROM incidents i
           WHERE i.status = 'active' AND COALESCE(i.incident_type, 'WF') != 'RX'"""
    ).fetchall()
    fires = [dict(r) for r in rows]

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
           WHERE fire_ops = 1 AND unixepoch('now') - unixepoch(seen_at) < 3600"""
    ).fetchone()[0]
    redflag = 0
    fw = conn.execute("SELECT payload FROM map_cache WHERE key='firewx:ca'").fetchone()
    if fw:
        try:
            redflag = len(json.loads(fw["payload"]).get("features") or [])
        except ValueError:
            pass
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
    d.text((tx, fy + 8), "TOAST REPORT", font=_font(22), fill=EMBER)
    from toast.core.appsettings import brand_line

    brand = brand_line(conn)
    if brand:
        d.text((tx, fy + 34), brand[:70], font=_font(14), fill=DIM)
    d.text(
        (W - 40, fy + 11), f"ledger data - generated {utcnow()[:16].replace('T', ' ')}Z",
        font=_font(15), fill=DIM, anchor="ra",
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    get_logger("graphics").info("state_card_rendered", fires=len(fires), bytes=buf.tell())
    return buf.getvalue()
