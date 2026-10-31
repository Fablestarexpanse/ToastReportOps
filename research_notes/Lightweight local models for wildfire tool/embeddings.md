# Text-embedding options for near-duplicate detection and cross-source corroboration of short wildfire/flood snippets (as of 2026-10-31)

Method note: ~20 tool calls (web search and fetch). The live MTEB leaderboard (huggingface.co/spaces/mteb/leaderboard) did not render through the fetch tool, so no leaderboard ranks were read first-hand. Model-card numbers below are vendor-reported unless marked. Several page extractions were made by a summarising fetch tool, not read verbatim; treat exact figures as "as extracted" and re-check on the cited page before committing to a decision.

## 1. Which models are credible and runnable locally (Ollama / llama.cpp / sentence-transformers)?

### Takeaway
Ollama's embedding library now lists nomic-embed-text (v1.5), nomic-embed-text-v2-moe, bge-m3, bge-large, mxbai-embed-large, snowflake-arctic-embed(2), granite-embedding, all-minilm, qwen3-embedding (0.6b/4b/8b), embeddinggemma and a new embeddinggemma-2 (270m-740m). Newer top-of-leaderboard models (Microsoft harrier-oss, jina-embeddings-v5) exist but I found no Ollama tag for them (jina v5 has GGUF on HF for llama.cpp, with a non-commercial licence on at least the nano repo).

