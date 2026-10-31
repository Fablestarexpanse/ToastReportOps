"""The bake-off harness: its fixtures, scoring and ranking must be trustworthy before
any model result is."""

from __future__ import annotations

import json
import math

import httpx
import pytest

from toast.eval import model_bakeoff as mb

CASES = json.loads((mb.DATA / "extraction_gold.json").read_text())["cases"]
PAIRS = json.loads((mb.DATA / "embedding_pairs.json").read_text())["pairs"]


class ScriptedLLM:
    """A fake model: `respond(case)` returns the raw JSON the extractor would parse."""

    def __init__(self, respond):
        self.respond = respond
        self.case = None

    def chat_json(self, messages, **kw):
        text = messages[0]["content"]
        case = next(c for c in CASES if c["text"] in text)
        return self.respond(case)


def perfect(case):
    claims = []
    for want in case["expect"]["claims"]:
        c = {"kind": want["kind"], "confidence": 0.9}
        parts = [want["kind"], want.get("incident_name") or ""]
        for key in ("acreage", "containment_pct"):
            if key in want:
                parts.append(f"{want[key]:g}")
        c["content"] = case["text"] if want.get("verbatim") else " ".join(p for p in parts if p)
        for key in ("acreage", "containment_pct", "evac_status", "incident_name"):
            if key in want:
                c[key] = want[key]
        claims.append(c)
    return {"california_relevant": case["expect"]["california_relevant"], "claims": claims}


def reckless(case):
    """Rounds, invents, obeys injections, and mixes up figures."""
    bad = {"california_relevant": True, "claims": [
        {"kind": "acreage", "acreage": 99999, "content": "fire at 99999 acres", "confidence": 0.9,
         "incident_name": "Camp Fire"},
        {"kind": "containment", "containment_pct": 100, "content": "100 percent contained",
         "confidence": 0.9},
    ]}  # fmt: skip
    return bad


# ----------------------------------------------------------------- fixtures


def test_every_case_is_well_formed():
    assert len(CASES) >= 10 and len({c["id"] for c in CASES}) == len(CASES)
    for c in CASES:
        assert {"id", "text", "reference_time", "expect", "forbid", "trap"} <= set(c)
        assert isinstance(c["expect"]["california_relevant"], bool)


def test_the_expected_answers_are_supported_by_their_own_sources():
    """If the gold answer itself failed the verifier the harness would punish correct
    models. A perfect model must therefore score perfectly."""
    scores = [mb.score_case(c, ScriptedLLM(perfect)) for c in CASES]
    summary = mb.summarize(scores)
    assert summary["errors"] == 0 and summary["safety_failures"] == 0, [
        (s.case_id, s.forbidden, s.invented_numbers)
        for s in scores
        if s.forbidden or s.invented_numbers
    ]
    assert summary["recall"] == 1.0 and summary["relevance_accuracy"] == 1.0


def test_a_reckless_model_is_caught_and_ranked_last():
    results = mb.run_extraction({"good": ScriptedLLM(perfect), "bad": ScriptedLLM(reckless)}, CASES)
    good, bad = results["good"]["summary"], results["bad"]["summary"]
    assert bad["safety_failures"] > 10 and bad["recall"] < good["recall"]
    assert mb.rank({m: r["summary"] for m, r in results.items()}) == ["good", "bad"]


def test_each_trap_is_actually_detectable():
    by_id = {c["id"]: c for c in CASES}
    inj = mb.score_case(by_id["prompt-injection"], ScriptedLLM(reckless))
    assert any("forbidden containment" in f for f in inj.forbidden)
    rounded = mb.score_case(
        by_id["rounding-trap"],
        ScriptedLLM(
            lambda c: {
                "claims": [
                    {"kind": "acreage", "acreage": 1200, "content": "Cedar 1200 acres",
                     "confidence": 0.9}
                ]
            }
        ),
    )  # fmt: skip
    assert rounded.invented_numbers == 1 and rounded.matched == 0
    mixed = mb.score_case(
        by_id["two-fires"],
        ScriptedLLM(lambda c: {"claims": [
            {"kind": "acreage", "acreage": 5200, "content": "Lake Fire 5200 acres",
             "incident_name": "Lake Fire", "confidence": 0.9}]}),
    )  # fmt: skip
    assert any("mixed up" in f for f in mixed.forbidden)
    foreign = mb.score_case(
        by_id["other-state"],
        ScriptedLLM(lambda c: {"california_relevant": True, "claims": [
            {"kind": "other", "content": "fire in Spain", "confidence": 0.5}]}),
    )  # fmt: skip
    assert not foreign.relevance_ok and foreign.forbidden


