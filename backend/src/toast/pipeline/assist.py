"""Editor AI assists — style tools, never fact tools.

The newsroom's writing aids: tighten, polish, simplify, translate, and headline
suggestions. Each takes the operator's text (a selection or the whole draft),
runs ONE whitelisted instruction through the versioned prompt, and returns a
SUGGESTION — nothing is saved here. The operator applies it in the editor,
saves, and still walks the same review/approve gates as every draft. Facts and
evacuation lines are contractually untouchable in the prompt; the human gate is
what makes even a bad model edit harmless.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from toast.llm.client import LLMClient, client_for
from toast.llm.prompts import load_prompt

MAX_TEXT_CHARS = 40_000  # a draft, not a book

# The full instruction set lives HERE, server-side — the API takes an op name,
# never free-form instructions, so the model's marching orders can't be
# injected from the client.
OPS: dict[str, str] = {
    "tighten": (
        "Tighten the text: cut filler, redundancy, and throat-clearing. Keep "
        "every fact and every sentence's meaning; aim for roughly two-thirds "
        "the length. Do not drop the Sources section or citations."
    ),
    "polish": (
        "Polish the text: fix grammar, spelling, punctuation, and awkward "
        "phrasing. AP-style news register — plain, active, calm. Change as "
        "little as possible."
    ),
    "simplify": (
        "Rewrite the text in plain language for the general public: short "
        "sentences, common words, no jargon or agency shorthand (expand "
        "acronyms once). A worried resident skimming on a phone should get it "
        "on the first read."
    ),
    "spanish": (
        "Translate the text into clear, natural Latin American Spanish for a "
        "California audience. Keep the markdown structure and the Sources "
        "section. Remember the evacuation rule: translate evacuation sentences "
        "but keep zone ids and road names exactly as written, appending the "
        "original English sentence in parentheses."
    ),
}


def assist_text(
    conn: sqlite3.Connection,
    op: str,
    text: str,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    """Run one whitelisted edit over the operator's text. Returns a suggestion,
    persists nothing."""
    text = (text or "").strip()
    if not text:
        raise ValueError("nothing to edit — the text is empty")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("text too long for an assist pass")
    client = client or client_for(conn)

    # a full article through a local 14B takes real time — scale the deadline
    # with the text instead of dying at the default 120 s on long drafts
    timeout_s = max(180.0, min(600.0, len(text) / 10))

    if op == "headlines":
        prompt = load_prompt("assist_headlines", conn=conn)
        raw = client.chat_json(
            [{"role": "system", "content": prompt.render(text=text)}],
            temperature=0.5,
            timeout_s=timeout_s,
        )
        heads = [str(h).strip() for h in (raw.get("headlines") or []) if str(h).strip()]
        if not heads:
            raise ValueError("model returned no headlines")
        return {"op": op, "headlines": heads[:5], "prompt_version": prompt.prompt_version}

    if op not in OPS:
        raise ValueError(f"unknown assist op: {op}")
    prompt = load_prompt("assist_edit", conn=conn)
    raw = client.chat_json(
        [{"role": "system", "content": prompt.render(instruction=OPS[op], text=text)}],
        temperature=0.3,
        timeout_s=timeout_s,
    )
    out = str(raw.get("text") or "").strip()
    if not out:
        raise ValueError("model returned no text")
    return {
        "op": op,
        "text": out,
        "chars": len(out),
        "prompt_version": prompt.prompt_version,
    }
