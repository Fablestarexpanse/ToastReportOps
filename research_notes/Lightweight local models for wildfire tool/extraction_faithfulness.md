# Faithful structured extraction and short-form generation with small open-weight LLMs (1B-32B), as of 2026-10-31

Research limits: about 16 tool calls. Most numbers come from web-fetch summaries of primary pages. Anything marked UNVERIFIED came from aggregators or snippets. No benchmark found measures our exact task (numbered-facts-to-update with citations and verbatim quoting, or number-faithful extraction from radio transcripts).

## Which benchmarks measure what we need, and how do small models score?

### Takeaway
The only current, primary, per-model small-model faithfulness table found is Vectara's leaderboard (last updated 2026-10-22). It is grounded summarisation of news-like passages, so it is a proxy, not extraction. Its numbers are non-monotonic in size and vary by model family, so a model's own score matters more than its parameter count. Standard instruction-following, FACTS Grounding, BFCL and StructEval scores for small models could not be verified cleanly.

### Cited Findings
Vectara Hallucination Leaderboard (HHEM-2.3 judge, vendor-run but applied independently to all models; private dataset of over 7,700 articles, 50 to 24K words; page says "Last updated on October 22, 2026"). Rows with about 40B parameters or fewer, sizes inferred from model names. Format: hallucination rate / answer rate / avg summary words. Lower hallucination is better; a low answer rate means the model refused or failed on many documents, so its rate is on a filtered subset.
- Phi-4 (14B): 3.7% / 80.7% / 120.9 — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Gemma-3-12b-it: 4.4% / 97.4% / 89.7 — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Qwen3-8B: 4.8% / 99.9% / 83.6 — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Mistral-Small-2501 (24B): 5.1% / 97.9% / 98.8 — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Gemma-4-26b-a4b-it: 5.2% / 99.8% / 67.1; Granite-4.0-h-small: 5.2% / 100% / 107.4 — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Qwen3-14B: 5.4%; Qwen3-4B: 5.7%; Qwen3-32B: 5.9% (all about 99.9% answer rate) — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Gemma-3-4b-it: 6.4% but only 67.3% answer rate — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Gemma-3-27b-it: 7.4%; Gemma-4-31b-it: 7.4%; Ministral-8b-2410: 7.4%; Ministral-3b-2410: 7.3% — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Nemotron-3-Nano-30B-A3B: 9.6%; GLM-4.7-flash: 9.3%; Granite-3.3-8b: 10.6%; Aya-expanse-8b: 9.5%; Aya-expanse-32b: 10.9% — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Newer releases are worse: Qwen3.5-35b-a3b 10.5%, Qwen3.5-27b 12.1%, Ministral-3-14b-2512 19.4%, Ministral-3-8b-2512 21.7%, Ministral-3-3b-2512 24.2% (74.3% answer rate), Phi-4-mini-instruct 23.5% (summaries about 420 words) — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Best small entry: antgroup/finix_s1_32b at 1.8%, with long summaries (172 words). Leaderboard entry only; I did not verify the model or its provenance — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Vectara also runs a separate FaithJudge-based leaderboard (per the repo). I did not open it. — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Caveat: HHEM is itself a model. FaithBench (NAACL 2024, arXiv 2410.13210) found that on hard summarisation samples all hallucination detectors correlate poorly with human labels (best F1-macro 55%, balanced accuracy 58%). Vectara rates are therefore noisy, especially for small differences. Samples were filtered for detector disagreement, so rates there run higher than on easy cases. FaithBench gives little size-scaling evidence — [FaithBench summary](https://www.themoonlight.io/review/faithbench-a-diverse-hallucination-benchmark-for-summarization-by-modern-llms), [paper](https://arxiv.org/html/2410.13210v1)
- JSONSchemaBench (arXiv 2501.10868, Jan-Feb 2025; independent academic): 10K real-world schemas. Llama-3.2-1B was used for coverage tests and Llama-3.1-8B for quality tests. Guidance had the highest compliance among open engines (for example Kubernetes schemas 0.91 coverage / 0.92 compliance; GitHub Hard 0.41 / 0.69). llama.cpp was close (0.76 / 0.78; 0.39 / 0.63). XGrammar was mid-range. Outlines had the lowest compliance, mostly from timeouts. Unconstrained "LM only" had the lowest compliance overall — [arXiv v3](https://arxiv.org/html/2501.10868v3)
- Instruction following (INCOMPLETE and UNVERIFIED): Gemma 3 27B scored 31.8% on IFBench in one Artificial Analysis snapshot, and Gemma 3 12B 88.9% on IFEval on an aggregator; these come from snippets and could not be matched to a single protocol — [BenchLM IFBench](https://www.benchlm.ai/benchmarks/ifBench), [IFEval aggregator](https://anotherwrapper.com/tools/llm-pricing/evals/ifeval)
- BFCL v4 (UNVERIFIED, aggregator, 2026-05-06): Qwen3 8B about 80.0 and Qwen3 14B about 79.8, with a Hammer2.1 entry at about 93.1. The page text was garbled, so the row labels and versions are not confirmed. Other aggregators list far lower scores for different 8B models (49.7, 39.2), so versions and metrics clearly differ — [benchmarklist](https://benchmarklist.com/benchmarks/bfcl_v4/), [BenchLM](https://benchlm.ai/benchmarks/bfcl-v4). Use the official Gorilla BFCL site for real numbers.
- FACTS Grounding (Google DeepMind, Kaggle-hosted; average of public and private sets). An evaluation-records dataset on Hugging Face includes Gemma 3 4B/12B/27B, but I could not read the scores — [DeepMind](https://deepmind.google/discover/blog/facts-grounding-a-new-benchmark-for-evaluating-the-factuality-of-large-language-models/), [HF records](https://huggingface.co/datasets/evaleval/EEE_datastore/commit/d907f4941a7e38c62a7cf2506f3b39b8b33d7bee)
- Crisis-text classification (not extraction): a 2024 study (arXiv 2412.10413, WWW 2025) over 19 disasters found proprietary beat open models on every task, few-shot gave minimal gains for most models, flood posts were hardest, and typos hurt — [arXiv](https://arxiv.org/pdf/2412.10413)

### Inferences
- Vectara scores for 4B to 32B Qwen3, Gemma 3 and Phi-4 sit in a narrow 3.7% to 7.4% band. Given detector noise, treat these as roughly tied, not ranked.
- Newer generations are not automatically better (Qwen3.5 and Ministral-3 are materially worse than their predecessors on this test). Pin and test the exact model and quantisation you deploy.
- Answer rate matters: Phi-4 (80.7%) and Gemma-3-4b (67.3%) look good partly because they decline many documents. For a safety tool, a refusal rate that high is its own failure mode.

### Gaps
- No clean source for RAGTruth, StructEval, IFBench, or FACTS Grounding scores for small open models; I did not open the primary papers or leaderboards.
- No benchmark tests numeric-fidelity or verbatim-quote extraction from noisy social-media or radio-transcript text.
- Did not verify the antgroup finix_s1_32b entry.

## Is there a quality gap between 4B-9B and 14B-32B, and where is the knee?

### Takeaway
On the one measured faithfulness benchmark found, within-family size has little consistent effect (Qwen3 4B to 32B all 4.8% to 5.9%). No extraction-specific scaling study was found, so the knee cannot be stated from evidence; it must be measured on our own task.

### Cited Findings
- Qwen3 family on Vectara: 8B 4.8%, 14B 5.4%, 4B 5.7%, 32B 5.9% — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Gemma 3: 12B 4.4%, 4B 6.4% (67.3% answer rate), 27B 7.4% — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Ministral-3 (Dec 2025): 3B 24.2%, 8B 21.7%, 14B 19.4%, a size gradient but all poor — [Vectara](https://github.com/vectara/hallucination-leaderboard)
- Chinese QA hallucination benchmark C-FAITH: hallucination fell with parameter count within Qwen-2.5 and Llama 3 families (QA, not extraction; no 7B in the quoted result) — [arXiv 2504.10167](https://arxiv.org/pdf/2504.10167)
- German public-sector benchmark (2026): faithfulness ranged from 0.20 (SmolLM2 1.7B) to 0.83 (Mixtral 8x22B), and small models showed substantially higher variance than medium or large — [arXiv 2606.13111](https://arxiv.org/pdf/2606.13111) (seen via search snippet only)

### Inferences
- Variance, not mean, seems to be the small-model problem: worst-case behaviour (a single invented number) is not captured by average rates. This is an inference from the German benchmark and the safety framing.
- Sub-4B models (1B to 3B) look risky (Ministral-3-3B, Phi-4-mini, SmolLM2), but 4B to 14B Qwen3, Gemma 3 and Phi-4 are comparable on this proxy.

### Gaps
- No same-family 7B vs 32B extraction faithfulness study found. No evidence on long-input extraction by size.

## Does thinking/reasoning mode help or hurt?

### Takeaway
Evidence is indirect (summarisation) and mixed; none is for extraction. Some evidence shows more reasoning raises hallucination in summaries. Thinking also costs latency and, in Ollama, interacts with structured-output bugs.

### Cited Findings
- "Understanding LLM Reasoning for Abstractive Summarization" (arXiv 2512.03503, Dec 2025): raising GPT-5 reasoning effort lowered factual faithfulness while ROUGE and BERTScore stayed stable; the authors suggest over-thinking fills non-existent logical gaps. Gemini 2.5 Flash thinking budget did not consistently help. Explicit reasoning strategies traded grounding for fluency, while implicit reasoning in reasoning models showed the inverse pattern. Models were proprietary, not small open ones — [arXiv](https://arxiv.org/pdf/2512.03503)
- A separate dialogue-summarisation paper (Expert Systems with Applications, 2025) reportedly found reasoning models give longer, less faithful summaries with higher novelty and lower source coverage. Seen via a search summary only. — [search summary of Yuan and Zhang review](https://www.themoonlight.io/review/understanding-llm-reasoning-for-abstractive-summarization)
- Ollama bug #14645: with thinking disabled, `format` was ignored on Qwen3.5 (opened 2026-03-05; closed 2026-07-07 via PR #15901; reporter confirmed fixed in 0.31.2). A commenter said older models such as Qwen3 were also affected from 0.30.0; another reported the chat path still ignoring format when thinking was suppressed via a system prompt on 0.30.7, while generate enforced it. Gemma4 was fixed separately in 0.21.1 — [GitHub](https://github.com/ollama/ollama/issues/14645)

### Inferences
- For extraction and cited generation, default to non-thinking (or minimal reasoning) plus deterministic post-checks; thinking mainly adds latency and room to "fill gaps". This is a hypothesis to test on our own set, not an established result.
- Constrained decoding and thinking toggles interact in Ollama; test the exact Ollama version for each model.

### Gaps
- No study of reasoning mode on extraction faithfulness for open 4B to 32B models; no latency measurements found.

## How reliable are Ollama structured outputs and constrained decoding, and do they hurt accuracy?

### Takeaway
Constrained decoding guarantees syntax, not truth. Reliability in Ollama has been version- and model-dependent (several 2026 bugs), so validate every response. Accuracy effects are mixed: gains on simple tasks, losses possible on reasoning-heavy ones.

### Cited Findings
- Ollama docs: pass a JSON schema in `format`; also include the schema in the prompt to ground the response; set temperature 0 for determinism; validate client-side (for example Pydantic); Ollama cloud does not currently support structured outputs — [Ollama docs](https://docs.ollama.com/capabilities/structured-outputs)
- Constrained decoding did not reduce, and sometimes raised, accuracy on three reasoning tasks with Llama-3.1-8B (GSM8K 80.1% LM-only vs 82.4% to 83.8% with constraints) — [JSONSchemaBench](https://arxiv.org/html/2501.10868v3). Authors note it could shift output out-of-distribution (for example number formatting) and that only three tasks were tested.
- "Let Me Speak Freely?" (Tam et al., EMNLP 2024 Industry, arXiv 2408.02442): stricter format constraints, especially constrained JSON mode, degrade reasoning tasks while classification can improve; looser formats help reasoning. A secondary review cites GPT-3.5 GSM8K 76.6% falling to 49.25% (UNVERIFIED against the paper) — [arXiv](https://arxiv.org/html/2408.02442v3)
- UNDRR extraction paper (arXiv 2609.23853, Sep 2026): Structured Outputs with Pydantic gave full vocabulary compliance (no out-of-vocabulary labels in 3,572 records) but the authors stress "structural compliance does not establish whether an extracted statement is supported by the document" — [arXiv](https://arxiv.org/html/2609.23853v1)
- Ollama issues: format ignored with thinking off on Qwen3.5 (#14645, above); a separate thread covers JSON schema adherence on Qwen 3.5/3.6 (#14493 is related but about tool calling and think-tag rendering). Blog reports property `description` fields are silently ignored by the grammar path and GPT-OSS structured-output unreliability — [issue 14493](https://github.com/ollama/ollama/issues/14493), [Glukhov GPT-OSS write-up](https://www.glukhov.org/post/2025/10/ollama-gpt-oss-structured-output-issues/). The "descriptions ignored" claim came from a third-party search summary and is UNVERIFIED.
- Empty-list pitfall: a practitioner article notes models invent array entries to "fill" the array when nothing exists to extract; schema should allow null or empty explicitly — [dev.to](https://dev.to/pockit_tools/llm-structured-output-in-2026-stop-parsing-json-with-regex-and-do-it-right-34pk) (practitioner source, low authority)

### Inferences
- Use schema `format` for syntax, but put the schema in the prompt too, keep temperature 0, add "unknown/null" options for every field, and validate. Consider a two-step approach (free-text evidence, then conversion to JSON) if reasoning quality drops, per Tam et al.
- Pin the Ollama version and run a conformance smoke test per model after every upgrade.

### Gaps
- No independent measurement of Ollama `format` vs prompt-only compliance or accuracy for 4B to 32B models. Ollama's grammar engine is llama.cpp-based; I did not verify this from primary docs.

## Published work on LLM extraction from crisis, disaster, emergency text

### Takeaway
Related work exists but none directly measures invented numbers or locations for small open models. Findings support: recall bias and over-extraction, locations that are right place/wrong extent, and quotes that are not always verbatim.

### Cited Findings
- UNDRR "Schema-Constrained LLM Extraction of Georeferenced Disasters" (arXiv 2609.23853, 20 Sep 2026): pooled micro-F1 on 171 event documents: GPT-5.4 86.6, GPT-5 86.0, Grok 4.6 81.2, Claude Opus 5 81.2, Claude Sonnet 5 80.9, DeepSeek V3.1 79.7, GPT-5 mini 78.7, Claude Haiku 4.5 78.3, Llama 4 Maverick 68.2, Mistral Large 56.9, spaCy-gazetteer 44.2. No parameter sizes and no small open models. Evidence traceability (quoted snippets found verbatim in source) was 72.0% for GPT-5, 47.2% for GPT-5.4, 94.6% for DeepSeek V3.1 (paraphrases allowed). Location audit of 75 mentions: 77.3% correct, 21.3% right place wrong extent, 1.3% wrong place. Failure examples: publication year substituted for event date, invented temporal anchors, facilities treated as affected locations. No fabrication rate reported — [arXiv](https://arxiv.org/html/2609.23853v1)
- "From Errors to Precision: A Taxonomy-Guided Approach for LLM Flood Cause Extraction" (dela Cruz, Hendrickx, Larson; WNUT 2026): off-the-shelf LLMs show strong recall bias; impact descriptions, background context and opinions trigger over-extraction; prompts with definitions and exclusions improved precision/F0.5. Models not named; hallucination not discussed — [ACL Anthology](https://aclanthology.org/2026.wnut-1.8/)
- Crisis tweet robustness study (arXiv 2412.10413): see first section.
- "From headlines to databases: leveraging LLMs for structured disaster event extraction" (Springer, 2026): exists; I read only the search summary and did not check its models or error analysis — [Springer](https://link.springer.com/article/10.1007/s41060-025-01017-1)
- ReliefWeb causal evidence extraction (Zhang and Oussalah, arXiv 2608.04576, Aug 2026): per a search summary uses query-conditioned extraction and short supporting spans for traceability. The PDF text could not be read, so details are UNVERIFIED — [arXiv](https://arxiv.org/pdf/2608.04576)
- DRRGS disaster response report generation with trustworthiness evaluation (ScienceDirect, 2026): found by title only; not read — [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S2212420926004231)
- FireForm issue #387 proposes schema validation, hallucination detection and corrective retry for first-responder incident extraction (design discussion, not evidence) — [GitHub](https://github.com/fireform-core/FireForm/issues/387)

### Inferences
- Even frontier models mis-assign dates and extents; small models on radio transcripts with ASR errors should be assumed worse. Date handling (event date vs publication date) and location extent are specific risks to test.

### Gaps
- No wildfire-specific study found. No paper quantifies number fabrication rates for 1B to 32B open models on crisis text. ASR/radio-transcript extraction not found.

## Practical techniques to reduce hallucination

### Takeaway
Best-supported practice is deterministic verification of outputs against the source (exact-match quotes and numbers), not trusting the model. Controlled evidence for each technique on small models is thin.

### Cited Findings
- Verbatim-span checking: a tool and practitioner guides argue for deterministic confirmation that each quote exists in the source before display; lexical matching catches fabricated quotes but not paraphrase or unquoted claims. Vendor/practitioner sources, not validated studies — [verbatimeter](https://pypi.org/project/verbatimeter/), [creditgpt guide](https://www.creditgpt.com/content/how-to-stop-ai-hallucination-in-document-analysis)
- ACL-Verbatim preprint (2026; seen via aggregators only): a 150M ModernBERT span classifier reached word-level F1 53.6 vs 48.7 for the strongest LLM extractor; LLM extractors had higher recall but lower precision — [hyper.ai summary](https://hyper.ai/papers/2605.21102). UNVERIFIED.
- The UNDRR paper shows the value and limits of quote checking: even verbatim-match rates varied from 47% to 95% across models, so the check must be enforced in code — [arXiv](https://arxiv.org/html/2609.23853v1)
- Prompt exclusions and definitions improved precision for flood cause extraction — [ACL Anthology](https://aclanthology.org/2026.wnut-1.8/)
- Ollama docs recommend temperature 0 and schema in prompt — [Ollama docs](https://docs.ollama.com/capabilities/structured-outputs)
- Few-shot examples gave minimal gains on crisis tweet classification for most models — [arXiv 2412.10413](https://arxiv.org/pdf/2412.10413)
- FaithBench: automated detectors are near chance on hard cases, so LLM-judge or HHEM verification should not be the only safeguard — [arXiv](https://arxiv.org/html/2410.13210v1)

### Inferences
- Recommended design for this tool (my synthesis, not tested): (1) extract with a schema requiring a source_quote per field; (2) in code, require each quote to be an exact substring of the source and each number/date/place token to appear in it, else reject or flag; (3) generate updates from numbered facts with an ID per sentence, and copy designated text (evacuation orders) by code substitution, never model re-typing; (4) post-check that every number in the output appears in the cited facts; (5) human review for orders. Non-thinking mode, temperature 0.
- Two-pass extract-then-verify: I found no independent study measuring its effect, so treat as plausible but unproven.

### Gaps
- No controlled study found comparing verbatim-quote extraction, two-pass verification, low temperature and few-shot on small-model hallucination. No evidence found on citation-faithfulness for numbered-fact generation.
