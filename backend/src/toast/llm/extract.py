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

from pydantic import BaseModel, Field, ValidationError

from toast.llm.client import LLMClient, LLMError
from toast.llm.prompts import load_prompt

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


class ExtractionResult(BaseModel):
    claims: list[ExtractedClaim]
    prompt_version: str
    dropped: int = 0  # claims that failed validation


def extract_claims(
    text: str,
    *,
    reference_time: str,
    source_name: str = "unknown",
    client: LLMClient | None = None,
) -> ExtractionResult:
    """Extract claims from untrusted text. reference_time anchors relative dates."""
    client = client or LLMClient()
    prompt = load_prompt("extract_claims")
    metadata = json.dumps({"reference_time_utc": reference_time, "source_name": source_name})
    # neutralize delimiter forgery: content may not fake the END marker to
    # smuggle text outside the untrusted block
    safe_text = text.replace("=====", "= = = =")
    rendered = prompt.render(metadata=metadata, content=safe_text)
    raw = client.chat_json([{"role": "system", "content": rendered}])

    if not isinstance(raw, dict) or not isinstance(raw.get("claims"), list):
        raise LLMError(f"extractor returned wrong shape: {str(raw)[:200]}")

    claims: list[ExtractedClaim] = []
    dropped = 0
    for item in raw["claims"]:
        try:
            claims.append(ExtractedClaim.model_validate(item))
        except ValidationError:
            dropped += 1
    return ExtractionResult(claims=claims, prompt_version=prompt.prompt_version, dropped=dropped)
