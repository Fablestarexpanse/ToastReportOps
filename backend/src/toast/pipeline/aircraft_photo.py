"""Aircraft photos from planespotters.net (free, keyless, attribution required).

An operator watching TANKER 914 orbit a fire wants to know what it looks like.
planespotters serves a photo by ICAO hex — the aircraft's assigned ID — so the
picture is saved against that hex and travels with the aircraft wherever it
appears next.

Two courtesies their terms require and we keep:
- the User-Agent carries our contact URL (core/fetch already sets it);
- the photographer credit and the photo's page link ride with every image and
  are shown — the photo is borrowed, and the credit is the rent.

A 'looked, none exists' result is cached too, so a helo with no photo on file is
not re-requested on every roster refresh.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.fetch import FetchError, fetch
from toast.core.logging import get_logger

API = "https://api.planespotters.net/pub/photos/hex"
REFRESH_DAYS = 30  # a hull's best photo rarely changes; re-look monthly at most


def _cached(conn: sqlite3.Connection, hex_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT thumb_url, large_url, photographer, link, found, fetched_at
           FROM aircraft_photos
           WHERE hex = ? AND unixepoch('now') - unixepoch(fetched_at) < ? * 86400""",
        (hex_id, REFRESH_DAYS),
    ).fetchone()
    if row is None:
        return None
    return {
        "hex": hex_id,
        "found": bool(row["found"]),
        "thumb_url": row["thumb_url"],
        "large_url": row["large_url"],
        "photographer": row["photographer"],
        "link": row["link"],
        "cached": True,
    }


def get_photo(conn: sqlite3.Connection, hex_id: str) -> dict[str, Any]:
    """Photo for one aircraft, by hex. Cached; fetched from planespotters on a
    miss. Always returns a dict with `found` — never raises to the caller."""
    hex_id = (hex_id or "").strip().upper()
    if not hex_id:
        return {"found": False, "hex": hex_id}
    hit = _cached(conn, hex_id)
    if hit is not None:
        return hit

    thumb = large = photographer = link = None
    found = False
    try:
        body, _ctype = fetch(
            f"{API}/{hex_id}", accept_types=("application/json",), max_bytes=512 * 1024
        )
        photos = json.loads(body).get("photos") or []
        if photos:
            p = photos[0]
            thumb = (p.get("thumbnail") or {}).get("src")
            large = (p.get("thumbnail_large") or {}).get("src")
            photographer = p.get("photographer")
            link = p.get("link")
            found = bool(thumb)
    except (FetchError, json.JSONDecodeError, KeyError, TypeError) as exc:
        # a photo lookup must never break the roster — record the miss and move on
        get_logger("aircraft_photo").info("photo_lookup_failed", hex=hex_id, error=str(exc)[:100])

    conn.execute(
        """INSERT INTO aircraft_photos
               (hex, thumb_url, large_url, photographer, link, found, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(hex) DO UPDATE SET
               thumb_url=excluded.thumb_url, large_url=excluded.large_url,
               photographer=excluded.photographer, link=excluded.link,
               found=excluded.found, fetched_at=excluded.fetched_at""",
        (hex_id, thumb, large, photographer, link, 1 if found else 0, utcnow()),
    )
    return {
        "hex": hex_id,
        "found": found,
        "thumb_url": thumb,
        "large_url": large,
        "photographer": photographer,
        "link": link,
        "cached": False,
    }
