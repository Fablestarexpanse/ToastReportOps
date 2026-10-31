"""Check an extracted claim against the source text it was extracted from.

The extractor's output is schema-validated (types, enums, ranges) but that says
nothing about whether it is *true to the source*. A model can emit a perfectly
well-formed claim with an acreage that appears nowhere in the article, a fire
name it made up, or a place it normalised into a different one — and the ledger
is append-only, so a bad fact stays. These checks are plain string and number
matching in code, so they cost nothing and cannot be talked out of it:

* every number in the claim (its text, acreage, containment) must occur in the
  source, digits or spelled out ("two thousand", "1.2 thousand");
* a named fire must occur in the source — an invented name drives attribution;
* a location must occur in the source — an invented place gets geocoded;
* an evacuation claim is checked for verbatim quoting.

Policy (`verify_claim`): an invented NUMBER drops the claim (an unsupported figure
is the one thing the ledger must never hold); an invented NAME or PLACE is cleared
from the claim and the rest kept; a non-verbatim evacuation claim is kept and
flagged so the operator sees it is a paraphrase. Dates and times are not checked
here — observed_at is normalised separately and relative dates legitimately
resolve against the reference time.

Not covered: a claim can quote real numbers and still be wrong about what they
mean. This catches fabrication, not misreading.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from toast.llm.extract import ExtractedClaim

_DIGITS = re.compile(r"\d[\d,]*(?:\.\d+)?")
_TIME = re.compile(r"\b\d{1,2}(?::\d{2})?\s*[AaPp]\.?[Mm]\b|\b\d{1,2}:\d{2}\b")
_DATE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)\b|\b(?:19|20)\d{2}\b"
)
_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "—": "-", "–": "-"})

_SMALL = {
    w: i
    for i, w in enumerate(
        (
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        )  # fmt: skip
    )
}
_TENS = {
    w: 10 * (i + 2)
    for i, w in enumerate(
        ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
    )
}
_SCALE = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}
_SUFFIX = {"thousand": 1_000, "million": 1_000_000, "k": 1_000, "m": 1_000_000}
_ZERO_PHRASES = re.compile(
    r"\b(?:uncontained|not contained|no containment|zero|0\s*%|0 percent)\b", re.IGNORECASE
)


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_>`]", "", text.translate(_QUOTES))).strip().casefold()


def _spelled_numbers(text: str) -> set[float]:
    """Numbers written as words: 'two thousand five hundred' -> 2500, 'twenty-five' -> 25."""
    out: set[float] = set()
    tokens = re.findall(r"[a-z]+", text.lower().replace("-", " "))
    total = current = 0
    active = False
    for tok in [*tokens, ""]:
        if tok in _SMALL:
            current += _SMALL[tok]
            active = True
        elif tok in _TENS:
            current += _TENS[tok]
            active = True
        elif tok == "hundred" and active:
            current = max(current, 1) * 100
        elif tok in ("thousand", "million") and active:
            total += max(current, 1) * _SCALE[tok]
            current = 0
        elif tok == "a" or tok == "and":
            continue
        else:
            if active:
                out.add(float(total + current))
            total = current = 0
            active = False
    return out


def numbers_in(text: str) -> set[float]:
    """Every quantity the text states, in digit or word form, plus 'k'/'thousand' scalings."""
    out = _spelled_numbers(text)
    for m in _DIGITS.finditer(text):
        try:
            value = float(m.group().replace(",", "").rstrip("."))
        except ValueError:
            continue
        out.add(value)
        suffix = re.match(r"\s*([a-z]+)", text[m.end() : m.end() + 12].lower())
        if suffix and suffix.group(1) in _SUFFIX:
            out.add(value * _SUFFIX[suffix.group(1)])
    return out


def _claim_numbers(claim: ExtractedClaim) -> list[float]:
    prose = _DATE.sub(" ", _TIME.sub(" ", claim.content))
    values = [float(m.group().replace(",", "").rstrip(".")) for m in _DIGITS.finditer(prose)]
    values += [v for v in (claim.acreage, claim.containment_pct) if v is not None]
    return values


def _name_core(name: str) -> str:
    name = re.sub(r"^the\s+", "", name.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+(fire|incident|complex)$", "", name, flags=re.IGNORECASE).strip()


def _contains_word(haystack_norm: str, needle: str) -> bool:
    needle_norm = _norm_text(needle)
    return bool(needle_norm) and bool(
        re.search(rf"(?<!\w){re.escape(needle_norm)}(?!\w)", haystack_norm)
    )


def verify_claim(
    claim: ExtractedClaim, source_text: str
) -> tuple[ExtractedClaim | None, list[str]]:
    """(claim to keep or None if unsupported, issue codes). See module docstring."""
    issues: list[str] = []
    source_norm = _norm_text(source_text)
    stated = numbers_in(source_text)

    for value in _claim_numbers(claim):
        if value in stated:
            continue
        if value == 0 and claim.kind == "containment" and _ZERO_PHRASES.search(source_text):
            continue
        issues.append("number_not_in_source")
        return None, issues

    update: dict[str, object] = {}
    if claim.incident_name and not (
        _contains_word(source_norm, claim.incident_name)
        or _contains_word(source_norm, _name_core(claim.incident_name))
    ):
        issues.append("name_not_in_source")
        update["incident_name"] = None
    if claim.location_text:
        head = claim.location_text.split(",")[0]
        if not _contains_word(source_norm, head):
            issues.append("location_not_in_source")
            update["location_text"] = None
    if claim.kind == "evac" and _norm_text(claim.content) not in source_norm:
        issues.append("evac_not_verbatim")

    update["issues"] = issues
    return claim.model_copy(update=update), issues