### Cited Findings
Ollama library listing (pull counts at time of fetch; descriptions as extracted) — [Ollama embedding search](https://ollama.com/search?c=embedding):
- nomic-embed-text 94.6M pulls; nomic-embed-text-v2-moe 1.2M; qwen3-embedding (0.6b, 4b, 8b) 4.5M; mxbai-embed-large (335m) 16.4M; bge-m3 (567m) 8.1M; embeddinggemma (300m) 2.7M; all-minilm (22m, 33m); snowflake-arctic-embed (22m-335m); snowflake-arctic-embed2 (568m); granite-embedding (30m English, 278m multilingual); bge-large (335m); paraphrase-multilingual (278m); embeddinggemma-2 (270m, 440m, 570m, 740m; 107.7K pulls). No jina or harrier entries appeared in this listing.

Per-model details:
- **nomic-embed-text (Ollama)**: `latest` = v1.5, 274MB, listed 2K context. — [Ollama page](https://ollama.com/library/nomic-embed-text)
- **nomic-embed-text-v1.5 (HF card)**: ~137M params, 768 dims, Matryoshka sizes 512/256/128/64, max seq 8192 (needs RoPE scaling settings), Apache-2.0, required prefixes `search_document`, `search_query`, `clustering`, `classification`. MTEB avg: 62.28 (768d), 61.96 (512), 61.04 (256), 59.34 (128), 56.10 (64); v1 = 62.39. Vendor-reported. — [HF card](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5)
- **nomic-embed-text-v2-moe**: 475M total / 305M active (8 experts, top-2), 768d, Matryoshka down to 256, max seq 512 tokens, ~100 languages, Apache-2.0, prefixes `search_query:` / `search_document:`. Vendor BEIR 52.86, MIRACL 65.80 (vs BGE-M3 48.8 / 69.2; Arctic-Embed v2 Base 55.40 / 59.90). Trained on 1.6B pairs; card is silent on test-set exclusion. — [HF card](https://huggingface.co/nomic-ai/nomic-embed-text-v2-moe)
- **qwen3-embedding**: Ollama tags latest(=8b), 0.6b (639MB, 32K ctx), 4b (2.5GB, 40K), 8b (4.7GB, 40K); dims up to 4096 for 8b, user-set 32-4096. — [Ollama page](https://ollama.com/library/qwen3-embedding) (Ollama page itself shows 32K/40K inconsistently)
- **Qwen3-Embedding HF card**: 0.6B max dim 1024, 4B 2560, 8B 4096; MRL supported by all; instruction-aware (queries get `Instruct: {task}\nQuery:{query}`, documents none); Apache-2.0; 32K context. Vendor MTEB: Multilingual mean-task 64.33 / 69.45 / 70.58; Eng v2 70.70 / 74.60 / 75.22; Multilingual STS 76.17 / 80.86 / 81.08; Eng v2 STS column 86.57 / 88.72 / 88.58. — [HF 0.6B card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- **embeddinggemma-2** (Google, Gemma 4 base): 740M total (270M text + 170M vision + 300M audio encoders; selectively loadable), 768d native, Matryoshka 512/256/128, 8,192 tokens, Apache-2.0. Vendor MTEB English v2 68.46, Multilingual v2 61.36 (v1: 61.15), Code 78.68. Prompt formats: docs `title: {title} | text: {content}` (`title: none` if absent); clustering/classification/STS `task: {type} | query: {content}`; fact checking `task: fact checking | query: {claim}`. Re-normalise after truncation. — [HF card](https://huggingface.co/google/embeddinggemma-2)
- **embeddinggemma-2 on Ollama**: tags 270m (0.30-0.38GB), 440m (~0.7GB), 570m (~0.95GB), 740m/latest (1.3GB); the 270m and 570m are text-only; Ollama page recommends 128d for text-only, minimal loss to 256d. — [Ollama page](https://ollama.com/library/embeddinggemma-2)
- **microsoft harrier-oss-v1** (270M, 0.6B, 27B): MIT licence, decoder-only, last-token pooling, L2-normalised, 32,768 max tokens per HF card; vendor Multilingual MTEB v2 66.5 / 69.0 / 74.3. 0.6B distilled from larger members. No Ollama tag found. — [HF 0.6B](https://huggingface.co/microsoft/harrier-oss-v1-0.6b), [HF 27B](https://huggingface.co/microsoft/harrier-oss-v1-27b), [Foundry blog](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/now-in-foundry-microsoft-harrier-and-nvidia-egm-8b/4510851)
- **jina-embeddings-v5-text** (nano, small; released from Feb 2026): GGUF builds on HF for llama.cpp; nano text-matching GGUF repo lists CC-BY-NC-4.0 (commercial use needs contacting Jina); small-retrieval licence not verified. Ollama availability not found. — [arXiv 2602.15547](https://arxiv.org/html/2602.15547v1), [nano GGUF repo](https://hf-cn0.pages.dev/jinaai/jina-embeddings-v5-text-nano-text-matching-GGUF)
- **IBM granite-embedding-english-r2**: 149M, ModernBERT, 768d, 8192 tokens, Apache-2.0; MTEB-v1 retrieval 53.0, MTEB-v2 retrieval 59.5 (repo). Small r2: 47M, 384d. Ollama tag shows only 30m/278m (older generation, not r2). — [HF card](https://huggingface.co/ibm-granite/granite-embedding-english-r2), [GitHub](https://github.com/ibm-granite/granite-embedding-models/)
- bge-m3 (Ollama): 567m, dense 1024d, 8192 tokens, 100+ languages. Secondary sources conflict on whether sparse/ColBERT outputs are reachable via Ollama; one says only dense 1024d via `/api/embed`. — [morphllm](https://www.morphllm.com/ollama-embedding-models)
- Roundups (secondary, October 2026) say harrier-oss-v1-27b, Octen-Embedding-8B, Qwen3-Embedding-8B and QZhou-Embedding lead open-weight boards, and recommend Qwen3-Embedding-0.6B or EmbeddingGemma for small hardware; NV-Embed-v2 is non-commercial. — [supermemory](https://supermemory.ai/blog/best-open-source-embedding-models-benchmarked-and-ranked/), [futureagi](https://futureagi.com/blog/best-embedding-models-2025/), [maximem](https://www.maximem.ai/blog/best-embedding-models-2026)

### Inferences
- For a 768d, small-VRAM, Ollama-native setup, the realistic candidates are embeddinggemma-2 (270m/440m, native 768d, 8K ctx, Apache), embeddinggemma v1 (300m), qwen3-embedding:0.6b (1024d, MRL-truncatable) and nomic-embed-text-v2-moe. Big models (8B, 27B) are overkill for snippets of 1-3 sentences.
- nomic-embed-text v1.5 is an early-2024 model; it is the oldest and weakest of this set on MTEB-style averages (62.3 vs Qwen3-0.6B Eng v2 70.7 vs EmbeddingGemma-2 68.5), though those scores come from different MTEB versions (v1 vs v2) and are not directly comparable.

### Gaps
- No first-hand MTEB leaderboard read (page did not render); jina v5 and bge newer-gen (e.g. bge-en-icl, bge-multilingual-gemma2), GTE newer, mxbai, Arctic specs/licences were not individually verified here. Ollama tags for harrier and jina v5 not found (absence not proven).
- VRAM numbers are only disk sizes from Ollama; no measured VRAM or throughput benchmarks.

## 2. What do independent benchmarks say (MTEB, STS, clustering, pair classification, MMTEB, duplicate evidence, contamination)?

### Takeaway
Nearly all scores found are vendor-reported model-card numbers on MTEB v1/v2/Multilingual v2, which are not comparable across versions. I found no independent benchmark on short disaster/news snippet near-duplicate detection; the MTEB pair-classification/STS sub-scores are the closest proxy, and I did not retrieve per-task values for most candidates.

### Cited Findings
- Vendor-reported (not independently measured): Qwen3-Embedding 0.6B/4B/8B Eng v2 mean 70.70/74.60/75.22 and Multilingual 64.33/69.45/70.58 — [Qwen card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B); EmbeddingGemma-2 English v2 68.46, Multilingual v2 61.36 — [Google card](https://huggingface.co/google/embeddinggemma-2); nomic v1.5 MTEB(v1) 62.28 — [HF](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5); harrier 66.5/69.0/74.3 Multilingual v2 — [HF](https://huggingface.co/microsoft/harrier-oss-v1-0.6b).
- Roundup sites mix MTEB versions and warn scores across English v2 / Multilingual v2 are not comparable; some quote stale June-2025 numbers (e.g. Qwen3-8B 70.58) — [cognee](https://www.cognee.ai/best-embedding-models), [premai](https://www.premai.io/blog/best-embedding-models-for-rag-2026-ranked-by-mteb-score-cost-and-self-hosting/).
- Contamination: MTEB maintainers publish a "zero-shot" score based on disclosed training data overlap (e.g. e5-mistral-7b-instruct 95% zero-shot on MTEB Eng v2, i.e. ~5% overlap); it is coarse and cannot catch undisclosed overlap — [Maintaining MTEB, arXiv 2506.21182](https://arxiv.org/pdf/2506.21182). A 2026 paper (HTEB) says the metadata only captures known overlap — [arXiv 2605.28190](https://arxiv.org/pdf/2605.28190). A vendor (ZeroEntropy) suggests a 15+ NDCG drop from MTEB to private data signals contamination; this is a heuristic, not validated — [ZeroEntropy](https://zeroentropy.dev/concepts/mteb/).
- Dedupe evidence: on NEWS-COPY, bi-encoder + cross-encoder re-rank clustering got ARI 93.7 vs 91.5 bi-encoder alone vs 75.0 n-gram overlap (long-ish historical newspaper articles, not short snippets) — [Noise-Robust De-Duplication at Scale](https://arxiv.org/pdf/2210.04261).

### Inferences
- Because top-of-board gaps (a few MTEB points) are small relative to threshold and normalisation effects, and models were possibly trained on MTEB-adjacent data, a small hand-labelled in-domain test set (a few hundred pairs of real snippets) is the only reliable way to choose.
- Qwen3-0.6B and EmbeddingGemma-2 report higher vendor scores than nomic v1.5, so a gain is likely but its size for this task is unknown.

### Gaps
- Not found: per-task PairClassification/STS/Clustering numbers for nomic v1.5, EmbeddingGemma-2, harrier; any independent paraphrase/near-duplicate benchmark on disaster-news text; the MMTEB paper numbers.

## 3. Sensitivity to model vs threshold vs normalisation; threshold guidance for short noisy text

### Takeaway
No source quantifies model-vs-threshold sensitivity. Practitioner guidance is to calibrate the threshold on labelled pairs from your own model, using a middle "review" band; a news API uses cosine 0.95 for duplicates.

### Cited Findings
- A news dedupe API flags pairs with cosine >= 0.95 as potential duplicates and keeps dedupe distinct from lower-threshold topical clustering — [NewsCatcher docs](https://www.newscatcherapi.com/docs/news-api/guides-and-concepts/articles-deduplication.md).
- distilabel's EmbeddingDedup uses 0.9 as a default cosine threshold and says the right value depends on how embeddings were built — [distilabel](https://distilabel.argilla.io/dev/components-gallery/steps/embeddingdedup/).
- A patent source suggests treating near-duplicates as "flag for review" rather than auto-reject — [USPTO 12579107](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12579107).
- Short news items (~40 words) studied with TF-IDF for semantic duplicates — [HSE paper](https://bijournal.hse.ru/article/view/26078).
- Prefix omission is reported to degrade nomic retrieval (secondary source) — [localaimaster](https://localaimaster.com/models/nomic-embed-text); HF card says prefixes are required — [nomic v1 GGUF card](https://huggingface.co/nomic-ai/nomic-embed-text-v1-GGUF/blob/main/README.md).

### Inferences
- Thresholds do not transfer between models (score distributions differ per model, prefix choice and truncation dimension), so any model switch forces re-calibration; thresholds of 0.9-0.95 from the sources are starting points only.
- Normalisation likely matters for noisy radio/social text (strip URLs, hashtags, "BREAKING", timestamps, boilerplate; keep numbers and place names), but I found no source measuring it.

### Gaps
- No published calibration procedure or measurements for short noisy disaster text; sensitivity ranking (model vs threshold vs normalisation) is unsupported by evidence found.

## 4. Migration mechanics (sqlite-vec, dimension, Matryoshka, Ollama gotchas)

### Takeaway
A vec0 column has fixed `float[N]`; changing model or dimension means creating a new vec0 table and re-embedding every fact. Vectors from different models are not comparable, even at the same 768 dimension, so Matryoshka truncation to 768 does not avoid re-embedding.

### Cited Findings
- vec0 columns are declared like `contents_embedding float[768]`; vec0 supports metadata, partition-key and auxiliary (+) columns — [sqlite-vec vec0 docs](https://alexgarcia.xyz/sqlite-vec/features/vec0.html).
- Distance metric is chosen at CREATE (`distance_metric=cosine`); cosine distance = 1 - cosine similarity; default otherwise L2 — [sqlite-vec KNN docs](https://alexgarcia.xyz/sqlite-vec/features/knn.html), via search summary.
- Open-source projects rebuild by dropping the vec0 table, recreating it with the new size and marking chunks for re-embedding; no ALTER for dimension found — [code-repo-wiki vecdb.rs](https://docs.rs/code-repo-wiki/0.5.0/src/code_repo_wiki/search/vecdb.rs.html), [talon-core](https://docs.rs/crate/talon-core/0.4.2/source/src/vec_ext.rs). (Secondary; official docs did not state this explicitly.)
- `vec_slice(v, start, end)` then `vec_normalize(v)` implement Matryoshka truncation in SQL (float32 only; slice first, then normalise) — [sqlite-vec API reference](https://alexgarcia.xyz/sqlite-vec/api-reference.html).
- Ollama `/api/embed` supports batch `input`, normalised outputs and a `truncate` option (truncates by default; errors when false and context exceeded) — [ollamar docs](https://rdrr.io/cran/ollamar/man/embed.html), [openSUSE packaging notes](https://src.opensuse.org/rpm/ollama/commits/branch/factory). Ollama does not auto-add task prefixes (secondary source) — [localaimaster](https://localaimaster.com/models/nomic-embed-text).
- Downstream project reports Ollama's nomic-embed-text default context ~2048 tokens and oversize inputs being truncated — [auto_review PR #20](https://git.johnwilger.com/jwilger/auto_review/pulls/20) (one author's observation).
- GGUF notes: llama.cpp defaults to 2048 context for nomic files; quantisation affects fidelity (MSE vs reference ~5.6e-10 F16, ~6.9e-4 Q4_0) — [nomic v1 GGUF card](https://huggingface.co/nomic-ai/nomic-embed-text-v1-GGUF/blob/main/README.md).
- Prefix conventions differ per model: nomic `search_document:`/`search_query:`/`clustering:`; Qwen3 `Instruct:..\nQuery:` on query side only; EmbeddingGemma-2 `task: ... | query:` / `title: none | text:` (see Section 1).

### Inferences
- Migration checklist: (1) build a labelled eval set; (2) create `vec0(... float[N] distance_metric=cosine)` as a new table beside the old one; (3) re-embed all facts with the exact new prefix convention, in batches; (4) store model name + prefix + dim in a metadata row so mixed vectors are never compared; (5) re-calibrate thresholds; (6) swap and drop old table. Cost scales with fact count x model speed; a 0.3-0.6B model is cheap on CPU/small GPU for short snippets.
- For symmetric duplicate comparison, use the same-side prefix on both texts (nomic `clustering:` or `search_document:` for both; EmbeddingGemma `task: sentence similarity`; Qwen3 with the same instruction on both) instead of query/document asymmetry. I did not find a source recommending this specifically; verify empirically.
- Truncating a bigger model (Qwen3-0.6B at 1024 -> 768 via MRL) keeps the 768 schema but is a different embedding space, so full re-embed and re-calibration are still required. It is advisable only if it avoids touching downstream code, not for any comparability reason. EmbeddingGemma-2 is natively 768d, so it is the lowest-friction schema match.

### Gaps
- Did not locate specific ollama/ollama GitHub issues (embedding bugs, batch limits, num_ctx truncation behaviour, normalisation differences between /api/embeddings and /api/embed); search tool returned no issue pages. Verify on the tracker before migration. sqlite-vec official statement on dimension change not found.

## 5. Reranker or lexical signal versus bigger embedding model

### Takeaway
Evidence favours a two-stage design: embedding for candidate retrieval, then a pairwise cross-encoder (and likely entity/number overlap) for the final same-event decision; a bigger bi-encoder gives smaller gains. Direct evidence for entity/number overlap was not found.

### Cited Findings
- Cross-encoder rerank over bi-encoder candidates improved news dedupe clustering (ARI 93.7 vs 91.5), with n-gram overlap far behind at 75.0 — [arXiv 2210.04261](https://arxiv.org/pdf/2210.04261).
- Cross-encoders cost roughly 10-100x per pair vs bi-encoder similarity and produce no reusable embeddings, so they suit shortlists only — [Zilliz FAQ](https://zilliz.com/ai-faq/what-is-the-overhead-of-using-a-crossencoder-for-reranking-results-compared-to-just-using-biencoder-embeddings-and-how-can-you-minimize-that-extra-cost-in-a-system), [ZeroEntropy](https://www.zeroentropy.dev/articles/biencoder-vs-crossencoder/).
- Duplicate-question detection commonly uses cross-encoders on a shortlist — [Zeroentropy / KX docs](https://code.kx.com/kdbai/reference/reranking.html).

### Inferences
- For "same event" the critical discriminators are location, date/time, magnitude and named entities (e.g. two different fires in the same county, or two flood gauge readings): embeddings often rate such pairs highly similar. Cheap rule-based features (shared place names, numbers/acreage, time window) as a veto or boost are likely a better complement than a bigger embedding model. This reasoning is mine; no cited source tested it.
- A reranker such as bge-reranker or Qwen3-Reranker would be run only on pairs within the uncertain similarity band, keeping cost low.

### Gaps
- No evidence found on entity/number-overlap features for event matching, nor reranker model comparisons or Ollama availability of rerankers.

## Overall recommendation (research-based, with uncertainty)
- Keeping nomic-embed-text v1.5 is defensible if current dedupe precision/recall on a labelled sample is acceptable: zero migration cost, 768d matches schema, and the model is Apache-licensed, but it is a 2024 model with the weakest vendor benchmark numbers in the set, a 2K context in Ollama, and requires correct prefix use.
- Best switch candidates: embeddinggemma-2 270m/440m (native 768d, Apache-2.0, same schema, 8K ctx) and qwen3-embedding:0.6b (stronger vendor MTEB numbers; 1024d or MRL-truncated). Run a bake-off of 3 models (current, these two) on 200-500 labelled snippet pairs before committing; add an entity/number overlap check and optional cross-encoder for the grey band.
- Caveat: all quality claims above are vendor-reported; no independent task-specific evidence was found.
