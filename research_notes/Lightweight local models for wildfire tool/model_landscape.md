# Small open-weight LLMs (0.5B-32B) runnable via Ollama on 12/24 GB NVIDIA GPUs, as of 2026-10-31

Retrieval date for all items below: 2026-10-31 (fetched via WebFetch, which summarises pages with a small model; treat exact numbers as "as reported by the fetch" and re-verify before committing). About 14 tool calls were used; coverage is partial (see Gaps).

## Newest releases per family: sizes, dates, licences, context, dense/MoE

### Takeaway
Qwen has moved well past 3.5: Qwen3.6 (Apr 2026) and Qwen3.8-27B (Aug 2026, dense, Apache-2.0) exist and are on Ollama. Gemma 4 (Mar 2026, 12B added Jun 2026) is Apache-2.0 and fits 12 GB at 12B/E4B. IBM Granite 4.2 (Apache-2.0, 8B 5.3 GB) is explicitly aimed at extraction/classification/JSON. For 24 GB, Qwen3.8-27B (18 GB) and Gemma 4 31B/26B-A4B are the main upgrades over Qwen3.5-9B. No new small (<=9B) Qwen after 3.5 was found.

### Cited Findings
**Qwen**
- Qwen3.5 small series (0.8B, 2B, 4B, 9B) released 2026-03-02; Qwen3.5-27B, 35B-A3B, 122B-A10B on 2026-02-24; 397B-A17B on 2026-02-16. Qwen3.6-35B-A3B 2026-04-16 (MoE), Qwen3.6-27B 2026-04-22 (dense). Qwen3.8-2.4T-A95B 2026-08-12, Qwen3.8-27B 2026-08-14. Repo licence Apache-2.0, but weights carry their own licence file. The page lists no small Qwen3.8 variants. — [QwenLM/Qwen3.8 GitHub](https://github.com/QwenLM/Qwen3.8)
- Qwen3.8-27B: dense, 27B, Apache-2.0, 262,144 native context (extensible to 1M), hybrid Gated DeltaNet + Gated Attention layers, vision encoder. Release "August 2026" (collection shows Aug 13). — [HF Qwen/Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B)
- Qwen3.5 small series announced by Alibaba Qwen on X (early March 2026): 0.8B, 2B, 4B, 9B, plus base models. — [Qwen on X](https://x.com/Alibaba_Qwen/status/2028460046510965160)
- Ollama page for qwen3.8 mentions a "qwen3.8-flash-next" entry elsewhere in the library listing (1-2 months old); not verified. — [Ollama library](https://ollama.com/library)

**Google Gemma 4**
- Gemma 4 sizes: E2B, E4B (128K ctx), 12B Unified, 26B A4B (MoE, ~3.8B active), 31B dense (256K ctx). Licence Apache-2.0 (earlier Gemma generations used custom Gemma Terms of Use). — [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4); (page says it is deprecated in favour of gemma.ai)
- Release history: E2B/E4B/31B/26B-A4B on 2026-03-31; 12B Unified on 2026-06-03 (some secondary sources say Apr 2 for launch). — [Gemma releases](https://ai.google.dev/gemma/docs/releases)
- Gemma 3n (e2b, e4b) and Gemma 3 (270m-27b) still in Ollama but older. — [Ollama library](https://ollama.com/library)

**IBM Granite**
- Granite 4.2 (3B, 8B, 30B): dense decoder-only, Apache-2.0, 128K context; stated capabilities include summarization, classification, extraction, QA, RAG, tool use, structured JSON output. Granite 4.1 predates it (3-7 months old per Ollama). — [Ollama granite4.2](https://ollama.com/library/granite4.2)

**Mistral**
- Ministral 3 (3B, 8B, 14B): Apache-2.0, 256K context, text+image; Ollama listing age ~10 months (i.e. late 2025, possibly superseded). Dense/MoE not stated on the page. Also on Ollama: mistral-small3.2 (24B, ~1 yr old), devstral-small-2 (24B), mistral-medium-3.5 (128B, too large), mistral-large-4. No newer Mistral Small found in the listing. — [Ollama ministral-3](https://ollama.com/library/ministral-3), [Ollama library](https://ollama.com/library)

**NVIDIA Nemotron**
- nemotron-3-nano: hybrid Mamba-2/Attention MoE, 30B total / 3.5B active, 128 experts (6 active), NVIDIA Open Model License Agreement (not an OSI licence; read terms). Tags 4b and 30b. Newer entries in the listing: nemotron-3.5-lightning (30B), nemotron-3.5-bolt (e4b), nemotron-cascade-2 (30B). — [Ollama nemotron-3-nano](https://ollama.com/library/nemotron-3-nano), [Ollama library](https://ollama.com/library)

**Others seen in the Ollama listing (sizes only; licence/date NOT verified)**
- Ornith-1.5 (9b, 35b, 122b, 397b; 256K; vision/tools/thinking; maker "Ornith"; base model and licence not stated; updated ~1 week ago). Ornith-1.0 (9b, 35b) earlier. BenchLM ranks Ornith-1.5-9B at 34.04. — [Ollama ornith-1.5](https://ollama.com/library/ornith-1.5)
- gpt-oss 20b/120b (about 3 weeks since last update; ~1 yr old family; OpenAI), olmo-3 / olmo-3.1 (7b, 32b; ~10 months), lfm2.5 (8b, 24b), lfm2.5-thinking (1.2b), lfm3 (1.7b), phi4 / phi4-mini / phi4-reasoning (~1 yr, likely superseded), smollm2 (~1 yr), llama3.1/3.2 (older; llama4 is 16x17b, too large), deepseek-r1 distills (~1 yr), qwen3-vl (2b-32b), qwen3 (0.6b-235b), d3 (2b-27b) and bonsai-2 (2b-27b) unknown makers. — [Ollama library](https://ollama.com/library)
- Newcomers I could not identify the maker of: d1, d3, nimble, clef, flint, phonon, tev1, laya. Do not rely on them.

### Inferences
- Phi, Llama, SmolLM, OLMo, DeepSeek distills appear stale (listed as ~10-12 months since update); likely superseded for this use.
- Qwen3.5-9B-based Heretic fine-tune is on a base that is 8 months old; Qwen3.5-9B has no direct Qwen successor at that size I could find.

### Gaps
- Exact licence text/usage restrictions for Qwen3.5/3.6 weights, Ornith, gpt-oss (Apache-2.0 per my memory only, unverified today), OLMo, LFM (LFM Open License has revenue thresholds per memory, unverified), Nemotron restrictions.
- Releases for Phi, Llama, SmolLM, OLMo, LFM3, DeepSeek in 2026: not searched individually.
- Whether the Heretic/uncensored fine-tune has equivalents for newer bases: not searched.

## Ollama availability, tags, file sizes, VRAM fit

### Takeaway
All main candidates are in the Ollama library with Q4-class defaults. 12 GB: gemma4:12b (7.7-8.0 GB), granite4.2:8b (5.3 GB), qwen3.5:9b (6.6 GB), ministral-3:14b (9.1 GB, tight), nemotron-3-nano:4b. 24 GB: qwen3.8:27b (18 GB), qwen3.6:27b (18-19 GB), gemma4:31b (19-20 GB, tight), gemma4:26b (16-19 GB), granite4.2:30b (18 GB), nemotron-3-nano:30b (18-24 GB).

### Cited Findings
- qwen3.8:27b (=latest) 18 GB, 256K ctx, text+image, tools/thinking. — [Ollama qwen3.8](https://ollama.com/library/qwen3.8)
- qwen3.6: 27b 18-19 GB; 35b 23 GB (latest); 256K ctx. — [Ollama qwen3.6](https://ollama.com/library/qwen3.6)
- qwen3.5: 0.8b 1.2-1.3 GB; 2b 2.7-3.1; 4b 3.3-4.0; 9b 6.6-7.6; 27b 17-20; 35b 22 GB; 256K. — [Ollama qwen3.5 tags](https://ollama.com/library/qwen3.5/tags)
- gemma4: e2b 4.6 GB; e4b q4_K_M 6.6 GB (q8_0 9.0); 12b q4_K_M 8.0 GB, qat 7.2 GB, q8_0 13 GB; 26b-a4b q4_K_M 18 GB, qat 16 GB; 31b q4_K_M 20 GB, qat 19 GB. Contexts: E-models 128K, others 256K. — [Ollama gemma4 tags](https://ollama.com/library/gemma4/tags)
- granite4.2: 3b 2.2 GB; 8b 5.3 GB (Q4_K_M; q8_0 tag exists); 30b 18 GB; 128K. — [Ollama granite4.2](https://ollama.com/library/granite4.2)
- ministral-3: 3b 3.0 GB; 8b 6.0 GB; 14b 9.1 GB; 256K. — [Ollama ministral-3](https://ollama.com/library/ministral-3)
- nemotron-3-nano: 4b 2.8-2.9 GB (256K); 30b 18-24 GB (1M). — [Ollama nemotron-3-nano](https://ollama.com/library/nemotron-3-nano)
- ornith-1.5: 9b 6.6 GB; 35b 23 GB. — [Ollama ornith-1.5](https://ollama.com/library/ornith-1.5)
- BenchLM (page updated 2026-10-29) lists for 8-16 GB machines: Granite 4.2 8B 5.3 GB, Gemma 4 12B 7.7 GB, Ornith-1.5-9B 6.6 GB, Gemma 4 E4B 6.6 GB. — [BenchLM via search summary](https://benchlm.ai/best/ollama-models)

### Inferences
- File size excludes KV cache. As a rule of thumb, a 24 GB card has roughly 3-5 GB headroom for 8-16k context with an 18 GB model (Qwen3.8-27B, Granite 30B); 31B Q4 at 20 GB leaves little, so prefer gemma4:31b-it-qat (19 GB) or 26b-a4b (16 GB, MoE, faster). Qwen3.8/3.5 hybrid DeltaNet layers should keep KV small, but this is untested.
- On 12 GB: 12B at q4/qat (7-8 GB) leaves ample room for 16k. gemma4:12b-it-q8_0 (13 GB) does not fit.
- 35B-A3B models (23 GB) are essentially 24 GB-only with little context headroom.

### Gaps
- No measured VRAM/tokens-per-second figures on an actual 12/24 GB card were found.
- Ollama licence field was not shown for qwen3.x/ornith pages.

## Thinking/reasoning mode: can it be switched off, and how

### Takeaway
Most newer models think by default and can be disabled; in Ollama use the `think: false` API field / `/set nothink` (Ollama-supported for models tagged "thinking"). Model-native switches are given below.

### Cited Findings
- Qwen3.8-27B: on by default; off via `chat_template_kwargs: {enable_thinking: False}`; `reasoning_effort` tunes depth; non-thinking recommended sampling temp 0.7, top_p 0.8, top_k 20, presence_penalty 1.5. — [HF Qwen/Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B)
- Qwen3.8 on Ollama: "thinking is on by default and can be turned off per request"; `reasoning_effort`, `preserve_thinking`. — [Ollama qwen3.8](https://ollama.com/library/qwen3.8)
- Qwen3.6 on Ollama: tagged thinking; no explicit off switch described on page. — [Ollama qwen3.6](https://ollama.com/library/qwen3.6)
- Gemma 4: thinking enabled by including `<|think|>` at the start of the system prompt; disabled by omitting it (larger models still emit an empty thought block). Sampling temp 1.0, top_p 0.95, top_k 64. — [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)
- Granite 4.2: `enable_thinking` default true; `reasoning_effort` low/high; in Ollama `/set nothink` or `/set think low|medium|high`; API `"think"`. — [Ollama granite4.2](https://ollama.com/library/granite4.2)
- Nemotron-3-nano: reasoning on by default, configurable via a chat-template flag (name not given on page); off gives slight accuracy drop on hard prompts. — [Ollama nemotron-3-nano](https://ollama.com/library/nemotron-3-nano)
- Ministral-3 on Ollama: no thinking variants listed. — [Ollama ministral-3](https://ollama.com/library/ministral-3)

### Inferences
- For extraction/classification, run with thinking off (Granite 4.2, Gemma 4, Qwen3.8 all support this); expect accuracy loss on judgment calls, so test low-effort thinking on the yes/no step. Artificial Analysis shows the gap for Qwen3.8-27B (below).

### Gaps
- Ornith-1.5 and Qwen3.5 off-switch details not confirmed from a primary page (Qwen3.5's is believed similar to Qwen3's; not verified).

## Independent leaderboards and vendor-reported numbers

### Takeaway
Independent data found is thin. Artificial Analysis (Intelligence Index v5.0) scores Qwen3.8-27B 10 (non-reasoning) to 20 (Xhigh) and had no score on the page for Gemma 4 12B, Granite 4.2 30B or gpt-oss-20b. Other "rankings" found are SEO aggregators of unclear method.

### Cited Findings
**Independent**
- Artificial Analysis, open-weights small-model page, Intelligence Index v5.0 (no page date; fetched 2026-10-31): Qwen3.8 27B Xhigh 20, Medium 17, Low 16, Non-reasoning 10; Muse Glimmer (High, 30B) 6; Nemotron 3.5 Lightning (31.6B, 3.6B active) 6. Listed without a score: Gemma 4 12B (non-reasoning), Granite 4.2 30B, gpt-oss-20b (Low), K2 Horizon 7B, North Mini Code, Qwen3 Omni 30B A3B. — [Artificial Analysis](https://artificialanalysis.ai/models/open-source/small)
- BenchLM (aggregator; updated 2026-10-29): Granite 4.2 8B 36.78, Gemma 4 12B 35.48, Ornith-1.5-9B 34.04, Gemma 4 E4B 26.58. Methodology not retrieved; unclear whether independent. — [BenchLM](https://benchlm.ai/best/ollama-models) (numbers via search-tool summary)
- WhatLLM (editorially updated July 2026) picks Qwen3.6 27B for general use at 16-24 GB, Gemma 4 12B and Qwen3 8B/14B for 8-16 GB; attributes data to Artificial Analysis; gives no numbers. — [WhatLLM](https://whatllm.org/best-ollama-models)

**Vendor-reported (not independent)**
- Gemma 4 (instruction-tuned): MMLU-Pro 85.2 (31B), 82.6 (26B A4B), 77.2 (12B), 69.4 (E4B), 60.0 (E2B); GPQA Diamond 84.3 / 82.3 / 78.8 / 58.6 / 43.4; LiveCodeBench v6 80.0 / 77.1 / 72.0 / 52.0 / 44.0; Gemma 3 27B (no think) MMLU-Pro 67.6. — [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)
- Qwen3.8-27B: GPQA Diamond 89.2, LiveCodeBench v6 90.3, IFBench 79.5, HLE 30.8 (Qwen-reported). — [HF Qwen/Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B)
- Qwen3.5-4B: MMLU-Pro 79.1, GPQA Diamond 76.2 (vendor-reported, relayed by a low-quality SEO guide). — [search summary of promptquorum.com](https://www.promptquorum.com/local-llms/small-local-llm-models)

### Inferences
- AA's Qwen3.8-27B reasoning vs non-reasoning gap (20 vs 10) suggests thinking matters a lot on general intelligence tasks; extraction latency vs accuracy needs a task-specific eval on this tool's own data.
- Vendor MMLU-Pro/GPQA numbers say little about structured extraction or citation faithfulness; none of the sources measured those.

### Gaps
- Not retrieved: LMArena, LiveBench, Open LLM Leaderboard (retired), Epoch, Vals, Hugging Face leaderboards; AA scores for Gemma 4 26B/31B, Granite 4.2 8B, Qwen3.5-9B, Ornith (the AA page was filtered to "small" and did not show them).
- No extraction/classification/JSON-faithfulness or hallucination benchmarks found for any candidate; IFBench only for Qwen3.8 (vendor).
- BenchLM method and the SEO sites (promptquorum, localaimaster, huggingface.co/blog/daya-shankar) are low-confidence; one SEO page cited a "Qwen3.8-27B" alongside unverifiable versions.

## Suggested shortlist to test (derived, not sourced as a recommendation)
- 12 GB: gemma4:12b (qat 7.2 GB), granite4.2:8b (5.3 GB), qwen3.5:9b (baseline), ornith-1.5:9b (licence unknown).
- 24 GB: qwen3.8:27b (18 GB), gemma4:26b-a4b-it-qat (16 GB), gemma4:31b-it-qat (19 GB), granite4.2:30b (18 GB).
