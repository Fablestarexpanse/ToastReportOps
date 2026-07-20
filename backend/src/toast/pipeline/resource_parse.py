"""Pull equipment counts out of reported text.

WFIGS publishes total personnel and nothing else; the engine/crew/dozer/
aircraft breakdown lives in ICS-209 reports we do not have. But the counts are
often stated plainly in reporting — "23 engines and two helicopters are working
to knock the fire down" — so we read them from the text we already store.

Deterministic on purpose. Counting is exactly the task a language model will
quietly get wrong, and a fabricated engine count on a fire map is not a
cosmetic error. Everything here is regex over the stored sentence, and every
parsed number keeps the sentence it came from so the operator can see the
source of any figure at a glance.
"""

from __future__ import annotations

import re
from typing import Any

# Resource classes we count, with the words reporting actually uses.
RESOURCE_TYPES: dict[str, dict[str, Any]] = {
    "personnel": {
        "label": "PERSONNEL",
        "icon": "👤",
        "words": r"(?:personnel|firefighters?|responders?)",
    },
    "engines": {"label": "ENGINES", "icon": "🚒", "words": r"(?:engines?|fire\s+engines?)"},
    "crews": {
        "label": "CREWS",
        "icon": "🧑‍🚒",
        "words": r"(?:crews?|hand\s+crews?|hotshot\s+crews?)",
    },
    "dozers": {"label": "DOZERS", "icon": "🚜", "words": r"(?:dozers?|bulldozers?)"},
    "aircraft": {
        "label": "AIRCRAFT",
        "icon": "🚁",
        "words": r"(?:helicopters?|air\s+tankers?|tankers?|aircraft|airtankers?|planes?)",
    },
    "tenders": {"label": "TENDERS", "icon": "💧", "words": r"(?:water\s+tenders?|tenders?)"},
}

# Only read counts from sentences that describe deployment. Without this,
# "increasing engine staffing from two to three firefighters" — a policy
# sentence — would be recorded as three personnel on the fire.
DEPLOYMENT_CUES = re.compile(
    r"\b(assigned|working|deployed|committed|responding|responded|on\s+scene|battling|"
    r"fighting|attacking|dispatched|en\s?route|remain|staffed|operating|are\s+on)\b",
    re.IGNORECASE,
)

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
}
NUM = r"(\d{1,3}(?:,\d{3})*|\d+|" + "|".join(NUMBER_WORDS) + r")"


def _to_int(token: str) -> int | None:
    token = token.strip().lower().replace(",", "")
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token)


def parse_counts(text: str) -> dict[str, int]:
    """Resource counts stated in one sentence. Empty when it isn't about deployment."""
    if not text or not DEPLOYMENT_CUES.search(text):
        return {}
    found: dict[str, int] = {}
    for key, spec in RESOURCE_TYPES.items():
        # number, then at most one modifier word, then the resource noun —
        # tight adjacency keeps "from two to three firefighters" from matching
        # loosely across a clause
        pattern = rf"\b{NUM}\s+(?:\w+\s+)?{spec['words']}\b"
        for match in re.finditer(pattern, text, re.IGNORECASE):
            value = _to_int(match.group(1))
            if value is None or value <= 0 or value > 100_000:
                continue
            found[key] = max(found.get(key, 0), value)
    # "water tenders" also matches the looser tender pattern — keep the specific
    if (
        "tenders" in found
        and "aircraft" in found
        and re.search(r"water\s+tenders?", text, re.IGNORECASE)
        and not re.search(r"air\s?tankers?|helicopters?|aircraft|planes?", text, re.IGNORECASE)
    ):
        found.pop("aircraft", None)
    return found


def reported_resources(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts across reported resource facts, newest wins per type.

    `rows` are dicts with content, observed_at, source, tier — ordered newest
    first by the caller.
    """
    out: dict[str, Any] = {}
    for row in rows:
        for key, value in parse_counts(row.get("content", "")).items():
            if key in out:
                continue  # a newer fact already supplied this type
            out[key] = {
                "value": value,
                "observed_at": row.get("observed_at"),
                "source": row.get("source"),
                "tier": row.get("tier"),
                "quote": (row.get("content") or "")[:240],
            }
    return out
