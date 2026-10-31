"""Runtime settings the operator can change from the UI.

Environment (.env → Settings) supplies defaults; a row in app_settings
overrides it at runtime. Only keys in ALLOWED are settable — an operator can
switch models or display preferences, never inject arbitrary config.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from toast.core.config import Settings
from toast.core.db import utcnow

# key -> (Settings attribute for the default, human label, kind)
ALLOWED: dict[str, tuple[str | None, str, str]] = {
    "llm_model": ("llm_model", "Writing / extraction model", "model"),
    "embedding_model": ("embedding_model", "Embedding model", "model"),
    "timezone_display": (None, "Time zone", "choice"),  # 'utc' | 'local'
    # 24-hour is the default because a fire log is read against dispatch and
    # agency traffic, which is 24-hour. Kept as a preference, not a rule.
    "clock_format": (None, "Clock format", "choice"),  # '24h' | '12h'
    # 'offline' draws the bundled outline only — no tile server, works in a
    # PSPS outage. The others fetch tiles and need network.
    "map_basemap": (None, "Map basemap", "choice"),  # 'offline' | 'osm' | 'dark'
    "timeline_order": (None, "Timeline order", "choice"),  # 'newest' | 'oldest'
    "ui_poll_interval_s": (None, "UI refresh interval (s)", "number"),
    # branding: stamped on generated graphics and appended to articles/posts —
    # where a shared card travels, the byline travels with it
    "brand_web": (None, "Website link (on graphics + posts)", "text"),
    "brand_social": (None, "Social handles (on graphics + posts)", "text"),
}

UI_DEFAULTS = {
    "timezone_display": "utc",
    "clock_format": "24h",
    "map_basemap": "dark",
    "timeline_order": "newest",
    "ui_poll_interval_s": "10",
    "brand_web": "",
    "brand_social": "",
}


def brand_line(conn: sqlite3.Connection) -> str | None:
    """The operator's links, one line, or None when nothing is configured."""
    values = [get_setting(conn, k) for k in ("brand_web", "brand_social")]
    parts = [v.strip() for v in values if v and v.strip()]
    return " - ".join(parts) if parts else None


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def effective_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every allowed key with its effective value and where it came from."""
    env = Settings()
    stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_settings")}
    out: dict[str, Any] = {}
    for key, (attr, label, kind) in ALLOWED.items():
        default = getattr(env, attr) if attr else UI_DEFAULTS.get(key, "")
        out[key] = {
            "value": stored.get(key, default),
            "default": default,
            "overridden": key in stored,
            "label": label,
            "kind": kind,
        }
    return out


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    if key not in ALLOWED:
        raise ValueError(f"unknown setting {key!r}")
    value = str(value).strip()
    if not value:  # empty = revert to the environment default
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        return
    if len(value) > 200:
        raise ValueError("value too long")
    conn.execute(
        """INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                          updated_at = excluded.updated_at""",
        (key, value, utcnow()),
    )


def llm_overrides(conn: sqlite3.Connection) -> dict[str, str]:
    """Model overrides for LLMClient construction (empty when unset)."""
    out = {}
    for key in ("llm_model", "embedding_model"):
        val = get_setting(conn, key)
        if val:
            out[key] = val
    return out
