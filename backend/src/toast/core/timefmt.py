"""Civilian time — how the PUBLIC reads a timestamp.

The operator workstation runs on military/UTC by choice; everything a civilian
sees (articles, roundups, sources sections, share cards, the public site) uses
local Pacific, 12-hour with AM/PM. A resident deciding whether to worry should
never have to convert "01:34Z" in their head.
"""

from __future__ import annotations


def _pacific_tz():
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo("America/Los_Angeles")
    except Exception:  # tz database missing — fall back to UTC below
        return None


def civilian(iso: str | None) -> str:
    """'2026-07-19T00:39:15Z' -> 'Jul 18, 5:39 PM PDT'. Falls back to a plain
    UTC rendering (still 12-hour) if the tz database is unavailable."""
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
