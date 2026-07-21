"""Telegram publisher + ops hub.

What matters: the ops chat id is THE auth boundary (commands from any other
chat are ignored); approval from chat is the same state-machine transition as
the newsroom click; baselines mean a restart never re-alerts history; and an
unconfigured bot is a silent no-op, never an error.
"""

import json

import httpx

from toast.core.config import Settings
from toast.core.ids import ulid
from toast.pipeline import telegram_ops
from toast.publishers.telegram import TelegramPublisher

NOW = "2026-07-19T00:00:00.000Z"
SET = Settings(
    telegram_bot_token="123:abc", telegram_channel_id="@toastfire",
    telegram_ops_chat_id="777",
)


def _client(handler):
    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.telegram.org"
    )


class TestPublisher:
    def test_text_post_returns_a_receipt_url(self):
        def handler(request):
            assert "sendMessage" in str(request.url)
            body = json.loads(request.content)
            assert body["chat_id"] == "@toastfire"
            return httpx.Response(200, json={
                "result": {"message_id": 42, "chat": {"username": "toastfire"}}})

        p = TelegramPublisher(SET)
        p.http = _client(handler)
        out = p.publish("Fire update.")
        assert out["url"] == "https://t.me/toastfire/42"

    def test_image_failure_degrades_to_text(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            if "sendPhoto" in calls[-1]:
                return httpx.Response(400, text="photo bad")
            return httpx.Response(200, json={
                "result": {"message_id": 7, "chat": {"username": "toastfire"}}})

        p = TelegramPublisher(SET)
        p.http = _client(handler)
        out = p.publish("Fire update.", image=b"\x89PNG-bad")
        assert out["message_id"] == 7  # the words still went out
        assert any("sendPhoto" in c for c in calls) and any("sendMessage" in c for c in calls)


class TestNotify:
    def test_unconfigured_is_a_silent_noop(self, seeded):
        out = telegram_ops.notify_sweep(seeded, Settings())
        assert out == {"skipped": "telegram not configured"}

    def test_first_run_baselines_then_new_fire_alerts_once(self, seeded):
        sent = []

        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})

        out = telegram_ops.notify_sweep(seeded, SET, _client(handler))
        assert out["baselined"] is True and not sent  # history stays silent

        seeded.execute(
            "INSERT INTO incidents (id, canonical_name, status, county, created_at, updated_at)"
            " VALUES ('i9', 'Sawmill Fire', 'active', 'Butte', ?, ?)", (NOW, NOW),
        )
        telegram_ops.notify_sweep(seeded, SET, _client(handler))
        assert len(sent) == 1 and "Sawmill Fire" in sent[0]["text"]

        telegram_ops.notify_sweep(seeded, SET, _client(handler))
        assert len(sent) == 1  # never twice


class TestApprovalFromChat:
    def _draft(self, seeded):
        draft_id = ulid()
        seeded.execute(
            "INSERT INTO drafts (id, kind, body_md, status, created_at, updated_at)"
            " VALUES (?, 'social', 'x', 'draft', ?, ?)", (draft_id, NOW, NOW),
        )
        seeded.execute("UPDATE drafts SET status = 'needs_review' WHERE id = ?", (draft_id,))
        return draft_id

    def _updates(self, chat_id, data):
        def handler(request):
            if "getUpdates" in str(request.url):
                return httpx.Response(200, json={"result": [{
                    "update_id": 5,
                    "callback_query": {
                        "id": "cb1", "data": data,
                        "message": {"chat": {"id": chat_id}},
                    },
                }]})
            return httpx.Response(200, json={"ok": True})

        return _client(handler)

    def test_ops_chat_approval_moves_the_state_machine(self, seeded):
        draft_id = self._draft(seeded)
        out = telegram_ops.poll_updates(seeded, SET, self._updates(777, f"approve:{draft_id}"))
        assert out["handled"] == 1
        row = seeded.execute(
            "SELECT status, approved_by FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        assert row["status"] == "approved"
        assert "telegram" in row["approved_by"]  # the audit trail says where

    def test_a_foreign_chat_is_ignored_the_auth_boundary(self, seeded):
        draft_id = self._draft(seeded)
        out = telegram_ops.poll_updates(seeded, SET, self._updates(666, f"approve:{draft_id}"))
        assert out["handled"] == 0 and out["ignored"] == 1
        assert (
            seeded.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,)).fetchone()[0]
            == "needs_review"
        )

    def test_offset_advances_so_a_button_press_is_processed_once(self, seeded):
        draft_id = self._draft(seeded)
        telegram_ops.poll_updates(seeded, SET, self._updates(777, f"approve:{draft_id}"))
        state = telegram_ops._state(seeded, telegram_ops.OFFSET_KEY)
        assert state["offset"] == 6
