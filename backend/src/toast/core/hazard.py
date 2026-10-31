"""Which hazard season the operator is tracking: fire, flood, or both.

One setting (`hazard_profile` in app_settings) steers what gets polled and shown,
so the autumn changeover from fire to flood season is a switch, not a code edit.
The profile only *gates* flood work — the fire pipeline is untouched by it.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from toast.core.appsettings import get_setting

PROFILES = ("fire", "flood", "both")
DEFAULT_PROFILE = "fire"


def hazard_profile(conn: sqlite3.Connection) -> str:
    value = (get_setting(conn, "hazard_profile") or DEFAULT_PROFILE).strip().lower()
    return value if value in PROFILES else DEFAULT_PROFILE


def includes_flood(conn: sqlite3.Connection) -> bool:
    return hazard_profile(conn) in ("flood", "both")


def includes_fire(conn: sqlite3.Connection) -> bool:
    return hazard_profile(conn) in ("fire", "both")


def season_suggestion(today: date | None = None) -> str:
    """What the calendar suggests for Northern California. A hint only — the
    operator decides; a late-October offshore-wind event or an early
    atmospheric river doesn't care what month it is."""
    month = (today or date.today()).month
    if month in (6, 7, 8, 9):
        return "fire"
    if month in (12, 1, 2, 3):
        return "flood"
    return "both"  # Oct–Nov and Apr–May: the seasons overlap
