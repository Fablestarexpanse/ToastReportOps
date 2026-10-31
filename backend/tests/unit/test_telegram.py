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
    telegram_bot_token="123:abc",
    telegram_channel_id="@toastfire",
    telegram_ops_chat_id="777",
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")


class TestPublisher:
    def test_text_post_returns_a_receipt_url(self):
        def handler(request):
            assert "sendMessage" in str(request.url)
            body = json.loads(request.content)
            assert body["chat_id"] == "@toastfire"
            return httpx.Response(
                200, json={"result": {"message_id": 42, "chat": {"username": "toastfire"}}}
            )

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
            return httpx.Response(
                200, json={"result": {"message_id": 7, "chat": {"username": "toastfire"}}}
            )

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
            " VALUES ('i9', 'Sawmill Fire', 'active', 'Butte', ?, ?)",
            (NOW, NOW),
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
            " VALUES (?, 'social', 'x', 'draft', ?, ?)",
            (draft_id, NOW, NOW),
        )
        seeded.execute("UPDATE drafts SET status = 'needs_review' WHERE id = ?", (draft_id,))
        return draft_id

    def _updates(self, chat_id, data, user_id=None, acks=None):
        def handler(request):
            if "getUpdates" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "update_id": 5,
                                "callback_query": {
                                    "id": "cb1",
                                    "data": data,
                                    "message": {"chat": {"id": chat_id}},
                                    **({"from": {"id": user_id}} if user_id else {}),
                                },
                            }
                        ]
                    },
                )
            if acks is not None and "answerCallbackQuery" in str(request.url):
                acks.append(json.loads(request.content)["text"])
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

    def test_a_flagged_draft_cannot_be_approved_from_chat(self, seeded):
        draft_id = self._draft(seeded)
        seeded.execute(
            "UPDATE drafts SET verify_issues = ? WHERE id = ?",
            ('[{"code": "number_not_in_facts", "detail": "9999"}]', draft_id),
        )
        telegram_ops.poll_updates(seeded, SET, self._updates(777, f"approve:{draft_id}"))
        row = seeded.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        assert row["status"] == "needs_review"

    def test_only_listed_approvers_can_press_approve(self, seeded):
        listed = Settings(**{**SET.model_dump(), "telegram_ops_user_ids": "42, 43"})
        draft_id = self._draft(seeded)
        acks: list[str] = []
        out = telegram_ops.poll_updates(
            seeded, listed, self._updates(777, f"approve:{draft_id}", user_id=99, acks=acks)
        )
        assert out == {"handled": 0, "ignored": 1} and acks == ["not authorised to approve"]
        status = seeded.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        assert status["status"] == "needs_review"
        seeded.execute("DELETE FROM map_cache")  # reset the update offset
        out = telegram_ops.poll_updates(
            seeded, listed, self._updates(777, f"approve:{draft_id}", user_id=42)
        )
        assert out["handled"] == 1

    def test_without_a_list_anyone_in_the_ops_chat_may_approve(self, seeded):
        draft_id = self._draft(seeded)
        out = telegram_ops.poll_updates(
            seeded, SET, self._updates(777, f"approve:{draft_id}", user_id=99)
        )
        assert out["handled"] == 1

    def test_a_bot_configured_in_options_is_used_not_just_env(self, seeded, monkeypatch):
        """OPTIONS stores credentials in the database; alerts must not read .env only."""
        from toast.core.credentials import set_credential

        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "TELEGRAM_OPS_CHAT_ID"):
            monkeypatch.delenv(name, raising=False)
        set_credential(seeded, "TELEGRAM_BOT_TOKEN", "123:abc")
        set_credential(seeded, "TELEGRAM_OPS_CHAT_ID", "777")
        assert telegram_ops._cfg(conn=seeded) == ("123:abc", "777")
        assert telegram_ops._cfg() is None  # env alone has nothing: the old behaviour

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
