"""One place that decides what a timestamp is.

Everything bi-temporal in this system — supersession ordering, staleness, the
attribution time score, the watch board's recency window, lead-time trust
scoring — is arithmetic on `observed_at` and `ingested_at`. SQLite does that
arithmetic with `unixepoch()`, which returns NULL for anything it cannot parse,
and NULL propagates silently: `abs(None)` raises, comparisons quietly fail, and
a fact vanishes from a window it should be in.

An LLM writes `observed_at`. It will eventually emit `"July 19, 2026"` or
`"yesterday evening"` or an empty string. So text becomes a timestamp in exactly
one function, and anything that will not parse is refused rather than guessed at
— an invented hour on a fire report is worse than an honest fallback to when we
received it.
"""

from __future__ import annotations

from datetime import UTC, datetime

ISO_Z = "%Y-%m-%dT%H:%M:%S.%f"


def normalize_iso(value: str | None) -> str | None:
    """Return `value` as ISO-8601 UTC (`...THH:MM:SS.sssZ`), or None if it is
    not an unambiguous machine timestamp.

    Accepts what real feeds emit: trailing `Z`, `+00:00`/`-07:00` offsets, a
    space instead of `T`, and naive stamps (read as UTC, which is what every
    producer in this system means). Deliberately does NOT accept prose: we do
    not turn "yesterday evening" into an hour we made up.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)  # naive means UTC here, by convention
    return parsed.astimezone(UTC).strftime(ISO_Z)[:-3] + "Z"


def require_iso(value: str, *, field: str) -> str:
    """Normalize or raise. Used at the ledger boundary, where a fact with an
    uninterpretable timestamp must never be written in the first place."""
    normalized = normalize_iso(value)
    if normalized is None:
        raise ValueError(f"{field} is not a usable timestamp: {value!r}")
    return normalized
