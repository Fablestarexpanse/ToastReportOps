"""Draft generation — turn an incident's ledger facts into a review-ready report.

The AI drafts; nothing publishes without operator approval (enforced by the
draft state-machine trigger). Evacuation facts are passed to the model with an
explicit verbatim contract and are also available to render untouched.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toast.core.db import utcnow
from toast.core.ids import ulid
from toast.llm.client import LLMClient, client_for
from toast.llm.prompts import load_prompt


def gather_facts(
    conn: sqlite3.Connection,
    incident_id: str,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
) -> list[sqlite3.Row]:
    """Non-superseded facts for an incident within an observed_at window,
    oldest first. Superseded facts are excluded — the ledger's current view."""
    clauses = [
        "incident_id = ?",
        "id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)",
    ]
    params: list[Any] = [incident_id]
    if window_start:
        clauses.append("observed_at >= ?")
        params.append(window_start)
    if window_end:
        clauses.append("observed_at <= ?")
        params.append(window_end)
    return conn.execute(
        f"SELECT * FROM facts WHERE {' AND '.join(clauses)} ORDER BY observed_at ASC",
        params,
    ).fetchall()


def _facts_block(facts: list[sqlite3.Row]) -> tuple[str, dict[str, str]]:
    """Render facts as a numbered block for the prompt; return (text, id_map)
    where id_map maps the short number → real fact id."""
    lines = []
    id_map: dict[str, str] = {}
    for i, f in enumerate(facts, start=1):
        id_map[str(i)] = f["id"]
        tag = f"[#{i}]"
        kind = f["kind"]
        verbatim = " (EVACUATION — reproduce verbatim)" if kind == "evac" else ""
        lines.append(
            f"{tag} kind={kind}{verbatim} tier=T{f['tier']} "
            f"observed={f['observed_at']}: {f['content']}"
        )
    return "\n".join(lines), id_map


def _sources_section(conn: sqlite3.Connection, facts: list[sqlite3.Row]) -> str:
    """A numbered Sources list (markdown) for the article, one entry per fact
    the draft was built from — numbered 1..N to match the inline [#n] citations.
    This is the permanent account: every report carries its own provenance."""
    src_names = {
        r["id"]: (r["name"], r["tier"]) for r in conn.execute("SELECT id, name, tier FROM sources")
    }
    # raw source urls, when a raw blob was preserved for the fact
    refs = [f["raw_ref"] for f in facts if f["raw_ref"]]
    urls: dict[str, str] = {}
    if refs:
        placeholders = ",".join("?" * len(refs))
        urls = {
            r["sha256"]: r["url"]
            for r in conn.execute(
                f"SELECT sha256, url FROM raw_blobs WHERE sha256 IN ({placeholders})", refs
            )
            if r["url"]
        }
    lines = ["", "---", "", "**Sources**", ""]
    for i, f in enumerate(facts, start=1):
        name, tier = src_names.get(f["source_id"], (f["source_id"], f["tier"]))
        observed = (f["observed_at"] or "")[:16].replace("T", " ")
        url = urls.get(f["raw_ref"] or "")
        link = f" — {url}" if url else ""
        lines.append(f"{i}. {name} (T{tier}) — {f['content']} — observed {observed} UTC{link}")
    # the operator's links ride at the very end of every article, so the
    # byline follows the report wherever it is pasted or rewritten
    from toast.core.appsettings import brand_line

    brand = brand_line(conn)
    if brand:
        lines += ["", f"*Follow Toast Report: {brand}*"]
    return "\n".join(lines)


