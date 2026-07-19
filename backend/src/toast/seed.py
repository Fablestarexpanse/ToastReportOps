"""Seed the official Phase 1 sources (idempotent).

    python -m toast.seed

FIRMS is created disabled until FIRMS_MAP_KEY is set in .env — enable it in
the SOURCES UI (or flip sources.enabled) once the free key is registered.
"""

from __future__ import annotations

import json
import os
from typing import Any

from toast.core.config import Settings
from toast.core.db import apply_migrations, connect, utcnow
from toast.core.ids import ulid

SEEDS: list[dict[str, Any]] = [
    {
        "name": "WFIGS / IRWIN incidents",
        "type": "api",
        "adapter": "wfigs",
        "tier": 1,
        "config": {"state": "US-CA"},
        "schedule": "120",  # seconds
        "enabled": 1,
        "tos_notes": "Public NIFC open-data service; attribution preserved. Reviewed 2026-07-19.",
    },
    {
        "name": "NWS CAP alerts (CA)",
        "type": "api",
        "adapter": "nws_cap",
        "tier": 1,
        "config": {},
        "schedule": "300",
        "enabled": 1,
        "tos_notes": "api.weather.gov public API. Reviewed 2026-07-19.",
    },
    {
        "name": "NASA FIRMS hotspots (VIIRS, CA)",
        "type": "satellite",
        "adapter": "firms",
        "tier": 1,
        "config": {"map_key_env": "FIRMS_MAP_KEY"},
        "schedule": "600",
        "enabled": 0,  # enable once FIRMS_MAP_KEY is registered
        "tos_notes": "Free map key required; rate limits apply. Reviewed 2026-07-19.",
    },
]


def seed(conn) -> list[str]:
    created = []
    for spec in SEEDS:
        exists = conn.execute(
            "SELECT id, enabled FROM sources WHERE adapter = ? AND name = ?",
            (spec["adapter"], spec["name"]),
        ).fetchone()
        if exists:
            continue
        enabled = spec["enabled"]
        if spec["adapter"] == "firms" and os.environ.get("FIRMS_MAP_KEY"):
            enabled = 1
        now = utcnow()
        conn.execute(
            """INSERT INTO sources (id, name, type, adapter, tier, config, schedule, enabled,
                                    tos_notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ulid(),
                spec["name"],
                spec["type"],
                spec["adapter"],
                spec["tier"],
                json.dumps(spec["config"]),
                spec["schedule"],
                enabled,
                spec["tos_notes"],
                now,
                now,
            ),
        )
        created.append(spec["name"])
    return created


def main() -> None:
    settings = Settings()
    conn = connect(settings.toast_db_path)
    apply_migrations(conn)
    created = seed(conn)
    print(f"seeded: {created or 'nothing (all present)'}")


if __name__ == "__main__":
    main()
