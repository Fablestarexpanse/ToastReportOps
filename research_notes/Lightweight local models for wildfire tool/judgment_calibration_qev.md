# Calibrated small-model judgment classification and triage (incl. labqoat qev-0.8b), as of 2026-10-31

## qev-0.8b by labqoat: what it is and how far to trust it

### Takeaway
qev-0.8b is a real, Apache-2.0, openly downloadable 0.8B "decision model" (Qwen3.5-0.8B-Base full fine-tune) that outputs probabilities for yes/no, pick-one and ordinal questions in one forward pass, and it matches the shape of the wildfire tasks. Every performance number is vendor-run, the company is opaque, and independent corroboration is essentially absent, so treat it as a candidate to test on your own labelled data, not a proven component.

### Cited Findings
- Architecture and base: full fine-tune of Qwen3.5-0.8B-Base (~0.9B params, BF16 safetensors). It "does not generate text"; it takes a state (text/JSON/image) plus typed questions and returns calibrated probabilities in one forward pass — [HF model card](https://huggingface.co/labqoat/qev-0.8b); [app page](https://app.labqoat.com/e/qev/qev/)
- Question types: `choice` (pick one, up to 255 options), `noul` (yes/no, returns P(yes)), `score` (ordinal; expected score + distribution) — [HF model card](https://huggingface.co/labqoat/qev-0.8b)
- Training data (as disclosed): ~2.4B tokens of decision data; roughly half from public datasets, about a third program-generated, remainder written/labelled by larger models; ~10% share-alike, <1% research-use only. No corpus list or training recipe disclosed — [HF model card](https://huggingface.co/labqoat/qev-0.8b). A community discussion "Training data availability?" (#1, opened ~10 days before fetch, body not visible, no replies visible) is the only discussion — [HF discussions](https://huggingface.co/labqoat/qev-0.8b/discussions)
- Licence: Apache-2.0 (model card says ~10% of training rows are share-alike; implications for the licence are not discussed) — [HF model card](https://huggingface.co/labqoat/qev-0.8b)
- Calibration method: probabilities are a softmax over each question's options, sharpened by per-type temperatures (choice 1.7, noul 0.9, score 1.25) fitted on the authors' own calibration split; "not universally calibrated"; authors advise recalibrating on your own labelled data when probabilities drive decisions — [GGUF card](https://huggingface.co/labqoat/qev-0.8b-GGUF)
- Running it: native Python runner (`QEV.from_pretrained`, CUDA fast engine, `qev_server.py` serving `/v1/systemone` on port 8017); MLX build (~1.3 GB at 8-bit, ~109 ms on M4 Pro per card); GGUF build (~17 ms on M4 Pro per card) — [HF model card](https://huggingface.co/labqoat/qev-0.8b), [app page](https://app.labqoat.com/e/qev/qev/)
- Ollama / llama.cpp: stock `llama-server -hf labqoat/qev-0.8b-GGUF:Q8_0` serves a custom `POST /v1/systemone` endpoint (needs llama.cpp b11622+; b11364–b11621 shift answers by ~0.02). `ollama pull hf.co/labqoat/qev-0.8b-GGUF:Q8_0` then call `/v1/systemone` on port 11434; card says tested on Ollama 0.40.4. It is NOT used via the normal chat/completions route. Custom GGUFs need `add_systemone_metadata.py` first. llama-server output omits `x_p_max` and `certainty`, which the Python `qev_gguf.py` (llama-cpp-python 0.3.36, `decider-ai==1.9.0`) includes — [GGUF card](https://huggingface.co/labqoat/qev-0.8b-GGUF)
- Input length: not stated on either page — [HF model card](https://huggingface.co/labqoat/qev-0.8b), [app page](https://app.labqoat.com/e/qev/qev/)
- Images: zero-shot via base vision tower, resized to ≤448×448 (≤196 tokens); GGUF needs mmproj; weak on board games/counting — [HF model card](https://huggingface.co/labqoat/qev-0.8b), [GGUF card](https://huggingface.co/labqoat/qev-0.8b-GGUF)
- Hosted API: app page shows single POST (state + up to 16 questions) and /batch (up to 64 requests per forward pass); no pricing, rate limits, or usage terms on the page. labqoat.com lists no terms of service (only Google reCAPTCHA terms) — [app page](https://app.labqoat.com/e/qev/qev/), [labqoat.com](https://labqoat.com)
- Vendor-reported benchmarks (labqoat ran them): Benchmark Heaven 77.1% ±2.8 pts (231 items), "first of 27" models of ~1B, using labqoat's harness while others used a comparison harness; p50 3.5 ms on one L40S; JevExtra 72.0%, JevSpatial 74.2%; BH ECE 0.063; JevBench 85.1; JevVision v0 0.759 vs 0.651 base; 2.2 GB VRAM is an estimate, not a measurement — [app page](https://app.labqoat.com/e/qev/qev/), [HF model card](https://huggingface.co/labqoat/qev-0.8b). The "Jev*" benchmarks appear to be labqoat-associated (squad card calls games_bench "the team's own benchmark"); I could not establish who built Benchmark Heaven.
- Stated limitations: `score` weakest; accuracy drops on long/hard inputs, no reasoning; related questions not guaranteed consistent; rarely picks "cannot answer" options; mostly English business/agent-log data evaluated; GGUF slightly less calibrated than transformers on images; not for sole decisions on consequential matters about people — [GGUF card](https://huggingface.co/labqoat/qev-0.8b-GGUF)
- Popularity: 16 likes, 545 downloads last month, 1 discussion at fetch time — [HF model card](https://huggingface.co/labqoat/qev-0.8b)
- squad-0.8b sibling: "MoQLE" — shared trunk (full fine-tune of Qwen3.5-0.8B-Base, 24 layers), router after layer 5, four experts (games, e2e web-app testing, multilingual, documents) as rank-64 2-bit LoRAs (~2.7 MB each), RL-trained with frozen trunk. Apache-2.0. Benchmarks run by labqoat with own client; caveats include BH ±1 point run variance, Rules-7 gains partly from similar procedurally generated data, games_bench is team's own, image input not re-evaluated. Needs `specialists=True` + CUDA engine for experts; MLX and GGUF (llama.cpp b11786+) builds exist. None of the four experts targets news/events — [squad-0.8b card](https://huggingface.co/labqoat/squad-0.8b)
- Company: labqoat.com describes "The AI lab for agent swarms" (platform, SDK/CLI); no founders, staff, location, contact, entity name or pricing; waitlist "opens soon"; CLI guide under GitHub org qforge-dev — [labqoat.com](https://labqoat.com)
- Similarly named but separate project: AustinFu/Qev-0.8B (GitHub QiqianFu/Qev) — Qwen3.5-0.8B-Base + rank-64 LoRA + decision head, distilled from its own "Qev-9B v0.3.0" teacher on 44,576 inputs; reports JevBench public 73.16% (169/231), WANLI 66.02%, MMLU-Pro 24.70%. Its page does not mention labqoat, so the relationship is unestablished. Notably it reports the same 231-item "JevBench public" set, so that benchmark is at least used by a second party — [AustinFu card](https://huggingface.co/AustinFu/Qev-0.8B)

### Inferences
- The interface (choice / yes-no / ordinal with probabilities) maps directly to "new vs old", "still burning?", "which of three incidents"; the GGUF route means it could run in Ollama via a custom endpoint, but any code already using OpenAI-style chat+logprobs would need rewriting for `/v1/systemone`.
- 77.1% on a general decision benchmark says little about wildfire-news tasks; ECE 0.063 is on the authors' own calibration distribution, which the card itself says may not transfer. Expect to recalibrate (temperature/isotonic) on a few hundred labelled wildfire items.
- Provenance risk: no named team, no terms, 1 unanswered training-data question, a very new HF repo with modest downloads. Apache-2.0 weights reduce lock-in risk (can be pinned via the published SHA256SUMS), but there is no basis to assume maintenance.
- The input-length unknown matters for long incident reports; test before relying on it.

### Gaps
- No independent evaluation, reproduction, blog post, paper, or GitHub issue found beyond the one HF discussion; the web search returned only the HF page and the AustinFu project.
- Could not verify the company's identity or the "first of 27" comparison; competitor list and Benchmark Heaven provenance not seen.
- Context length, training recipe, exact data sources, and hosted-API terms/pricing not disclosed in what I fetched.
- I did not fetch the MLX card separately or any GitHub repo; fetch tool summaries (not raw pages) were used, so exact wording may differ.

## Calibration of small LLMs: logprobs vs verbalised confidence; API support

### Takeaway
Published evidence consistently says RLHF/instruction tuning hurts calibration of token probabilities and verbalised confidence, that verbalised numbers from small models are poor, and that post-hoc recalibration (Platt/temperature/isotonic) on a labelled set is the standard fix. I found little rigorous 1B–14B-specific data in this pass, and Ollama logprobs support is documented from v0.12.11 but needs verification against current docs.

### Cited Findings
- Tian et al. (arXiv 2305.14975): for several RLHF models verbalised confidence was better calibrated than the model's token log probabilities, and RLHF generally worsens Llama-70B log-prob calibration (ECE) — [arXiv 2305.14975](https://arxiv.org/pdf/2305.14975v1) (via search summary; models tested are large)
- ICLR 2025 paper (arXiv 2410.09724): RLHF-trained models show more verbalised overconfidence than pre-RLHF counterparts, partly due to reward-model bias toward confident answers; a PPO-based fix reduced ECE on Llama3-8B with comparable accuracy — [arXiv 2410.09724](https://arxiv.org/pdf/2410.09724v1)
- Practitioner guidance: Platt scaling suited to small calibration sets, isotonic regression beat it on ECE in one multi-dataset comparison — [KDnuggets](https://www.kdnuggets.com/a-deep-dive-into-calibration-of-language-models-platt-scaling-isotonic-regression-temperature-scaling) (secondary, weak)
- Ollama: release notes for v0.12.11-rc1 state Ollama's API and OpenAI-compatible API "now supports Logprobs" — [release notes mirror](https://newreleases.io/project/github/ollama/ollama/release/v0.12.11-rc1); a third-party reference claims logprobs are unsupported on the compat layer — [developersdigest](https://www.developersdigest.tech/reference/api-compatibility/ollama) (undated, unofficial; likely stale)
- llama.cpp: commit #10783 "server : fix logprobs, make it OAI-compatible" returns `completion_probabilities` with `top_logprobs` bounded by `n_probs` and adds `post_sampling_probs` — [commit mirror](https://cdn04132025.gitlink.org.cn/replica/llama.cpp/commit/57bb2c40cd94c5a09f5210ed8264cc93b21c4b7e). A May 2024 llamafile issue reported `/v1/chat/completions` ignoring logprobs/top_logprobs — [llamafile #409](https://github.com/Mozilla-Ocho/llamafile/issues/409) (old; current status unverified)

### Inferences
- Reading P("Yes") vs P("No") from a single constrained next-token position (normalised over the two tokens) is the cheapest lightweight signal, but should be treated as an uncalibrated score and recalibrated on labelled local data; verbalised confidence from sub-14B instruct models should not be relied on (supported by RLHF-overconfidence findings, though not measured on small models in what I found).
- Because Ollama logprobs only appear from ~v0.12.11 (late 2025), pin Ollama >= that version and test; llama-server's native `/completion` with `n_probs` is the more established route.

### Gaps
- No primary study of ECE/Brier for 1B–14B instruct models head-to-head (logprob vs verbalised) located; the blog-reported ECE numbers (e.g., 0.377 verbalised) lack primary sources and are excluded.
- Final v0.12.11 release date and current official Ollama docs wording not verified; current llama.cpp chat-endpoint logprobs behaviour not verified.

## Encoder classifiers vs small generative models (ModernBERT, DeBERTa-v3, NLI zero-shot, SetFit)

### Takeaway
Fine-tuned encoders are the standard fast, cheap triage option; DeBERTa-v3 is the more accurate/sample-efficient base, ModernBERT is faster and handles 8k context, and SetFit gives few-shot training in minutes. I could only partly source the quantitative trade-offs.

### Cited Findings
- Controlled comparison (Antoun et al., arXiv 2504.08716): trained on the same data, DeBERTaV3 beats ModernBERT in overall benchmark performance and sample efficiency except on retrieval; ModernBERT's main advantage is faster training and inference — [arXiv 2504.08716](https://arxiv.org/html/2504.08716v2)
- ModernBERT authors report base beats DeBERTaV3-base on GLUE, supports 8192-token context, and is ~2x (up to 4x on mixed lengths) faster — [ModernBERT write-up via search](https://levelup.gitconnected.com/after-6-years-google-bert-finally-has-a-replacement-39351ca2b478) (secondary; vendor-side)
- SetFit: prompt-free few-shot method that contrastively fine-tunes a Sentence Transformer on small pair sets then trains a classification head; competitive with PEFT/PET at an order of magnitude less training cost; SetFit RoBERTa-355M beat PET and GPT-3 on RAFT — [SetFit paper](https://huggingface.co/papers/2209.11055), [HF blog](https://huggingface.co/blog/setfit)
- A zero-shot ModernBERT-large NLI-style model exists ("modernbert-large-zeroshot-v2.0") — [PromptLayer listing](https://www.promptlayer.com/models/modernbert-large-zeroshot-v20) (listing only, no numbers read)

### Inferences
- For "is this about a California wildfire at all?" a fine-tuned encoder (or SetFit) with a few hundred labels is likely sufficient and far cheaper than any LLM; its softmax is also easy to calibrate. For pairwise "same incident?" cross-encoder pair classification is the encoder analogue.
- Encoders cannot reason over long, multi-fact text as well as LLMs; keep them for the high-volume first stage.

### Gaps
- No sourced latency/VRAM figures, labelled-data requirements (beyond SetFit's few-shot claim), or NLI zero-shot (bart-large-mnli, DeBERTa NLI) accuracy numbers; I did not search them. Treat any figures from memory as unverified.

## LLMs for event dedupe and old-vs-new temporal judgment

### Takeaway
The literature suggests LLMs alone under-perform supervised small models on cross-document event coreference, hybrids help, and models tend to default to outdated information; nothing directly evaluates wildfire or news "new vs old" classification.

### Cited Findings
- Min et al. (arXiv 2406.02148): LLM-generated event summaries used to fine-tune a small LM beat LLM-only and SLM-only on three CDEC datasets; LLMs trail supervised SLMs since a few demonstrations cannot cover annotation guidelines — [arXiv 2406.02148](https://ar5iv.labs.arxiv.org/html/2406.02148)
- arXiv 2404.01921: zero-shot CoT pairwise coreference judgments; smaller models often produce outputs hard to parse into labels — [arXiv 2404.01921](https://arxiv.org/pdf/2404.01921)
- Zhao et al. (ACL 2025, "Beyond Benchmarks"): existing CDEC datasets mostly cover Wikipedia-worthy events and don't reflect news distributions — [arXiv 2502.12509](https://arxiv.org/abs/2502.12509v4) (as summarised by search)
- Temporal: DyKnow found models give outdated answers; a 2025 knowledge-conflict study found LLMs give outdated answers even when they can identify the current one; UnSeenTimeQA found struggles with long event-dependency chains and parallel timelines; EvolveBench tests temporal awareness — [DyKnow](https://arxiv.org/html/2404.08700v1), [arXiv 2510.19172](https://arxiv.org/pdf/2510.19172v1), [UnSeenTimeQA](https://www.arxiv.org/pdf/2407.03525v3), [EvolveBench](https://preview.aclanthology.org/ingestion-acl-25/2025.acl-long.788.pdf)

### Inferences
- "New vs old" should not be left to model world knowledge: supply explicit timestamps (report date, incident start date, last-update time) in the prompt, or compute date arithmetic outside the model, and use the model only for textual judgments.
- "Which incident does this fact belong to" is a pick-one/coreference task where supplying incident names, locations and dates as options plus an explicit "none/new incident" option is sensible, especially since qev's card says it rarely picks "cannot answer".

### Gaps
- No studies on LLM news deduplication at scale, wildfire reports, or "still burning" status inference; the benchmarks above are only indirectly relevant. Results seen via search summaries, not full papers.

## Recommended design: cascade / routing with a calibrated threshold

### Takeaway
Evidence supports a two-stage cascade: cheap model scores, a calibrated confidence decides auto-handle vs escalate to a larger model or human, with the threshold set on held-out labelled data. Reported savings are workload-specific preprints.

### Cited Findings
- ICML 2026 "Calibrated Deferral Routing for Cost-Efficient Guardrails": 2B-to-8B cascade within ~1 pt of the 8B alone with 55% lower inference cost — [ICML page](https://icml.cc/virtual/2026/67782) (self-reported)
- UCCI (arXiv 2605.18796): token-margin aggregation + isotonic regression gives calibrated error probabilities, ECE 0.03 on its workload; claims to beat a FrugalGPT-style learned threshold — [arXiv 2605.18796](https://arxiv.org/html/2605.18796v1) (via search summary)
- Conformal Cascade (arXiv 2607.25018): accept only when conformal prediction set is a singleton, for distribution-free accuracy guarantees; notes 0.9 confidence does not mean 90% success — [alphaxiv](https://www.alphaxiv.org/abs/2607.25018v3)
- Language Model Cascades: Token-level uncertainty (arXiv 2404.10136) and AutoRelAnnotator (arXiv 2606.25871, notes temperature/Platt/isotonic/histogram binning as common calibration options) — [2404.10136](https://arxiv.org/pdf/2404.10136), [2606.25871](https://arxiv.org/pdf/2606.25871)

### Inferences
- Practical recipe: (1) encoder or small model for the topic filter; (2) small judgment model (qev-0.8b if it validates, or a 3–8B instruct model's constrained Yes/No logprob) producing p; (3) fit isotonic/Platt on 300–1000 labelled wildfire items; (4) choose two thresholds from a precision–coverage curve: auto-accept if p>=hi or p<=lo, else escalate to a larger model, then to human review; (5) monitor drift with periodic labelled samples since calibration was fit on a specific distribution.
- Conformal/risk-controlled thresholds give a defensible error guarantee if the operator needs one.

### Gaps
- All cascade results are recent preprints/self-reported on non-wildfire tasks, via search summaries; none independently replicated that I found.
- Did not verify FrugalGPT original numbers.
