"""Attribution replay evaluation.

Phase 2's acceptance criterion — "≥90% of facts auto-attribute correctly" — was
never actually measured. This measures it, repeatably, offline.

The gold set is facts whose text names exactly ONE known incident, so the
correct answer is certain from the text alone without hand-labelling. That is
an honest but *easy* subset: it says nothing about facts that name no fire, and
the numbers should be read as an upper bound, not a grade for the whole ledger.

Two figures matter, and they fail differently:

- PRECISION: of the facts it attributed, how many went to the right fire?
  A wrong attribution corrupts an incident's ledger and can end up in a
  published report. This should be ~100%; anything less is a defect.
- RECALL: of the facts it could have attributed, how many did it?
  A miss costs the operator manual work and leaves the fact out of drafts.
  Lower is tolerable, but a fall means something regressed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from toast.core.db import utcnow
from toast.core.ids import ulid
from toast.pipeline.attribution import AUTO_THRESHOLD, score_candidates

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "attribution_gold.json"


@dataclass
class EvalResult:
    total: int = 0
    attributed: int = 0
    correct: int = 0
    wrong: int = 0
    missed: int = 0
    # Facts the SCORER never sees in production: official records carry the
    # incident's id, so they are attributed by reference. Counting them as
    # scorer misses would understate it badly.
    by_reference: int = 0
    # A fact whose text names the fire but whose extraction produced no name:
    # an EXTRACTOR defect, not a scorer one. Tracked separately so a
    # regression in either is visible on its own.
    extractor_miss: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def precision(self) -> float | None:
        return round(self.correct / self.attributed, 3) if self.attributed else None

    @property
    def recall(self) -> float | None:
        return round(self.correct / self.total, 3) if self.total else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "scored": {
                "total": self.total,
                "attributed": self.attributed,
                "correct": self.correct,
                "wrong": self.wrong,
                "missed": self.missed,
                "precision": self.precision,
                "recall": self.recall,
            },
            "by_reference": self.by_reference,
            "extractor_miss": self.extractor_miss,
            "errors": self.errors[:20],
        }


def load_fixture(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or FIXTURE).read_text(encoding="utf-8"))


def build_world(conn: sqlite3.Connection, fixture: dict[str, Any]) -> dict[str, str]:
    """Recreate the fixture's incidents so the scorer has the same choices."""
    name_to_id: dict[str, str] = {}
    now = utcnow()
    for inc in fixture["incidents"]:
        iid = ulid()
        conn.execute(
            """INSERT INTO incidents (id, canonical_name, status, lat, lon, county,
                                      created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                iid,
                inc["canonical_name"],
                inc.get("status") or "active",
                inc.get("lat"),
                inc.get("lon"),
                inc.get("county"),
                now,
                inc.get("updated_at") or now,
            ),
        )
        name_to_id[inc["canonical_name"]] = iid
    return name_to_id


def run_eval(conn: sqlite3.Connection, fixture: dict[str, Any] | None = None) -> EvalResult:
    """Score every gold fact against the live attribution logic."""
    fixture = fixture or load_fixture()
    name_to_id = build_world(conn, fixture)
    result = EvalResult()

    for item in fixture["facts"]:
        truth_id = name_to_id.get(item["truth_incident"])
        if truth_id is None:
            continue

        # Route each fact the way production would.
        if not item.get("extracted_name"):
            if item.get("tier", 5) <= 2:
                result.by_reference += 1  # official record: attributed by id
            else:
                result.extractor_miss += 1
                result.errors.append(
                    {
                        "type": "EXTRACTOR MISS",
                        "truth": item["truth_incident"],
                        "content": item["content"][:120],
                    }
                )
            continue

        result.total += 1
        candidates = score_candidates(
            conn,
            observed_at=item["observed_at"],
            incident_name=item.get("extracted_name"),
            lat=item.get("lat"),
            lon=item.get("lon"),
        )
        if not candidates:
            result.missed += 1
            continue
        best = candidates[0]
        runner_up = candidates[1].score if len(candidates) > 1 else 0.0
        # the same rule attribute_fact applies in production
        auto = best.score >= AUTO_THRESHOLD and best.score - runner_up >= 0.15
        if not auto:
            result.missed += 1
            result.errors.append(
                {
                    "type": "missed",
                    "truth": item["truth_incident"],
                    "best": best.incident_name,
                    "score": round(best.score, 3),
                    "gap": round(best.score - runner_up, 3),
                    "content": item["content"][:120],
                }
            )
            continue
        result.attributed += 1
        if best.incident_id == truth_id:
            result.correct += 1
        else:
            result.wrong += 1
            result.errors.append(
                {
                    "type": "WRONG",
                    "truth": item["truth_incident"],
                    "got": best.incident_name,
                    "score": round(best.score, 3),
                    "content": item["content"][:120],
                }
            )
    return result


def main() -> None:  # pragma: no cover - operator convenience
    import tempfile

    from toast.core.db import apply_migrations, connect

    # ignore_cleanup_errors: Windows keeps a handle on the sqlite file briefly
    # after close, and a tidy-up failure must not fail the evaluation
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        conn = connect(Path(tmp) / "eval.db")
        try:
            apply_migrations(conn)
            result = run_eval(conn)
        finally:
            conn.close()
    print(json.dumps(result.as_dict(), indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
