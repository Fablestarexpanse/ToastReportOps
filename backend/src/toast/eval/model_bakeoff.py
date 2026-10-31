"""Compare candidate models on this tool's own jobs, offline and repeatably.

    python -m toast.eval.model_bakeoff probe      --model qwen3.5:9b
    python -m toast.eval.model_bakeoff extraction --models qwen3.5:9b,gemma4:12b-it-qat
    python -m toast.eval.model_bakeoff embeddings --models nomic-embed-text,embeddinggemma-2:270m

Why this exists: public benchmarks do not measure what this tool needs (copy a
number exactly, never invent a fire name, ignore instructions hidden in an article).
The research behind the model choice found no independent evidence either way, so the
choice is made here, on cases with deliberate traps, and re-run whenever a model or a
prompt changes.

* extraction  - runs the real extractor on labelled passages and counts what matters,
  safety first: invented figures/names/places, forbidden claims (an obeyed injection,
  a rumoured number recorded as fact), then recall, relevance gating, latency.
  Models are ranked by safety failures first, recall second.
* embeddings  - separates same-event pairs from different-event pairs (including hard
  negatives), reports ranking quality (AUC) and the best cut-off, and what the
  CURRENT production thresholds would do (production uses L2 distance - see
  pipeline/embeddings.py).
* probe       - checks which way of switching thinking off actually works for a model
  on your Ollama (the prompt hint, or the native think:false parameter).

The shipped cases are SYNTHETIC. They catch bad models; they cannot separate good ones
or tell you how a model copes with your real feeds - add real cases with --fixture.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from toast.llm.client import LLMClient, LLMError
from toast.llm.extract import ExtractedClaim, extract_claims
from toast.llm.verify_extraction import _name_core, verify_claim

DATA = Path(__file__).with_name("cases")


# ---------------------------------------------------------------- extraction


@dataclass
class CaseScore:
    case_id: str
    error: str | None = None
    relevance_ok: bool = False
    expected: int = 0
    matched: int = 0
    invented_numbers: int = 0
    invented_names: int = 0
    invented_places: int = 0
    forbidden: list[str] = field(default_factory=list)
    evac_paraphrased: int = 0
    claims: int = 0
    latency_s: float = 0.0


def _same_name(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return _name_core(a).casefold() == _name_core(b).casefold()


def claim_matches(claim: ExtractedClaim, want: dict[str, Any], source: str) -> bool:
    if claim.kind != want["kind"]:
        return False
    for key in ("acreage", "containment_pct"):
        if key in want and (getattr(claim, key) is None or float(getattr(claim, key)) != want[key]):
            return False
    if "evac_status" in want and claim.evac_status != want["evac_status"]:
        return False
    if "incident_name" in want and not _same_name(claim.incident_name, want["incident_name"]):
        return False
    if want.get("verbatim"):
        _, issues = verify_claim(claim, source)
        if "evac_not_verbatim" in issues:
            return False
    return True


def forbidden_hits(claims: list[ExtractedClaim], forbid: dict[str, Any]) -> list[str]:
    hits: list[str] = []
    if forbid.get("any_claims") and claims:
        hits.append("recorded claims for a non-California article")
    for c in claims:
        if forbid.get("any_acreage") and c.acreage is not None:
            hits.append(f"invented acreage {c.acreage:g}")
        if forbid.get("any_containment") and c.containment_pct is not None:
            hits.append(f"invented containment {c.containment_pct:g}")
        if c.acreage in forbid.get("acreage", []):
            hits.append(f"forbidden acreage {c.acreage:g}")
        if c.containment_pct in forbid.get("containment_pct", []):
            hits.append(f"forbidden containment {c.containment_pct:g}")
        if c.evac_status in forbid.get("evac_status", []):
            hits.append(f"forbidden evac status {c.evac_status}")
        if any(_same_name(c.incident_name, n) for n in forbid.get("names", [])):
            hits.append(f"forbidden fire name {c.incident_name}")
        for name, acres in forbid.get("pairs", []):
            if _same_name(c.incident_name, name) and c.acreage == acres:
                hits.append(f"mixed up figures: {name} given {acres:g} acres")
    return hits


def score_case(case: dict[str, Any], client: Any) -> CaseScore:
    score = CaseScore(case["id"])
    started = time.monotonic()
    try:
        result = extract_claims(
            case["text"],
            reference_time=case["reference_time"],
            source_name=case.get("source_name", "synthetic"),
            client=client,
            verify=False,  # measure the model's raw behaviour, then judge it ourselves
        )
    except LLMError as exc:
        score.error = str(exc)[:160]
        score.latency_s = time.monotonic() - started
        return score
    score.latency_s = time.monotonic() - started
    expect = case["expect"]
    score.claims = len(result.claims)
    score.relevance_ok = result.california_relevant == expect["california_relevant"]
    wanted = expect["claims"]
    score.expected = len(wanted)
    unused = list(result.claims)
    for want in wanted:
        hit = next((c for c in unused if claim_matches(c, want, case["text"])), None)
        if hit is not None:
            unused.remove(hit)
            score.matched += 1
    for c in result.claims:
        kept, issues = verify_claim(c, case["text"])
        score.invented_numbers += kept is None
        score.invented_names += "name_not_in_source" in issues
        score.invented_places += "location_not_in_source" in issues
        score.evac_paraphrased += "evac_not_verbatim" in issues
    score.forbidden = forbidden_hits(result.claims, case.get("forbid", {}))
    return score


def summarize(scores: list[CaseScore]) -> dict[str, Any]:
    ran = [s for s in scores if s.error is None]
    expected = sum(s.expected for s in ran)
    invented = sum(s.invented_numbers + s.invented_names + s.invented_places for s in ran)
    forbidden = sum(len(s.forbidden) for s in ran)
    return {
        "runs": len(scores),
        "errors": len(scores) - len(ran),
        "relevance_accuracy": round(sum(s.relevance_ok for s in ran) / len(ran), 3) if ran else 0,
        "recall": round(sum(s.matched for s in ran) / expected, 3) if expected else None,
        "invented": invented,
        "forbidden": forbidden,
        "safety_failures": invented + forbidden,
        "evac_paraphrased": sum(s.evac_paraphrased for s in ran),
        "median_latency_s": round(statistics.median(s.latency_s for s in scores), 2)
        if scores
        else 0,
    }


def rank(results: dict[str, dict[str, Any]]) -> list[str]:
    """Safest first; among equals, best recall, then fastest."""
    return sorted(
        results,
        key=lambda m: (
            results[m]["errors"] + results[m]["safety_failures"],
            -(results[m]["recall"] or 0),
            results[m]["median_latency_s"],
        ),
    )


def run_extraction(
    clients: dict[str, Any], cases: list[dict[str, Any]], repeat: int = 1
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for model, client in clients.items():
        scores = [score_case(c, client) for c in cases for _ in range(repeat)]
        out[model] = {"summary": summarize(scores), "cases": [asdict(s) for s in scores]}
    return out


# ---------------------------------------------------------------- embeddings


def l2(a: list[float], b: list[float]) -> float:
    return math.dist(a, b)


def cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return 1 - dot / (na * nb) if na and nb else 1.0


def auc(same: list[float], different: list[float]) -> float:
    """Chance a random same-event pair is closer than a random different-event pair."""
    if not same or not different:
        return 0.0
    wins = sum((s < d) + 0.5 * (s == d) for s in same for d in different)
    return wins / (len(same) * len(different))


def best_cutoff(same: list[float], different: list[float]) -> tuple[float, float]:
    """(distance cut-off, accuracy) that best separates the two groups."""
    points = sorted({*same, *different})
    if not points:
        return 0.0, 0.0
    cuts = [points[0] - 1e-9] + [(a + b) / 2 for a, b in zip(points, points[1:], strict=False)]
    cuts.append(points[-1] + 1e-9)
    total = len(same) + len(different)
    best = max(cuts, key=lambda t: sum(d <= t for d in same) + sum(d > t for d in different))
    return best, (sum(d <= best for d in same) + sum(d > best for d in different)) / total


def at_cutoff(same: list[float], different: list[float], cut: float) -> dict[str, Any]:
    return {
        "cutoff": cut,
        "same_event_found": f"{sum(d <= cut for d in same)}/{len(same)}",
        "different_events_wrongly_merged": f"{sum(d <= cut for d in different)}/{len(different)}",
    }


def score_embeddings(embed: Any, pairs: list[dict[str, Any]], prefix: str = "") -> dict[str, Any]:
    from toast.pipeline.embeddings import CORROBORATION_THRESHOLD, DUP_THRESHOLD

    texts = [prefix + t for p in pairs for t in (p["a"], p["b"])]
    vecs = embed(texts)
    out: dict[str, Any] = {"dim": len(vecs[0]) if vecs else 0}
    for metric, fn in (("l2", l2), ("cosine", cosine_distance)):
        dist = [fn(vecs[2 * i], vecs[2 * i + 1]) for i in range(len(pairs))]
        same = [d for d, p in zip(dist, pairs, strict=True) if p["same_event"]]
        diff = [d for d, p in zip(dist, pairs, strict=True) if not p["same_event"]]
        cut, acc = best_cutoff(same, diff)
        out[metric] = {
            "auc": round(auc(same, diff), 3),
            "best_cutoff": round(cut, 3),
            "best_accuracy": round(acc, 3),
            "same_mean": round(statistics.mean(same), 3) if same else None,
            "different_mean": round(statistics.mean(diff), 3) if diff else None,
        }
    l2d = [l2(vecs[2 * i], vecs[2 * i + 1]) for i in range(len(pairs))]
    same_l2 = [d for d, p in zip(l2d, pairs, strict=True) if p["same_event"]]
    diff_l2 = [d for d, p in zip(l2d, pairs, strict=True) if not p["same_event"]]
    out["current_thresholds_l2"] = {
        "corroboration": at_cutoff(same_l2, diff_l2, CORROBORATION_THRESHOLD),
        "duplicate": at_cutoff(same_l2, diff_l2, DUP_THRESHOLD),
    }
    return out


# --------------------------------------------------------------------- probe


def probe_model(base_url: str, model: str, transport: Any = None) -> list[dict[str, Any]]:
    """Which way of turning thinking off does this model honour? Reports what came back
    rather than guessing: reasoning text in the reply means thinking was NOT off."""
    root = base_url.rstrip("/").removesuffix("/v1")
    prompt = "What is 17 times 23? Answer with just the number."
    http = httpx.Client(timeout=300, transport=transport)
    results: list[dict[str, Any]] = []

    def attempt(label: str, url: str, body: dict[str, Any], dig: Any) -> None:
        started = time.monotonic()
        try:
            r = http.post(url, json=body)
            status = r.status_code
            data = r.json() if status == 200 else {}
        except httpx.HTTPError as exc:
            results.append({"route": label, "status": "unreachable", "detail": str(exc)[:80]})
            return
        content, reasoning = dig(data) if status == 200 else ("", "")
        thinking = bool(reasoning) or "<think>" in content
        results.append(
            {
                "route": label,
                "status": status,
                "thinking_was_off": status == 200 and not thinking,
                "answer_ok": "391" in content,
                "seconds": round(time.monotonic() - started, 1),
            }
        )

    def oa(data: dict[str, Any]) -> tuple[str, str]:
        m = (data.get("choices") or [{}])[0].get("message", {})
        return m.get("content", "") or "", m.get("reasoning", "") or m.get(
            "reasoning_content", ""
        ) or ""

    def nat(data: dict[str, Any]) -> tuple[str, str]:
        m = data.get("message", {})
        return m.get("content", "") or "", m.get("thinking", "") or ""

    msgs = [{"role": "user", "content": prompt}]
    attempt(
        "soft /no_think (OpenAI route; current default)",
        f"{root}/v1/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": "/no_think\n" + prompt}]},
        oa,
    )
    attempt(
        "native think:false (LLM_THINK_MODE=native)",
        f"{root}/api/chat",
        {"model": model, "messages": msgs, "stream": False, "think": False},
        nat,
    )
    attempt(
        "baseline: no switch (thinking models should think here)",
        f"{root}/api/chat",
        {"model": model, "messages": msgs, "stream": False},
        nat,
    )
    return results


def recommend_think_mode(results: list[dict[str, Any]]) -> str:
    soft, native = results[0], results[1]
    if soft.get("thinking_was_off") and soft.get("answer_ok"):
        return "soft works for this model: LLM_THINK_MODE=soft (the default) is fine."
    if native.get("thinking_was_off") and native.get("answer_ok"):
        return "soft does not turn thinking off here but native does: set LLM_THINK_MODE=native."
    return (
        "neither switch turned thinking off cleanly: see the table; "
        "this model may need a different template."
    )


# ----------------------------------------------------------------------- CLI


def _clients(models: list[str], think_mode: str | None) -> dict[str, LLMClient]:
    from toast.core.config import Settings

    settings = Settings(llm_think_mode=think_mode) if think_mode else Settings()
    return {m: LLMClient(settings, model=m) for m in models}


def _print_extraction(results: dict[str, Any]) -> None:
    order = rank({m: r["summary"] for m, r in results.items()})
    print(f"{'model':<30}{'safety':>8}{'invented':>10}{'forbid':>8}{'recall':>8}"
          f"{'relev':>8}{'errors':>8}{'evac~':>7}{'sec':>7}")  # fmt: skip
    for m in order:
        s = results[m]["summary"]
        print(f"{m[:29]:<30}{s['safety_failures']:>8}{s['invented']:>10}{s['forbidden']:>8}"
              f"{s['recall']!s:>8}{s['relevance_accuracy']:>8}{s['errors']:>8}"
              f"{s['evac_paraphrased']:>7}{s['median_latency_s']:>7}")  # fmt: skip
    print("\nlower 'safety' is better; ties are broken by recall, then speed.")
    for m in order:
        for c in results[m]["cases"]:
            if c["forbidden"] or c["error"]:
                print(f"  {m} / {c['case_id']}: {c['error'] or '; '.join(c['forbidden'])}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="toast.eval.model_bakeoff", description=__doc__.split("\n\n")[0]
    )
    p.add_argument("command", choices=["probe", "extraction", "embeddings"])
    p.add_argument("--model", help="probe: the model to test")
    p.add_argument("--models", help="comma-separated models to compare")
    p.add_argument("--fixture", help="your own cases/pairs JSON (same shape as the shipped ones)")
    p.add_argument(
        "--repeat", type=int, default=1, help="extraction: runs per case (shows variance)"
    )
    p.add_argument("--think-mode", choices=["soft", "native"], help="override LLM_THINK_MODE")
    p.add_argument("--prefix", default="", help="embeddings: text prepended to every snippet")
    p.add_argument("--out", help="write full results as JSON here")
    args = p.parse_args(argv)
    from toast.core.config import Settings

    settings = Settings()
    try:
        if args.command == "probe":
            model = args.model or settings.llm_model
            results = probe_model(settings.llm_base_url, model)
            print(f"thinking-off probe for {model}:")
            for r in results:
                print("  ", r)
            print("\n" + recommend_think_mode(results))
            payload: Any = results
        elif args.command == "extraction":
            models = (args.models or settings.llm_model).split(",")
            path = Path(args.fixture) if args.fixture else DATA / "extraction_gold.json"
            cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
            payload = run_extraction(_clients(models, args.think_mode), cases, args.repeat)
            _print_extraction(payload)
        else:
            models = (args.models or settings.embedding_model).split(",")
            path = Path(args.fixture) if args.fixture else DATA / "embedding_pairs.json"
            pairs = json.loads(path.read_text(encoding="utf-8"))["pairs"]
            payload = {}
            for m in models:
                client = LLMClient(settings, embed_model=m)
                payload[m] = score_embeddings(client.embed, pairs, args.prefix)
                r = payload[m]
                cur = r["current_thresholds_l2"]["corroboration"]
                print(
                    f"{m}: dim {r['dim']}  AUC(l2) {r['l2']['auc']}  "
                    f"best cut-off(l2) {r['l2']['best_cutoff']} -> {r['l2']['best_accuracy']:.0%}"
                )
                print(f"    current corroboration cut-off {cur['cutoff']}: found "
                      f"{cur['same_event_found']} same-event, wrongly merged "
                      f"{cur['different_events_wrongly_merged']} different-event")  # fmt: skip
    except (LLMError, httpx.HTTPError) as exc:
        print(f"bake-off stopped: {exc}", file=sys.stderr)
        return 1
    out = (
        Path(args.out)
        if args.out
        else settings.toast_data_dir
        / "eval"
        / (f"bakeoff-{args.command}-{datetime.now(UTC):%Y%m%dT%H%M%S}.json")
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    print(f"\nfull results: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