def generate_draft(
    conn: sqlite3.Connection,
    incident_id: str,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    """Generate a draft update for an incident. Inserts a 'draft' row and
    returns a summary. Raises if the incident has no facts in range."""
    incident = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if incident is None:
        raise ValueError("incident not found")
    facts = gather_facts(conn, incident_id, window_start=window_start, window_end=window_end)
    if not facts:
        return {"created": False, "reason": "no facts in window"}

    facts_text, id_map = _facts_block(facts)
    prompt = load_prompt("draft_update", conn=conn)
    window_desc = f"{window_start or 'incident start'} to {window_end or 'now'}"
    rendered = prompt.render(
        incident=f"{incident['canonical_name']} ({incident['county'] or '?'} County, "
        f"status {incident['status']})",
        window=window_desc,
        facts=facts_text,
    )
    client = client or client_for(conn)
    raw = client.chat_json([{"role": "system", "content": rendered}])

    body_md = str(raw.get("body_md", "")).strip()
    title = str(raw.get("title") or f"{incident['canonical_name']} update").strip()
    # translate the model's short [#n] ids back to real fact ids
    cited_short = [str(c) for c in raw.get("cited_fact_ids", [])]
    cited_real = [id_map[c] for c in cited_short if c in id_map]

    # Always append the source account — every report carries its provenance,
    # listing every fact it was generated from (numbered to match [#n]).
    if body_md:
        body_md = body_md + "\n" + _sources_section(conn, facts)

    draft_id = ulid()
    now = utcnow()
    conn.execute(
        """INSERT INTO drafts (id, incident_id, kind, status, title, window_start, window_end,
                               body_md, cited_fact_ids, created_at, updated_at)
           VALUES (?, ?, 'update', 'draft', ?, ?, ?, ?, ?, ?, ?)""",
        (
            draft_id,
            incident_id,
            title,
            window_start,
            window_end,
            body_md,
            json.dumps(cited_real),
            now,
            now,
        ),
    )
    return {
        "created": True,
        "draft_id": draft_id,
        "title": title,
        "facts_used": len(facts),
        "cited": len(cited_real),
        "body_md": body_md,
    }


STATEWIDE_TITLE = "CALIFORNIA WILDFIRE ROUNDUP"


def _latest_fact_row(
    conn: sqlite3.Connection, incident_id: str, kind: str
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM facts
           WHERE incident_id = ? AND kind = ?
             AND id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
           ORDER BY (observed_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')) ASC,
             (tier >= 3) ASC, observed_at DESC LIMIT 1""",
        (incident_id, kind),
    ).fetchone()


def generate_statewide_roundup(conn: sqlite3.Connection) -> dict[str, Any]:
    """One draft covering every active fire — deterministic, not LLM-written.

    A roundup is a table of numbers; the ledger already holds them exactly,
    so composing it mechanically means zero invention risk and every figure
    cited. The SOCIAL VERSION button rewrites it in a human voice afterwards,
    through the same human-approval gate as everything else. Evacuation
    entries carry the official status WORD only — the full official text
    stays in the workstation, per doctrine.
    """
    incidents = conn.execute(
        """SELECT id, canonical_name, county FROM incidents
           WHERE status = 'active' AND COALESCE(incident_type, 'WF') != 'RX'"""
    ).fetchall()

    entries = []
    for inc in incidents:
        ac = _latest_fact_row(conn, inc["id"], "acreage")
        ct = _latest_fact_row(conn, inc["id"], "containment")
        ev = _latest_fact_row(conn, inc["id"], "evac")
        evac_status = ev["evac_status"] if ev else None
        entries.append(
            {
                "inc": inc,
                "acreage": ac["acreage"] if ac else None,
                "containment": ct["containment_pct"] if ct else None,
                "evac": evac_status
                if evac_status in ("order", "warning", "shelter_in_place")
                else None,
                "facts": [f for f in (ac, ct, ev) if f is not None],
            }
        )
    entries.sort(key=lambda e: (e["acreage"] or 0), reverse=True)

    cited: list[sqlite3.Row] = []
    index: dict[str, int] = {}

    def cite(entry) -> str:
        marks = []
        for f in entry["facts"]:
            if f["id"] not in index:
                cited.append(f)
                index[f["id"]] = len(cited)
            marks.append(f"[#{index[f['id']]}]")
        return "".join(marks)

    def line(entry) -> str:
        inc = entry["inc"]
        county = f" ({inc['county']} County)" if inc["county"] else ""
        ac = (
            f"{int(entry['acreage']):,} acres"
            if entry["acreage"] is not None
            else "size unconfirmed"
        )
        ct = f", {int(entry['containment'])}% contained" if entry["containment"] is not None else ""
        evac_label = {
            "order": " — **EVACUATION ORDER in effect**",
            "warning": " — **EVACUATION WARNING in effect**",
            "shelter_in_place": " — **SHELTER IN PLACE in effect**",
        }.get(entry["evac"] or "", "")
        text = f"- **{inc['canonical_name']}**{county} — {ac}{ct}{evac_label} {cite(entry)}"
        return text.rstrip()

    evac_entries = [e for e in entries if e["evac"]]
    sized = [e for e in entries if not e["evac"] and e["acreage"] is not None]
    unsized = [e for e in entries if not e["evac"] and e["acreage"] is None]

    total_acres = sum(int(e["acreage"]) for e in entries if e["acreage"] is not None)
    now = utcnow()
    datestr = now[:10]

    lines = [
        f"{len(entries)} active fires are burning across California, totalling "
        f"{total_acres:,} mapped acres. {len(evac_entries)} "
        f"{'fire has' if len(evac_entries) == 1 else 'fires have'} active evacuation "
        "instructions.",
        "",
    ]
    if evac_entries:
        lines += ["## Under evacuation", ""]
        lines += [line(e) for e in evac_entries]
        lines += ["", "Evacuation boundaries and instructions are issued by county "
                  "officials — always follow the official notice, not this summary.", ""]
    if sized:
        lines += ["## Largest active fires", ""]
        lines += [line(e) for e in sized[:12]]
        lines += [""]
    if unsized:
        names = ", ".join(e["inc"]["canonical_name"] for e in unsized)
        lines += [f"Also active (size not yet confirmed): {names}.", ""]

    body_md = chr(10).join(lines) + _sources_section(conn, cited)

    from toast.core.ids import ulid

    draft_id = ulid()
    title = f"{STATEWIDE_TITLE} — {datestr}"
    conn.execute(
        """INSERT INTO drafts (id, incident_id, kind, status, title, body_md,
                               cited_fact_ids, created_at, updated_at)
           VALUES (?, NULL, 'update', 'draft', ?, ?, ?, ?, ?)""",
        (draft_id, title, body_md, json.dumps([f["id"] for f in cited]), now, now),
    )
    conn.commit()
    return {
        "draft_id": draft_id,
        "title": title,
        "fires": len(entries),
        "under_evac": len(evac_entries),
        "cited": len(cited),
    }
