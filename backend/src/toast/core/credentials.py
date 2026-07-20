"""Credential store + service registry.

Keys can be entered from OPTIONS instead of hand-editing .env. A value stored
here overrides the matching environment variable at runtime.

Rules, in order of importance:
1. A stored value is NEVER returned to the client — status and a masked hint
   only. There is no read-back endpoint by design.
2. Only allowlisted names are accepted; the registry below is the allowlist.
3. Values are stripped from backup snapshots (toast/backup.py) — snapshots go
   offsite, secrets do not.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from toast.core.config import Settings
from toast.core.db import utcnow


@dataclass(frozen=True)
class Field:
    name: str  # env-style name, e.g. FIRMS_MAP_KEY
    label: str
    secret: bool = True  # False for public identifiers (handles, bucket names)
    placeholder: str = ""


@dataclass(frozen=True)
class Service:
    id: str
    label: str
    why: str  # what this unlocks, in the operator's terms
    signup_url: str
    signup_label: str
    fields: list[Field]
    testable: bool = True
    notes: str = ""
    optional_fields: list[Field] = field(default_factory=list)


# The registry doubles as the allowlist. Signup URLs match docs/API-KEYS.md.
SERVICES: list[Service] = [
    Service(
        id="firms",
        label="NASA FIRMS — satellite hotspots",
        why="Earliest automated signal of a new fire. Feeds the watch board.",
        signup_url="https://firms.modaps.eosdis.nasa.gov/api/map_key/",
        signup_label="Get a FIRMS map key (instant, by email)",
        fields=[Field("FIRMS_MAP_KEY", "Map key", placeholder="paste the MAP_KEY from the email")],
    ),
    Service(
        id="bluesky",
        label="Bluesky — social posting",
        why="Publishes approved SOCIAL drafts. Easiest channel: no app review.",
        signup_url="https://bsky.app/settings/app-passwords",
        signup_label="Create a Bluesky app password",
        fields=[
            Field("BLUESKY_HANDLE", "Handle", secret=False, placeholder="you.bsky.social"),
            Field("BLUESKY_APP_PASSWORD", "App password", placeholder="xxxx-xxxx-xxxx-xxxx"),
        ],
        notes="Use an APP PASSWORD, never your account password.",
    ),
    Service(
        id="backblaze",
        label="Backblaze B2 — offsite backup",
        why="The copy that survives the house. Until this is set, backups are local only.",
        signup_url="https://www.backblaze.com/sign-up/cloud-storage",
        signup_label="Create a B2 account + bucket (10 GB free)",
        fields=[
            Field("B2_KEY_ID", "Key ID"),
            Field("B2_APPLICATION_KEY", "Application key"),
            Field("B2_BUCKET", "Bucket name", secret=False, placeholder="toast-report-archive"),
        ],
        notes="Scope the application key to just this bucket, read+write.",
    ),
    # No web-host service here on purpose: the newsroom does not push to any
    # website. The operator publishes to their own site by hand until they
    # choose to wire one up.
    Service(
        id="x",
        label="X / Twitter — social posting",
        why="Posts the short version of approved SOCIAL drafts (~500/month free).",
        signup_url="https://developer.x.com/en/portal/dashboard",
        signup_label="Create an X app + user tokens",
        fields=[
            Field("X_API_KEY", "API key"),
            Field("X_API_SECRET", "API secret"),
            Field("X_ACCESS_TOKEN", "Access token"),
            Field("X_ACCESS_SECRET", "Access token secret"),
        ],
        notes="Needs user-context (OAuth 1.0a) tokens with write permission.",
    ),
    Service(
        id="facebook",
        label="Facebook Page — social posting",
        why="Biggest local audience. Requires Meta app review — start early.",
        signup_url="https://developers.facebook.com/apps/",
        signup_label="Create an app + Page access token",
        fields=[
            Field("FB_PAGE_ID", "Page ID", secret=False),
            Field("FB_PAGE_TOKEN", "Page access token"),
        ],
        notes="pages_manage_posts permission requires app review (weeks).",
    ),
    Service(
        id="airnow",
        label="AirNow — smoke / AQI",
        why="Air quality layer around incidents.",
        signup_url="https://docs.airnowapi.org/account/request/",
        signup_label="Request an AirNow API key",
        fields=[Field("AIRNOW_API_KEY", "API key")],
    ),
]

SERVICE_BY_ID = {s.id: s for s in SERVICES}
ALL_FIELDS: dict[str, Field] = {
    f.name: f for s in SERVICES for f in [*s.fields, *s.optional_fields]
}


def mask(value: str, *, secret: bool = True) -> str:
    """A hint that the value is right, revealing nothing useful."""
    if not value:
        return ""
    if not secret:
        return value  # handles, bucket names — public identifiers
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * min(12, len(value) - 4) + value[-4:]


def get_credential(conn: sqlite3.Connection, name: str) -> str:
    """Stored value, else the environment. Empty string when unset."""
    row = conn.execute("SELECT value FROM credentials WHERE name = ?", (name,)).fetchone()
    if row and row["value"]:
        return str(row["value"])
    return os.environ.get(name, "") or getattr(Settings(), name.lower(), "") or ""


def set_credential(conn: sqlite3.Connection, name: str, value: str) -> None:
    if name not in ALL_FIELDS:
        raise ValueError(f"unknown credential {name!r}")
    value = value.strip()
    if not value:  # clearing falls back to .env
        conn.execute("DELETE FROM credentials WHERE name = ?", (name,))
        return
    if len(value) > 500:
        raise ValueError("value too long")
    conn.execute(
        """INSERT INTO credentials (name, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at""",
        (name, value, utcnow()),
    )


def settings_with_credentials(conn: sqlite3.Connection) -> Settings:
    """Settings with stored credentials applied — what consumers should use."""
    base = Settings()
    overrides: dict[str, Any] = {}
    for name in ALL_FIELDS:
        attr = name.lower()
        if not hasattr(base, attr):
            continue
        value = get_credential(conn, name)
        if value:
            overrides[attr] = value
    return Settings(**overrides)


def service_status(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Everything the OPTIONS panel needs — and no secret values."""
    stored = {r["name"]: r["value"] for r in conn.execute("SELECT name, value FROM credentials")}
    checks = {
        r["service"]: dict(r)
        for r in conn.execute("SELECT service, status, message, checked_at FROM credential_checks")
    }
    out = []
    for s in SERVICES:
        fields = []
        for f in s.fields:
            env_value = os.environ.get(f.name, "") or getattr(Settings(), f.name.lower(), "") or ""
            value = stored.get(f.name) or env_value
            fields.append(
                {
                    "name": f.name,
                    "label": f.label,
                    "secret": f.secret,
                    "placeholder": f.placeholder,
                    "set": bool(value),
                    "masked": mask(value, secret=f.secret),
                    "source": "operator" if stored.get(f.name) else ("env" if env_value else None),
                }
            )
        configured = all(f["set"] for f in fields)
        check = checks.get(s.id)
        out.append(
            {
                "id": s.id,
                "label": s.label,
                "why": s.why,
                "signup_url": s.signup_url,
                "signup_label": s.signup_label,
                "notes": s.notes,
                "testable": s.testable,
                "configured": configured,
                "fields": fields,
                # A stale verdict must not outrank reality: with the fields
                # empty the service is simply not set up, whatever an earlier
                # test concluded about a key that no longer exists.
                "status": (
                    ((check or {}).get("status") or "unchecked") if configured else "unconfigured"
                ),
                "message": (check or {}).get("message") if configured else None,
                "checked_at": (check or {}).get("checked_at") if configured else None,
            }
        )
    return out


def record_check(
    conn: sqlite3.Connection, service: str, status: str, message: str | None
) -> dict[str, Any]:
    conn.execute(
        """INSERT INTO credential_checks (service, status, message, checked_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(service) DO UPDATE SET status = excluded.status,
                                              message = excluded.message,
                                              checked_at = excluded.checked_at""",
        (service, status, (message or "")[:300] or None, utcnow()),
    )
    return {"service": service, "status": status, "message": message, "checked_at": utcnow()}
