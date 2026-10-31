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

from toast.core.config import Settings
from toast.core.db import utcnow
from toast.core.fetch import FetchError, fetch
from toast.core.logging import get_logger
from toast.core.rawstore import store_raw

API = "https://api.planespotters.net/pub/photos/hex"
REFRESH_DAYS = 30  # a hull's best photo rarely changes; re-look monthly at most
IMG_TYPES = ("image/jpeg", "image/png", "image/webp")


def _cached(conn: sqlite3.Connection, hex_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT thumb_url, large_url, photographer, link, found, image_sha, fetched_at
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
        "image_sha": row["image_sha"],
        "cached": True,
    }


def _save_image(conn: sqlite3.Connection, url: str) -> str | None:
    """Download the photo into the CAS so it is ours, keyed by content hash.
    Returns the sha, or None if the image could not be fetched — a missing image
    is not a failure of the roster, just a picture we could not keep."""
    try:
        body, ctype = fetch(url, accept_types=IMG_TYPES, max_bytes=3 * 1024 * 1024)
        return store_raw(conn, Settings().toast_data_dir, body, ctype or "image/jpeg", url)
    except (FetchError, OSError) as exc:
        get_logger("aircraft_photo").info(
            "image_download_failed", url=url[:80], error=str(exc)[:80]
        )
        return None


def get_photo(conn: sqlite3.Connection, hex_id: str) -> dict[str, Any]:
    """Photo for one aircraft, by hex. Cached; fetched from planespotters on a
    miss. Always returns a dict with `found` — never raises to the caller."""
    hex_id = (hex_id or "").strip().upper()
    if not hex_id:
        return {"found": False, "hex": hex_id}
    hit = _cached(conn, hex_id)
    if hit is not None:
        return hit

    thumb = large = photographer = link = image_sha = None
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
            # save the picture to the hull's ID: download it into the CAS so it
            # is ours and survives planespotters removing it
            if image_url := large or thumb:
                image_sha = _save_image(conn, image_url)
    except (FetchError, json.JSONDecodeError, KeyError, TypeError) as exc:
        # a photo lookup must never break the roster — record the miss and move on
        get_logger("aircraft_photo").info("photo_lookup_failed", hex=hex_id, error=str(exc)[:100])

    conn.execute(
        """INSERT INTO aircraft_photos
               (hex, thumb_url, large_url, photographer, link, found, image_sha, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(hex) DO UPDATE SET
               thumb_url=excluded.thumb_url, large_url=excluded.large_url,
               photographer=excluded.photographer, link=excluded.link,
               found=excluded.found, image_sha=excluded.image_sha,
               fetched_at=excluded.fetched_at""",
        (hex_id, thumb, large, photographer, link, 1 if found else 0, image_sha, utcnow()),
    )
    return {
        "hex": hex_id,
        "found": found,
        "thumb_url": thumb,
        "large_url": large,
        "photographer": photographer,
        "link": link,
        "image_sha": image_sha,  # present = saved to this unit's ID
        "cached": False,
    }


def image_bytes(conn: sqlite3.Connection, hex_id: str) -> tuple[bytes, str] | None:
    """The saved photo bytes for one aircraft, read from the CAS. None if we have
    no image for it. This is the 'saved to the unit' picture — served from our
    own store, not hotlinked."""
    row = conn.execute(
        "SELECT image_sha FROM aircraft_photos WHERE hex = ?", ((hex_id or "").strip().upper(),)
    ).fetchone()
    if row is None or not row["image_sha"]:
        return None
    blob = conn.execute(
        "SELECT path, media_type FROM raw_blobs WHERE sha256 = ?", (row["image_sha"],)
    ).fetchone()
    if blob is None:
        return None
    from pathlib import Path

    fpath = Path(Settings().toast_data_dir) / blob["path"]
    if not fpath.exists():
        return None
    return fpath.read_bytes(), blob["media_type"] or "image/jpeg"
