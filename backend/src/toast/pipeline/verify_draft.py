"""Deterministic check of an AI-written draft against the facts it was written from.

The draft prompt *asks* the model to cite every sentence, invent nothing and
reproduce evacuation text verbatim. A request is not a guarantee, and the person
approving at 2 AM is the last line of defence — so this module turns those rules
into checks the code makes:

* every ``[#n]`` citation points at a fact the model was actually given;
* every evacuation fact in the window appears in the body verbatim (an order
  that was paraphrased *or left out* is the failure that hurts people);
* every number in the prose appears in the facts (no invented or "rounded"
  figures);
* no sentence carries a figure without a citation.

Number *words* ("three structures") are not checked, and a clean result is not a
proof the draft is right — it means none of these mechanical failures occurred.
The human review stays; this stops the obvious mistakes reaching it unflagged.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from toast.core.timefmt import civilian

SOURCES_MARK = "\n---\n\n**Sources**"

_CITE_GROUP = re.compile(r"\[#[\d#,\s]+\]")
_CITE_ID = re.compile(r"#(\d+)")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_TIME = re.compile(r"\b\d{1,2}(?::\d{2})?\s*[AaPp]\.?[Mm]\b\.?")
_MONTH_DAY = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)\b"
)
_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "—": "-", "–": "-"})


def _norm_num(token: str) -> str:
    t = token.replace(",", "").rstrip(".")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t


def _norm_text(text: str) -> str:
    text = text.translate(_QUOTES)
    text = re.sub(r"[*_>`]", "", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _numbers(text: str) -> set[str]:
    return {_norm_num(m) for m in _NUMBER.findall(text)}


def _allowed_numbers(facts: list[sqlite3.Row], window: tuple[str | None, ...]) -> set[str]:
    allowed: set[str] = set()
    for f in facts:
        allowed |= _numbers(str(f["content"] or ""))
        for col in ("acreage", "containment_pct"):
            if f[col] is not None:
                allowed.add(_norm_num(f"{f[col]:g}"))
        # times the model renders for the public derive from observed_at
        allowed |= _numbers(civilian(f["observed_at"]))
    for w in window:
        if w:
            allowed |= _numbers(civilian(w))
    return allowed


def verify_draft(
    body_md: str,
    facts: list[sqlite3.Row],
    id_map: dict[str, str],
    *,
    window: tuple[str | None, ...] = (),
) -> list[dict[str, Any]]:
    """Issues found in ``body_md`` (the body *before* the Sources list is
    appended). Empty list = no mechanical problem found."""
    body = body_md.split(SOURCES_MARK, 1)[0]
    issues: list[dict[str, Any]] = []

    # 1. citations must reference facts the model was given
    for group in _CITE_GROUP.findall(body):
        for n in _CITE_ID.findall(group):
            if n not in id_map:
                issues.append(
                    {"code": "unknown_citation", "detail": f"cites [#{n}], which is not a fact"}
                )

    # 2. evacuation text: present and verbatim
    norm_body = _norm_text(_CITE_GROUP.sub("", body))
    for i, f in enumerate(facts, start=1):
        if f["kind"] == "evac" and _norm_text(str(f["content"])) not in norm_body:
            issues.append(
                {
                    "code": "evac_not_verbatim",
                    "detail": f"evacuation fact [#{i}] is missing or paraphrased: "
                    f"{str(f['content'])[:120]}",
                }
            )

    # 3. every figure in the prose exists in the facts
    prose = _CITE_GROUP.sub("", body)
    prose = _TIME.sub(" ", prose)
    prose = _MONTH_DAY.sub(" ", prose)
    allowed = _allowed_numbers(facts, window)
    for token in sorted(_numbers(prose) - allowed):
        issues.append(
            {"code": "number_not_in_facts", "detail": f"{token} does not appear in any fact"}
        )

    # 4. a sentence carrying a figure must carry a citation
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body):
        s = sentence.strip()
        if not s or s.startswith("#"):
            continue
        has_figure = bool(_numbers(_MONTH_DAY.sub(" ", _TIME.sub(" ", _CITE_GROUP.sub("", s)))))
        if has_figure and not _CITE_GROUP.search(s):
            issues.append({"code": "uncited_figure", "detail": s[:140]})

    return issues


def summarize(issues: list[dict[str, Any]]) -> str:
    """One line for a phone notification."""
    if not issues:
        return ""
    kinds = sorted({i["code"] for i in issues})
    return f"{len(issues)} issue(s): " + ", ".join(kinds)
