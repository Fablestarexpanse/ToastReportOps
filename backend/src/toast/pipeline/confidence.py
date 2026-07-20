"""Confidence model v1: tier prior × extractor self-score, corroboration bump.

Deterministic official-source facts bypass this (written at 0.9-0.99 directly);
this scores LLM-extracted claims from lower-tier sources.
"""

from __future__ import annotations

TIER_PRIOR = {1: 0.98, 2: 0.9, 3: 0.75, 4: 0.65, 5: 0.45}
CORROBORATION_BUMP = 0.08  # per independent corroborating source
CAP = 0.99


def initial_confidence(tier: int, extractor_score: float) -> float:
    return round(min(CAP, TIER_PRIOR.get(tier, 0.5) * extractor_score), 3)


def corroborated_confidence(base: float, corroborating_sources: int) -> float:
    return round(min(CAP, base + CORROBORATION_BUMP * corroborating_sources), 3)
