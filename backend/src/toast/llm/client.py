"""OpenAI-compatible LLM client (chat + embeddings).

Works against any /v1 endpoint (Ollama, LM Studio, vLLM, cloud later) —
selected purely by LLM_BASE_URL/LLM_MODEL env. The client never composes
prompts; callers pass complete messages (see prompts.py for the loader and
extract.py for the injection posture).
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from toast.core.config import Settings

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(Exception):
    pass


def _parse_json_lenient(content: str) -> Any:
    """Parse model JSON, tolerating reasoning-tag preambles and code fences."""
    cleaned = _THINK_RE.sub("", content).strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = re.sub(r"^json\s*", "", cleaned, flags=re.IGNORECASE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # last resort: first {...} span
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError(f"model returned non-JSON: {content[:300]}")


class LLMClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        model: str | None = None,
        embed_model: str | None = None,
    ) -> None:
        s = settings or Settings()
        self.base_url = s.llm_base_url.rstrip("/")
        self.model = model or s.llm_model
        self.embed_model = embed_model or s.embedding_model
        self.api_key = s.llm_api_key
        self.default_timeout_s = s.llm_timeout_s
        self.no_think_token = s.llm_no_think_token
        self.reasoning_on_judgment = s.llm_reasoning_on_judgment

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        timeout_s: float | None = None,
        no_think: bool = True,
    ) -> Any:
        """Chat completion that must return a JSON value (json_object mode).

        no_think appends the reasoning-skip soft switch to the first system
        message (harmless for models that don't recognize it)."""
        msgs = [dict(m) for m in messages]
        if no_think and self.no_think_token and msgs:
            msgs[0]["content"] = f"{self.no_think_token}\n{msgs[0]['content']}"
        # Every prompt in this pipeline is a single system message — the whole
        # task, including the injection-hardened content blocks, lives there.
        # Some chat templates REFUSE that shape: Qwen3.5's raises
        # "No user query found in messages." and the request 400s before the
        # model ever runs. A minimal user turn satisfies those templates without
        # touching the system prompt or the task it states.
        if not any(m.get("role") == "user" for m in msgs):
            msgs.append({"role": "user", "content": "Proceed."})
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": self.model,
                "messages": msgs,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout_s or self.default_timeout_s,
        )
        if resp.status_code != 200:
            raise LLMError(f"chat failed: {resp.status_code} {resp.text[:300]}")
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse_json_lenient(content)

    def judge_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        timeout_s: float | None = None,
    ) -> Any:
        """A JUDGMENT call: the model weighs conflicting evidence and decides.

        Same contract as chat_json, but reasoning is allowed through instead of
        suppressed — these are the calls where thinking earns its latency (is
        this a new fire or old news? is this one still burning?). Extraction and
        the writing stages keep using chat_json and stay fast.

        Reasoning output is safe here: _parse_json_lenient strips <think> blocks
        and falls back to the first {...} span, so a preamble never breaks the
        parse. Set llm_reasoning_on_judgment=false to force it off everywhere.
        """
        return self.chat_json(
            messages,
            temperature=temperature,
            timeout_s=timeout_s,
            no_think=not self.reasoning_on_judgment,
        )

    def embed(self, texts: list[str], *, timeout_s: float = 120.0) -> list[list[float]]:
        resp = httpx.post(
            f"{self.base_url}/embeddings",
            headers=self._headers(),
            json={"model": self.embed_model, "input": texts},
            timeout=timeout_s,
        )
        if resp.status_code != 200:
            raise LLMError(f"embed failed: {resp.status_code} {resp.text[:300]}")
        data = resp.json()["data"]
        return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]

    def list_models(self, *, timeout_s: float = 10.0) -> list[str]:
        """Models the local endpoint actually has pulled (OpenAI-compatible
        /models). Empty list when the endpoint is unreachable — the settings UI
        degrades to a free-text field rather than failing."""
        try:
            resp = httpx.get(f"{self.base_url}/models", headers=self._headers(), timeout=timeout_s)
            if resp.status_code != 200:
                return []
            return sorted(str(m["id"]) for m in resp.json().get("data", []))
        except httpx.HTTPError:
            return []


def client_for(conn: Any, settings: Settings | None = None) -> LLMClient:
    """LLMClient honoring the operator's model choices from app_settings."""
    from toast.core.appsettings import llm_overrides

    ov = llm_overrides(conn)
    return LLMClient(settings, model=ov.get("llm_model"), embed_model=ov.get("embedding_model"))