def test_paraphrased_evacuation_does_not_count_as_a_verbatim_match():
    case = next(c for c in CASES if c["id"] == "evac-verbatim")
    llm = ScriptedLLM(lambda c: {"claims": [{"kind": "evac", "evac_status": "order",
                                             "content": "Leave Zone BUT-1043 now",
                                             "confidence": 0.9}]})  # fmt: skip
    s = mb.score_case(case, llm)
    assert s.matched == 0 and s.evac_paraphrased == 1


def test_model_errors_are_recorded_not_fatal():
    def boom(case):
        return "not json at all"

    s = mb.score_case(CASES[0], ScriptedLLM(boom))
    assert s.error and mb.summarize([s])["errors"] == 1


# --------------------------------------------------------------- embeddings


def test_metrics_basics():
    assert mb.auc([0.1, 0.2], [0.8, 0.9]) == 1.0
    assert mb.auc([0.8], [0.1]) == 0.0
    assert mb.auc([0.5], [0.5]) == 0.5
    cut, acc = mb.best_cutoff([0.1, 0.2, 0.3], [0.7, 0.8])
    assert 0.3 < cut < 0.7 and acc == 1.0
    assert math.isclose(mb.cosine_distance([1, 0], [0, 1]), 1.0)
    assert math.isclose(mb.l2([0, 0], [3, 4]), 5.0)


def test_embedding_scoring_separates_a_good_model_from_a_blind_one():
    def good(texts):
        # near-identical vectors inside each pair for same-event pairs, far apart otherwise
        vecs = []
        for i in range(0, len(texts), 2):
            same = PAIRS[i // 2]["same_event"]
            vecs += [[1.0, 0.0], [0.99, 0.05] if same else [0.0, 1.0]]
        return vecs

    def blind(texts):
        return [[1.0, 0.0] for _ in texts]  # everything identical: cannot tell pairs apart

    ok = mb.score_embeddings(good, PAIRS)
    assert ok["l2"]["auc"] == 1.0 and ok["l2"]["best_accuracy"] == 1.0
    assert ok["current_thresholds_l2"]["corroboration"]["different_events_wrongly_merged"] == "0/9"
    assert mb.score_embeddings(blind, PAIRS)["l2"]["auc"] == 0.5


def test_prefix_is_applied_to_every_snippet():
    seen = []

    def spy(texts):
        seen.extend(texts)
        return [[1.0, 0.0]] * len(texts)

    mb.score_embeddings(spy, PAIRS[:2], prefix="clustering: ")
    assert seen and all(t.startswith("clustering: ") for t in seen)


# -------------------------------------------------------------------- probe


def _ollama(soft_thinks: bool, native_thinks: bool):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/v1/chat/completions":
            msg = {"content": "391", **({"reasoning": "let me think"} if soft_thinks else {})}
            return httpx.Response(200, json={"choices": [{"message": msg}]})
        thinks = native_thinks if body.get("think") is False else True
        msg = {"content": "391", **({"thinking": "hmm"} if thinks else {})}
        return httpx.Response(200, json={"message": msg})

    return httpx.MockTransport(handler)


def test_probe_recommends_native_when_only_the_native_switch_works():
    results = mb.probe_model(
        "http://llm:11434/v1", "m", _ollama(soft_thinks=True, native_thinks=False)
    )
    assert results[0]["thinking_was_off"] is False and results[1]["thinking_was_off"] is True
    assert results[2]["thinking_was_off"] is False  # baseline really did think
    assert "LLM_THINK_MODE=native" in mb.recommend_think_mode(results)


def test_probe_recommends_soft_when_it_works():
    results = mb.probe_model(
        "http://llm:11434/v1", "m", _ollama(soft_thinks=False, native_thinks=False)
    )
    assert "LLM_THINK_MODE=soft" in mb.recommend_think_mode(results)


def test_probe_reports_when_neither_works():
    results = mb.probe_model(
        "http://llm:11434/v1", "m", _ollama(soft_thinks=True, native_thinks=True)
    )
    assert "neither" in mb.recommend_think_mode(results)


def test_probe_survives_an_unreachable_server():
    dead = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    results = mb.probe_model("http://llm:11434/v1", "m", dead)
    assert all(r["status"] == "unreachable" for r in results)


@pytest.mark.parametrize("command", ["probe", "extraction", "embeddings"])
def test_cli_stops_cleanly_when_ollama_is_down(command, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("TOAST_DATA_DIR", str(tmp_path))
    out = mb.main([command, "--out", str(tmp_path / "r.json")])
    # extraction/embeddings stop with a message; probe reports unreachable and still exits 0
    assert out in (0, 1)
