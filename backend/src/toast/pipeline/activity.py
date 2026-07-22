"""Activity check: is every "active" fire actually still burning?

Fires only ever enter this system; nothing was asking whether they should
leave it, so dead fires sat "active" indefinitely. This sweep gathers
DETERMINISTIC evidence per fire — CAL FIRE's live list (the strongest signal:
officials saying active/inactive right now), latest containment, days since
any fact, satellite hotspots nearby, fire-ops aircraft overhead — and lets a
local LLM read the evidence plus the newest reporting to propose close / keep
/ uncertain with one sentence of reasoning.

The contract is the same as AI triage: propose, never act. Closing a fire is
the operator's click, through the ordinary status endpoint, reversible
(contained -> active exists because fires re-flare).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.fetch import FetchError, fetch
from toast.core.logging import get_logger
from toast.llm.client import LLMClient, LLMError
from toast.llm.prompts import load_prompt
from toast.pipeline.incidents import haversine_km

CALFIRE_LIST = "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=true"
HOTSPOT_KM = 8.0
AIRCRAFT_HOURS = 48


def _calfire_live(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """The official list, including inactive entries, keyed by UniqueId."""
    try:
        body, _ct = fetch(
            CALFIRE_LIST,
            accept_types=("application/json", "text/plain"),
            max_bytes=8 * 1024 * 1024,
        )
        items = json.loads(body)
    except (FetchError, ValueError):
        return {}
    out = {}
    for item in items if isinstance(items, list) else []:
        uid = str(item.get("UniqueId") or "").lower()
        if uid:
            out[uid] = {
                "is_active": bool(item.get("IsActive")),
                "containment": item.get("PercentContained"),
                "updated": item.get("Updated"),
            }
    return out


def gather_evidence(
    conn: sqlite3.Connection, inc: sqlite3.Row, calfire: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    days_quiet = conn.execute(
        """SELECT CAST((unixepoch('now') - unixepoch(MAX(observed_at))) / 86400.0 AS REAL)
           FROM facts WHERE incident_id = ?""",
        (inc["id"],),
    ).fetchone()[0]
    containment = conn.execute(
        """SELECT containment_pct FROM facts
           WHERE incident_id = ? AND kind = 'containment' AND containment_pct IS NOT NULL
             AND id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
           ORDER BY observed_at DESC LIMIT 1""",
        (inc["id"],),
    ).fetchone()

    official = None
    cfid = (inc["calfire_id"] or "").lower()
    if cfid and cfid in calfire:
        official = calfire[cfid]

    hotspots_near = 0
    if inc["lat"] is not None:
        row = conn.execute("SELECT payload FROM map_cache WHERE key = 'hotspots:ca'").fetchone()
        if row:
            try:
                for h in json.loads(row["payload"]).get("hotspots", []):
                    if haversine_km(inc["lat"], inc["lon"], h["lat"], h["lon"]) <= HOTSPOT_KM:
                        hotspots_near += 1
            except (ValueError, KeyError, TypeError):
                pass

    aircraft = conn.execute(
        """SELECT COUNT(DISTINCT hex) FROM aircraft_positions
           WHERE incident_id = ? AND fire_ops = 1
             AND unixepoch('now') - unixepoch(seen_at) < ? * 3600""",
        (inc["id"], AIRCRAFT_HOURS),
    ).fetchone()[0]

    return {
        "days_since_last_fact": round(days_quiet, 1) if days_quiet is not None else None,
        "containment_pct": containment["containment_pct"] if containment else None,
        "official_status": (
            "active" if official and official["is_active"]
            else "inactive" if official
            else "not on CAL FIRE list" if cfid
            else "never had a CAL FIRE id"
        ),
        "official_containment": official["containment"] if official else None,
        "hotspots_within_8km": hotspots_near,
        "fire_aircraft_last_48h": aircraft,
    }


def _rules_verdict(ev: dict[str, Any]) -> tuple[str, str]:
    """Transparent deterministic verdict — used alone when the LLM is down,
    and always stored beside the LLM's view."""
    quiet = ev["days_since_last_fact"] or 0
    cont = ev["official_containment"] or ev["containment_pct"] or 0
    signals_out = (
        ev["official_status"] == "inactive"
        or (cont >= 95 and quiet >= 2)
    )
    signals_alive = ev["hotspots_within_8km"] > 0 or ev["fire_aircraft_last_48h"] > 0
    if signals_out and not signals_alive:
        return "close", (
            f"officials list it {ev['official_status']}, "
            f"{int(cont)}% contained, quiet {quiet:.0f}d, no heat or aircraft"
        )
    if signals_alive:
        return "keep", (
            f"{ev['hotspots_within_8km']} hotspots / "
            f"{ev['fire_aircraft_last_48h']} fire aircraft still present"
        )
    return "uncertain", "signals are mixed — needs eyes"


def check_active(
    conn: sqlite3.Connection, *, client: LLMClient | None = None
) -> dict[str, Any]:
    incidents = conn.execute(
        "SELECT * FROM incidents WHERE status = 'active'"
    ).fetchall()
    calfire = _calfire_live(conn)
    llm = client
    prompt = None
    try:
        prompt = load_prompt("activity_check", conn=conn)
        llm = llm or LLMClient()
    except Exception:
        llm = None

    proposed = 0
    log = get_logger("activity")
    for inc in incidents:
        ev = gather_evidence(conn, inc, calfire)
        verdict, reasoning = _rules_verdict(ev)
        confidence = 0.7
        version = "rules-v1"
        # the LLM reviews only fires the rules would touch — nuance where it
        # matters, no 28 needless generations
        if llm is not None and prompt is not None and verdict != "keep":
            snippets = [
                r["content"][:160]
                for r in conn.execute(
                    """SELECT content FROM facts WHERE incident_id = ?
                       ORDER BY observed_at DESC LIMIT 4""",
                    (inc["id"],),
                )
            ]
            try:
                out = llm.chat_json([{"role": "system", "content": prompt.render(
                    name=inc["canonical_name"],
                    evidence=json.dumps(ev, indent=1),
                    recent=chr(10).join(snippets) or "(no reporting)",
                )}])
                v = str(out.get("verdict") or "")
                if v in ("close", "keep", "uncertain"):
                    verdict = v
                    reasoning = str(out.get("reasoning") or reasoning)[:300]
                    try:
                        confidence = max(0.0, min(1.0, float(out.get("confidence") or 0.5)))
                    except (TypeError, ValueError):
                        confidence = 0.5
                    version = prompt.version
            except LLMError:
                pass  # rules verdict stands, honestly versioned as rules-v1
        conn.execute(
            """INSERT OR REPLACE INTO activity_proposals
               (incident_id, verdict, evidence, reasoning, confidence,
                prompt_version, proposed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (inc["id"], verdict, json.dumps(ev), reasoning, confidence, version, utcnow()),
        )
        proposed += 1
    conn.commit()
    closable = conn.execute(
        "SELECT COUNT(*) FROM activity_proposals WHERE verdict = 'close'"
    ).fetchone()[0]
    log.info("activity_check_done", checked=proposed, closable=closable)
    return {"checked": proposed, "closable": closable}
