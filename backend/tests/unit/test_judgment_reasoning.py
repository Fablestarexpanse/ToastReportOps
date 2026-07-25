"""Reasoning is suppressed on the hot path and allowed on judgment calls.

The pipeline appends /no_think to every prompt — a choice made for EXTRACTION
("pull the acreage out of this sentence"), where reasoning is pure latency on
the highest-volume stage in the system. It was then inherited by everything,
including the calls that actually weigh conflicting evidence: is this a new fire
or old news, is this one still burning, does this claim belong to that incident.

judge_json is the exception, and these tests pin both halves of it.
"""

from typing import Any

import pytest

from toast.core.config import Settings
from toast.llm import client as client_mod
from toast.llm.client import LLMClient


class _Resp:
    status_code = 200

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": '{"verdict": "new"}'}}]}


@pytest.fixture
def captured(monkeypatch):
    """Capture the outgoing request body instead of calling a model."""
    sent: dict[str, Any] = {}

    def fake_post(url, **kw):
        sent.update(kw.get("json") or {})
        return _Resp()

    monkeypatch.setattr(client_mod.httpx, "post", fake_post)
    return sent


def _prompt(sent) -> str:
    return sent["messages"][0]["content"]


def test_extraction_still_suppresses_reasoning(captured):
    LLMClient(Settings()).chat_json([{"role": "system", "content": "extract acreage"}])
    assert _prompt(captured).startswith("/no_think")


def test_judgment_lets_the_model_think(captured):
    LLMClient(Settings()).judge_json([{"role": "system", "content": "new fire or old news?"}])
    # no soft switch — this is the call where thinking earns its latency
    assert "/no_think" not in _prompt(captured)
    assert _prompt(captured).startswith("new fire or old news?")


def test_judgment_reasoning_can_be_forced_off(captured):
    """The escape hatch: a model that ignores /no_think, or judgment latency
    backing the job queue up, and the operator wants it off everywhere."""
    s = Settings()
    s.llm_reasoning_on_judgment = False
    LLMClient(s).judge_json([{"role": "system", "content": "still burning?"}])
    assert _prompt(captured).startswith("/no_think")


def test_judgment_keeps_the_json_contract(captured):
    LLMClient(Settings()).judge_json([{"role": "system", "content": "x"}])
    # reasoning changes the prompt, never the response contract
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["temperature"] == 0.1


def test_reasoning_preamble_does_not_break_the_parse(monkeypatch):
    """A thinking model emits <think>…</think> before its JSON. The lenient
    parser must survive that, or enabling reasoning would break every verdict."""

    class Thinking(_Resp):
        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "<think>Reported 3 days ago, but the "
                            "acreage grew today, so it is active.</think>\n"
                            '{"verdict": "new", "confidence": 0.8}'
                        }
                    }
                ]
            }

    monkeypatch.setattr(client_mod.httpx, "post", lambda url, **kw: Thinking())
    out = LLMClient(Settings()).judge_json([{"role": "system", "content": "x"}])
    assert out == {"verdict": "new", "confidence": 0.8}
