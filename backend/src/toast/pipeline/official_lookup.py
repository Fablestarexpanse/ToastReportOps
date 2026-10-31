"""Official lists, used to verify New Starts candidates rather than to create them.

CAL FIRE's full list knows when a "new" fire closed weeks ago, and WFIGS holds small
fires that no list we ingest would ever show. Neither creates an incident here. A
scheduled job caches both so the board never waits on the network, and a list we
could not read yields 'unknown': absence is only claimed when every list was read.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from toast.core.db import utcnow
from toast.core.fetch import fetch
from toast.core.logging import get_logger
from toast.pipeline.activity import CALFIRE_LIST
from toast.pipeline.attribution import _norm_name
from toast.pipeline.counties import county_centroid
from toast.sources.wfigs import DEFAULT_URL, OUT_FIELDS, _epoch_ms_to_iso

CACHE_KEY = "official:lists"
LOOKBACK_DAYS = 45  # fire names repeat year to year; an older match is another fire
MAX_LIST_AGE_S = 3600  # refreshed every 10 min, so this old means the job has stopped
_LISTS = ("calfire", "wfigs")


def _recent(started: str | None, now: datetime) -> bool:
    if not started:
        return False
    try:
        when = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return now - when <= timedelta(days=LOOKBACK_DAYS)


def _fetch_json(url: str) -> Any:
    body, _ct = fetch(
        url, accept_types=("application/json", "text/plain"), max_bytes=16 * 1024 * 1024
    )
    return json.loads(body)


def _calfire_rows(now: datetime) -> list[dict[str, Any]]:
    items = _fetch_json(CALFIRE_LIST)
    if not isinstance(items, list):
        raise ValueError("CAL FIRE list was not a list")
    return [
        {
            "key": _norm_name(str(i.get("Name") or "")),
            "county": i.get("County"),
            "acres": i.get("AcresBurned"),
            "containment": i.get("PercentContained"),
            "started": i.get("Started"),
            "is_active": bool(i.get("IsActive")),
            "final": bool(i.get("Final")),
            "category": None,
        }
        for i in items
        if _recent(i.get("Started"), now)
    ]


def _wfigs_rows(now: datetime) -> list[dict[str, Any]]:
    since = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    params = {
        "where": f"POOState='US-CA' AND FireDiscoveryDateTime > TIMESTAMP '{since}'",
        "outFields": ",".join(OUT_FIELDS),
        "resultRecordCount": 2000,
        "f": "json",
    }
    features = _fetch_json(f"{DEFAULT_URL}?{urlencode(params)}").get("features")
    if features is None:  # ArcGIS reports its errors in a 200 response
        raise ValueError("WFIGS response had no features")
    rows = []
    for feature in features:
        a = feature.get("attributes") or {}
        rows.append(
            {
                "key": _norm_name(str(a.get("IncidentName") or "")),
                "county": a.get("POOCounty"),
                "acres": a.get("IncidentSize"),
                "containment": a.get("PercentContained"),
                "started": _epoch_ms_to_iso(a.get("FireDiscoveryDateTime")),
                "is_active": None,
                "final": None,
                "category": a.get("IncidentTypeCategory"),
            }
        )
    return rows


def _load(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The cached lists, unless the job has stopped refreshing them."""
    row = conn.execute(
        """SELECT payload FROM map_cache
           WHERE key = ? AND unixepoch('now') - unixepoch(fetched_at) < ?""",
        (CACHE_KEY, MAX_LIST_AGE_S),
    ).fetchone()
    return json.loads(row["payload"]) if row else None


def refresh(conn: sqlite3.Connection) -> dict[str, Any]:
    """Re-read both lists. A list that fails keeps its last good copy until it ages out."""
    now = datetime.now(UTC)
    row = conn.execute("SELECT payload FROM map_cache WHERE key = ?", (CACHE_KEY,)).fetchone()
    lists = json.loads(row["payload"]) if row else {}
    log = get_logger("official_lookup")
    summary: dict[str, Any] = {}
    for name, read in (("calfire", _calfire_rows), ("wfigs", _wfigs_rows)):
        try:
            lists[name] = {"rows": read(now), "at": utcnow()}
            summary[name] = len(lists[name]["rows"])
        except Exception as exc:  # one list down must not cost us the other
            log.warning("official_list_failed", list=name, error=str(exc)[:200])
            summary[name] = None
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (CACHE_KEY, json.dumps(lists), utcnow()),
    )
    log.info("official_lists_refreshed", **summary)
    return summary


def _readable(lists: dict[str, Any], name: str) -> bool:
    entry = lists.get(name)
    if not entry:
        return False
    at = datetime.fromisoformat(entry["at"].replace("Z", "+00:00"))
    return (datetime.now(UTC) - at).total_seconds() < MAX_LIST_AGE_S


def _same_county(hint: str | None, county: str | None) -> bool:
    """Only a real county name can rule a match out. A candidate's location is often
    something else, such as a national forest, which says nothing either way."""
    known = county_centroid(hint) if hint else None
    if not known or not county:
        return True
    return known[0].lower() in county.lower()


def _unmatched(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "source": None,
        "acres": None,
        "containment": None,
        "started": None,
        "final": None,
        "category": None,
    }


def check(conn: sqlite3.Connection, name: str, county: str | None) -> dict[str, Any]:
    """What the official lists say about a named fire. Reads the cache only."""
    lists = _load(conn)
    if lists is None:
        return _unmatched("unknown")
    key = _norm_name(name)
    readable = {n: _readable(lists, n) for n in _LISTS}
    for list_name, source in (("calfire", "CAL FIRE"), ("wfigs", "NIFC")):
        if not readable[list_name]:
            continue
        for row in lists[list_name]["rows"]:
            if row["key"] == key and _same_county(county, row["county"]):
                if list_name == "calfire":
                    status = "active" if row["is_active"] else "closed"
                else:
                    status = "reported"
                return {
                    "status": status,
                    "source": source,
                    "acres": row["acres"],
                    "containment": row["containment"],
                    "started": row["started"],
                    "final": row["final"],
                    "category": row["category"],
                }
    return _unmatched("none" if all(readable.values()) else "unknown")
