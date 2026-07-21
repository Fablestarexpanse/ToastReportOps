"""Telegram ops hub: alerts to the operator's phone, approvals from chat.

Two loops, both scheduler-driven and both silent when no bot is configured:

- notify_sweep: server-side twin of the UI alert bell — new active fires,
  EVACUATION CHANGES (the highest-priority event this system can notice), and
  drafts arriving for review, pushed to the PRIVATE ops chat. Baselines live
  in map_cache, so a restart never re-alerts history, and unlike the browser
  bell this works when the workstation is closed — which is the whole point.

- poll_updates: getUpdates long-poll for the inline APPROVE / SEND BACK
  buttons on draft notifications. The auth boundary is the chat id: a command
  from any chat other than the configured ops chat is ignored and logged.
  Approval here is the SAME human approval as the newsroom click — it moves
  needs_review -> approved through the ordinary state machine, and releasing
  to channels remains a separate deliberate act.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import httpx

from toast.core.config import Settings
from toast.core.db import utcnow
from toast.core.logging import get_logger

API = "https://api.telegram.org"
STATE_KEY = "telegram:notify:state"
OFFSET_KEY = "telegram:poll:offset"
ACTIVE_EVAC = ("order", "warning", "shelter_in_place")

EVAC_LABEL = {
    "order": "🚨 EVACUATION ORDER",
    "warning": "⚠️ EVACUATION WARNING",
    "shelter_in_place": "⚠️ SHELTER IN PLACE",
    "lifted": "✅ EVACUATION LIFTED",
}


def _cfg(settings: Settings | None = None) -> tuple[str, str] | None:
    s = settings or Settings()
    if s.telegram_bot_token and s.telegram_ops_chat_id:
        return s.telegram_bot_token, s.telegram_ops_chat_id
    return None


def _state(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT payload FROM map_cache WHERE key = ?", (key,)).fetchone()
    return json.loads(row["payload"]) if row else None


def _save(conn: sqlite3.Connection, key: str, state: dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO map_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
        (key, json.dumps(state), utcnow()),
    )
    conn.commit()


def _send(
    client: httpx.Client, token: str, chat: str, text: str,
    buttons: list[list[dict[str, str]]] | None = None,
) -> bool:
    payload: dict[str, Any] = {"chat_id": chat, "text": text, "parse_mode": "HTML"}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    r = client.post(f"{API}/bot{token}/sendMessage", json=payload)
    return r.status_code == 200


def notify_sweep(
    conn: sqlite3.Connection,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    cfg = _cfg(settings)
    if cfg is None:
        return {"skipped": "telegram not configured"}
    token, chat = cfg
    own = client is None
    client = client or httpx.Client(timeout=30)
    log = get_logger("telegram")

    state = _state(conn, STATE_KEY)
    first_run = state is None
    state = state or {"incidents": [], "evac": {}, "drafts": []}
    seen_inc = set(state["incidents"])
    seen_drafts = set(state["drafts"])
    sent = 0
    try:
        # new active fires
        for r in conn.execute(
            "SELECT id, canonical_name, county FROM incidents WHERE status = 'active'"
        ):
            if r["id"] in seen_inc:
                continue
            seen_inc.add(r["id"])
            if first_run:
                continue  # baseline silently, like the bell
            county = f" ({r['county']} Co.)" if r["county"] else ""
            if _send(client, token, chat, f"🔥 NEW FIRE: <b>{r['canonical_name']}</b>{county}"):
                sent += 1

        # evacuation transitions — persisted baseline, alerts even after restarts
        for r in conn.execute(
            """SELECT i.id, i.canonical_name,
                 (SELECT v.evac_status FROM facts v WHERE v.incident_id = i.id
                    AND v.kind = 'evac'
                    AND v.id NOT IN (SELECT supersedes FROM facts WHERE supersedes IS NOT NULL)
                  ORDER BY v.observed_at DESC LIMIT 1) AS evac
               FROM incidents i WHERE i.status = 'active'"""
        ):
            cur = r["evac"] if r["evac"] in (*ACTIVE_EVAC, "lifted") else "none"
            prev = state["evac"].get(r["id"], "none")
            state["evac"][r["id"]] = cur
            if first_run or cur == prev:
                continue
            if cur in ACTIVE_EVAC or (cur == "lifted" and prev in ACTIVE_EVAC):
                text = (
                    f"{EVAC_LABEL[cur]}: <b>{r['canonical_name']}</b>\n"
                    "Official text is in the workstation — verify before repeating."
                )
                if _send(client, token, chat, text):
                    sent += 1

        # drafts arriving for review, with approval buttons
        for r in conn.execute(
            "SELECT id, title FROM drafts WHERE status = 'needs_review'"
        ):
            if r["id"] in seen_drafts:
                continue
            seen_drafts.add(r["id"])
            if first_run:
                continue
            buttons = [[
                {"text": "✓ APPROVE", "callback_data": f"approve:{r['id']}"},
                {"text": "✗ SEND BACK", "callback_data": f"reject:{r['id']}"},
            ]]
            if _send(
                client, token, chat,
                f"📝 DRAFT FOR REVIEW: <b>{r['title'] or r['id'][-6:]}</b>\n"
                "Approving here is the same human approval as the newsroom click; "
                "releasing to channels stays a separate step.",
                buttons,
            ):
                sent += 1
    finally:
        state["incidents"] = sorted(seen_inc)
        state["drafts"] = sorted(seen_drafts)
        _save(conn, STATE_KEY, state)
        if own:
            client.close()
    if sent:
        log.info("telegram_notified", messages=sent)
    return {"sent": sent, "baselined": first_run}


def poll_updates(
    conn: sqlite3.Connection,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    cfg = _cfg(settings)
    if cfg is None:
        return {"skipped": "telegram not configured"}
    token, ops_chat = cfg
    own = client is None
    client = client or httpx.Client(timeout=35)
    log = get_logger("telegram")

    offset = (_state(conn, OFFSET_KEY) or {}).get("offset", 0)
    handled = ignored = 0
    try:
        r = client.post(
            f"{API}/bot{token}/getUpdates",
            json={"offset": offset, "timeout": 0, "allowed_updates": ["callback_query"]},
        )
        if r.status_code != 200:
            return {"error": f"getUpdates HTTP {r.status_code}"}
        for upd in r.json().get("result", []):
            offset = max(offset, upd.get("update_id", 0) + 1)
            cb = upd.get("callback_query")
            if not cb:
                continue
            from_chat = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
            data = cb.get("data") or ""
            # THE auth boundary: only the configured ops chat may command
            if from_chat != str(ops_chat):
                ignored += 1
                log.warning("telegram_command_ignored", chat=from_chat, data=data[:40])
                continue
            action, _, draft_id = data.partition(":")
            if action in ("approve", "reject") and draft_id:
                target = "approved" if action == "approve" else "draft"
                ok = _transition(conn, draft_id, target)
                _ack(client, token, cb["id"], "done ✓" if ok else "could not apply")
                if ok:
                    handled += 1
    finally:
        _save(conn, OFFSET_KEY, {"offset": offset})
        if own:
            client.close()
    if handled or ignored:
        log.info("telegram_commands", handled=handled, ignored=ignored)
    return {"handled": handled, "ignored": ignored}


def _transition(conn: sqlite3.Connection, draft_id: str, target: str) -> bool:
    row = conn.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None or row["status"] != "needs_review":
        return False  # already handled in the newsroom, or gone
    now = utcnow()
    try:
        if target == "approved":
            conn.execute(
                """UPDATE drafts SET status = 'approved', approved_by = 'brian (telegram)',
                   approved_at = ?, updated_at = ? WHERE id = ?""",
                (now, now, draft_id),
            )
        else:
            conn.execute(
                "UPDATE drafts SET status = 'draft', updated_at = ? WHERE id = ?",
                (now, draft_id),
            )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def _ack(client: httpx.Client, token: str, callback_id: str, text: str) -> None:
    client.post(
        f"{API}/bot{token}/answerCallbackQuery",
        json={"callback_query_id": callback_id, "text": text},
    )
