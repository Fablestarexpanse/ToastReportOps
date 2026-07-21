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
            ORDER BY observed_at DESC LIMIT 1""",
        (incident_id, kind),
    ).fetchone()
    return row["v"] if row else None


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
    y = 34

    # header
    d.text((40, y), inc["canonical_name"].upper(), font=_font(54), fill=TXT)
    y += 74
    sub = " · ".join(
        s for s in [f"{inc['county']} County" if inc["county"] else None, "California"] if s
    )
    d.text((40, y), sub, font=_font(22), fill=DIM)
    y += 44

    # evacuation banner — the official status word, verbatim, never paraphrased
    if evac_active:
        d.rectangle([(40, y), (W - 40, y + 56)], outline=EVAC, width=2)
        label = {
            "order": "EVACUATION ORDER IN EFFECT",
            "warning": "EVACUATION WARNING IN EFFECT",
            "shelter_in_place": "SHELTER IN PLACE IN EFFECT",
        }[evac]
        d.text((58, y + 12), f"⚠ {label} — SEE OFFICIAL NOTICE", font=_font(26), fill=EVAC)
        y += 74

    # stat tiles
    tiles = [
        ("ACRES", f"{int(acreage):,}" if acreage is not None else "—", EMBER),
        ("CONTAINED", f"{int(containment)}%" if containment is not None else "—", OK),
        ("STATUS", (inc["status"] or "").upper() or "—", DIM),
    ]
    tw = (W - 80 - 2 * 16) // 3
    for i, (label, value, color) in enumerate(tiles):
        x0 = 40 + i * (tw + 16)
        d.rectangle([(x0, y), (x0 + tw, y + 92)], fill=PANEL, outline=LINE)
        d.text((x0 + 18, y + 12), label, font=_font(17), fill=DIM)
        d.text((x0 + 18, y + 36), value, font=_font(40), fill=color)
    y += 108

    # map strip
    map_top = y
    if inc["lat"] is not None and inc["lon"] is not None:
        zoom = 11
        strip = _stitch_map(inc["lat"], inc["lon"], zoom=zoom, w=W - 80, h=MAP_H)
        img.paste(strip, (40, map_top))
        d2 = ImageDraw.Draw(img)
        _draw_perimeter(
            conn, d2, incident_id, inc["lat"], inc["lon"],
            zoom=zoom, w=W - 80, h=MAP_H, off_y=map_top,
        )
        cxp, cyp = 40 + (W - 80) // 2, map_top + MAP_H // 2
        for r, col in ((14, EVAC if evac_active else EMBER), (7, TXT)):
            d2.ellipse([(cxp - r, cyp - r), (cxp + r, cyp + r)], outline=col, width=3)
        d2.rectangle([(40, map_top), (W - 40, map_top + MAP_H)], outline=LINE)
        d2.text(
            (W - 46, map_top + MAP_H - 24), "© OpenStreetMap · © CARTO",
            font=_font(15), fill=DIM, anchor="ra",
        )
    else:
        d.rectangle([(40, map_top), (W - 40, map_top + MAP_H)], fill=PANEL, outline=LINE)
        d.text((60, map_top + 20), "location not yet confirmed", font=_font(22), fill=DIM)

    # footer: when this was true, badged
    fy = map_top + MAP_H + 18
    stamp = utcnow()[:16].replace("T", " ")
    badge_path = Path(__file__).resolve().parents[1] / "assets" / "ops-badge.png"
    tx = 40
    if badge_path.exists():
        from PIL import Image as _Image

        badge = _Image.open(badge_path).convert("RGBA")
        badge.thumbnail((80, 80), _Image.LANCZOS)
        img.paste(badge, (40, fy - 36), badge)
        tx = 40 + badge.width + 12
    d.text((tx, fy), "TOAST REPORT", font=_font(22), fill=EMBER)
    d.text(
        (W - 40, fy + 3), f"ledger data · generated {stamp}Z", font=_font(17), fill=DIM, anchor="ra"
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
        "SELECT incident_id, status FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()
    if draft is None:
        raise ValueError("draft not found")
    if draft["incident_id"] is None:
        raise ValueError("draft has no incident — nothing to draw")
    png = generate_card(conn, draft["incident_id"])
    sha = store_raw(conn, Path(Settings().toast_data_dir), png, "image/png", url=None)
    conn.execute("UPDATE drafts SET graphic_sha = ? WHERE id = ?", (sha, draft_id))
    conn.commit()
    return {"draft_id": draft_id, "graphic_sha": sha, "bytes": len(png)}
