# Corroboration — tuning notes (audit R6, 2026-07-20)

Corroboration is the system's signature stat: "N independent sources say this."
It feeds `effective_confidence`, which feeds what gets drafted and published.
That makes a false corroboration worse than a missing one — it manufactures
confidence rather than merely failing to find it.

These thresholds were measured against the live ledger (282 embedded facts),
not guessed.

## What was wrong

Corroboration required only: different source, cosine distance ≤ 0.35, and
observed within 12h. Nothing established that the two claims were **about the
same thing**.

Measured on real data:

| cross-source pairs | count | median distance |
|---|---|---|
| same incident | 103 | 0.584 |
| different incident | 141 | 0.713 |
| same incident + same kind | 60 | 0.540 |
| same incident + different kind | 43 | 0.620 |

The false matches were the dangerous part. Every cross-fire "corroboration" in
the ledger sat at **distance 0.0** — textually identical wire boilerplate:

> "A new wildfire was reported in Los Angeles County on July 20"

Different sources, *different fires*, perfect embedding match. Counted as
independent confirmation. No threshold fixes this: the text really is identical,
so tightening the distance makes it *more* likely to qualify, not less.

## What changed

1. **Same-subject guard.** Both attributed → incident ids must match. Either
   unattributed (watch-board case) → the claimed fire *name* must match.
   Generic text naming no fire corroborates nothing.
2. **Same-kind guard.** An acreage claim does not confirm an evacuation claim.
3. **Threshold 0.35 → 0.45.** Safe once the guards exist:

   | threshold | same-kind matches | cross-kind matches |
   |---|---|---|
   | 0.35 | 14 | 0 |
   | **0.45** | **22** | **0** |
   | 0.55 | 32 | 14 |
   | 0.65 | 48 | 26 |

   0.45 is the knee — more recall, still no cross-kind contamination.
4. **k 8 → 16.** 5% of facts had all 8 nearest neighbours from their own
   source, so a chatty feed's repeats could crowd out a genuine corroborator.

## Result on the live ledger

| | before | after |
|---|---|---|
| facts with corroboration | 21 | 13 |
| same-incident pairs | 7 | 16 |
| **cross-fire false pairs** | **1+** | **0** |

The headline count went *down* while correct pairs more than doubled. That is
the intended direction: the previous number was partly boilerplate agreeing
with itself.

## Re-running after a rule change

`fact_assessments` is derived and rebuildable; prior assessments are stale
whenever these rules change. Rebuild with:

```
curl -X POST localhost:8400/facts/reassess
```

It reuses stored embeddings (no GPU cost) and never touches `facts` — the
ledger is immutable; only the assessment layer is recomputed.

## Known limits

- Two sources copying the same wire story about the *same* fire count as two
  corroborations. Real independence would need syndication detection; the
  trust loop (`docs`/`pipeline/trust.py`) partly compensates by measuring
  whether a source is ever *first*.
- The 12h window is unmeasured — it has never rejected a pair that the other
  guards accepted, so there is no evidence to tune it on yet.
