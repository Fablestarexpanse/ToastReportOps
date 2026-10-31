"""Claim extraction from untrusted text.

Injection posture (docs/SECURITY.md):
1. Untrusted content goes ONLY inside the delimited block of the versioned
   prompt; system role carries the security contract.
2. Output must validate against ExtractedClaim (typed fields, enums, ranges) —
   anything else is discarded, the model cannot invent fields or actions.
3. This module has no tools and no side effects; it returns claims, callers
   decide what to write.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from toast.core.timestamps import normalize_iso
from toast.llm.client import LLMClient, LLMError
from toast.llm.prompts import load_prompt
from toast.llm.verify_extraction import verify_claim

ClaimKind = Literal[
    "start",
    "acreage",
    "containment",
    "evac",
    "road",
    "resource",
    "damage",
    "cause",
    "weather",
    "other",
]


class ExtractedClaim(BaseModel):
    content: str = Field(min_length=3, max_length=500)
    kind: ClaimKind
    incident_name: str | None = Field(default=None, max_length=120)
    location_text: str | None = Field(default=None, max_length=300)
    acreage: float | None = Field(default=None, ge=0, le=5_000_000)
    containment_pct: float | None = Field(default=None, ge=0, le=100)
    evac_status: Literal["order", "warning", "shelter_in_place", "lifted"] | None = None
    observed_at: str | None = None
    confidence: float = Field(ge=0, le=1)
    # set by toast.llm.verify_extraction, never by the model: what the source
    # check found wrong with this claim (an empty list = nothing found)
    issues: list[str] = Field(default_factory=list)

    @field_validator("observed_at")
    @classmethod
    def _usable_timestamp(cls, v: str | None) -> str | None:
        """A model-written date that SQLite cannot parse poisons every time
        window it lands in, so it is dropped here and the caller falls back to
        ingest time — recorded, not hidden."""
        return normalize_iso(v)


class ExtractionResult(BaseModel):
    claims: list[ExtractedClaim]
    prompt_version: str
    dropped: int = 0  # claims that failed validation
    unsupported: int = 0  # claims dropped for stating a figure the source doesn't
    california_relevant: bool = True  # v2 gate; v1 has no field → treated as CA
    primary_location: str | None = None


def extract_claims(
    text: str,
    *,
    reference_time: str,
    source_name: str = "unknown",
    version: str = "v2",
    client: LLMClient | None = None,
    verify: bool = True,
) -> ExtractionResult:
    """Extract claims from untrusted text. reference_time anchors relative dates.

    v2 first decides California-relevance; a non-CA article returns
    california_relevant=False and (per the prompt) an empty claims list."""
    client = client or LLMClient()
    prompt = load_prompt("extract_claims", version)
    metadata = json.dumps({"reference_time_utc": reference_time, "source_name": source_name})
    # neutralize delimiter forgery: content may not fake the END marker to
    # smuggle text outside the untrusted block
    safe_text = text.replace("=====", "= = = =")
    rendered = prompt.render(metadata=metadata, content=safe_text)
    raw = client.chat_json([{"role": "system", "content": rendered}])

    if not isinstance(raw, dict) or not isinstance(raw.get("claims"), list):
        raise LLMError(f"extractor returned wrong shape: {str(raw)[:200]}")

    # default True when the key is absent (v1 compatibility)
    california_relevant = bool(raw.get("california_relevant", True))
    primary_location = raw.get("primary_location")
    if not isinstance(primary_location, str):
        primary_location = None

    claims: list[ExtractedClaim] = []
    dropped = unsupported = 0
    for item in raw["claims"]:
        try:
            claim = ExtractedClaim.model_validate(item)
        except ValidationError:
            dropped += 1
            continue
        claim.issues = []  # never trust a model-supplied value for this field
        if verify:
            kept, _issues = verify_claim(claim, text)
            if kept is None:
                unsupported += 1
                continue
            claim = kept
        claims.append(claim)
    return ExtractionResult(
        claims=claims,
        prompt_version=prompt.prompt_version,
        dropped=dropped,
        unsupported=unsupported,
        california_relevant=california_relevant,
        primary_location=primary_location,
    )
