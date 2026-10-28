# Eval log

Every kept or rejected change, with numbers and run IDs (`runs/` is local and gitignored). See [EVALS.md](EVALS.md) for how runs are graded and compared.

## 2026-10-27 · B0 baseline and noise floor

**Config:** `b0`, gpt-oss-20b fast (`p150x2`), `reasoning_effort=medium`, `max_tokens=4096`, concurrency 32. **Judge:** gpt-oss-20b, prompt `JUDGE_PROMPT` in `grading.py`.
**Runs:** `20261027-190123-b0`, `20261027-193403-b0`

| Suite | Run 1 | Run 2 | Not attempted | p50 latency (at 32 concurrent) | Tokens per answer |
|---|---|---|---|---|---|
| FinanceBench (150) | 12.0% | 11.3% | 23% | 110–122 s | 660–740 |
| SimpleQA (150) | 9.3% | 10.0% | 53–57% | 153–159 s | 1,040 |

**Noise floor:** the difference is ±0.7 points, but the bootstrap CI on B − A is about ±5 points, and 6–10% of questions flip between runs. **At n = 150, only changes above ~5 points are detectable.**

- FinanceBench metric questions (exact figures from the filings) score 2–4% closed-book. That's the gap retrieval has to close.
- 287 of 300 answers needed the LLM judge. gpt-oss answers in sentences, so deterministic matching rarely applies.
- Run 1 had 2 SimpleQA timeouts at 600 s. The timeout is now 1,800 s.

## 2026-10-27 · Judge switched to Claude (blinded subagents)

`judge-export` built a blinded queue: 1,149 undecided answers from all 4 runs, shuffled, with opaque keys. Six Claude subagents graded it against `rubric.md`. The earlier gpt-oss grades are kept as `grades.gptoss.jsonl`.

- **Agreement with gpt-oss on the same answers:** 93–94% for gpt-oss's own runs, and 88–91% for Ornith's. gpt-oss did **not** inflate its own family's scores. If anything it was harsher on Ornith.
- **Where they disagree:** mostly long-form FinanceBench answers. Claude credits an answer whose main conclusion matches the gold one. gpt-oss marks it wrong when it leaves out the gold answer's specific figures. Calibrate this with ~30 hand-graded answers.
- **B0 under the Claude judge:** FinanceBench 16.0% / 14.7% and SimpleQA 10.0% / 10.0% (runs 1 and 2). The noise floor is unchanged: CI about ±4–6 points.

## 2026-10-27 · Ornith 1.5 35B-A3B vs gpt-oss-20b at B0

**Config:** `b0` via `recipes/ornith.toml` (`raahemnabeel/ornith-1.5-35b-a3b`, `p300`, the same 2 chips), `max_tokens=4096`, concurrency 32. Baseline: `20261027-190123-b0`. **Judge:** Claude Opus 5.5 (blinded).
**Runs:** thinking on `20261027-202203-b0`, thinking off `20261027-202816-b0`.

| Suite | gpt-oss | Ornith, thinking | Δ (95% CI) | Ornith, no thinking | Δ (95% CI) |
|---|---|---|---|---|---|
| FinanceBench | 16.0% | 16.7% | +0.7 [−6.0, +7.3] | 9.3% | −6.7 [−12.0, −1.3] |
| SimpleQA | 10.0% | 12.0% | +2.0 [−3.3, +7.3] | 8.7% | −1.3 [−6.0, +3.3] |
| p50 latency (FinanceBench / SimpleQA) | 109 / 159 s | 40 / 20 s | | 2.7 / 2.3 s | |
| Wall time, 300 answers | ~33 min | 6.2 min | | 1.4 min | |

Component benchmarks (`ttdr bench`):

| | gpt-oss | Ornith |
|---|---|---|
| Decode, 1 stream / 32 streams total | 34 / 162 tok/s | 120 / 676 tok/s |
| Tool calls (2 runs × 12) | 24/24 at low, 20/24 at medium | 23/24 thinking, 24/24 no thinking |

**Decision:** ~~about 5× lower time per answer~~ (void; see the correction below). Ornith with thinking on **ties** gpt-oss on quality (inside the noise floor). With thinking off, it's **significantly worse** on FinanceBench. The default LLM stays gpt-oss until B1 and a full research run confirm this. Ornith with thinking on is the leading candidate.

## 2026-10-27 · Correction: gpt-oss was measured off its fast path

Every gpt-oss number above used **default sampling** (`temperature=1.0`, no `top_k`). On `jashansinghTT/gpt-oss-20b-blackhole-fast`, that falls off the fused device path: sampling with `top_k ≤ 32` (or greedy) stays fast, while `top_k > 32` or no top_k is 5–9× slower. Chat endpoint, 256 tokens, `ignore_eos`:

| gpt-oss `p150x2` | Default sampling | `temp=1, top_k=20` | Greedy |
|---|---|---|---|
| 1 user | 35 tok/s | 135–170 tok/s | 171 tok/s |
| 32 users, total | 162 tok/s | 970 tok/s | 1,403 tok/s |

The bundle card claims 187 tok/s for one user (greedy), which matches. **The Ornith-vs-gpt-oss speed conclusion above is void.** Configured with `top_k=20`, gpt-oss is as fast as Ornith or faster (Ornith measured 120 / 676 tok/s). The quality comparison stands. Next: put sampling in the recipe, re-run gpt-oss B0 and re-measure both models with their recommended fast-path sampling.

## 2026-10-27 · Final LLM A/B: gpt-oss-20b vs Ornith 35B-A3B, both on their fast paths

**Config:** `b0`, concurrency 32, sampling from the recipe. gpt-oss: `temperature=1, top_k=20`, `reasoning_effort=medium`. Ornith: `temperature=0.6, top_p=0.95, top_k=20`, thinking on. Two runs each. **Judge:** Claude Opus 5.5, with all 4 runs in one blinded queue (`data/judge/queue2`).
**Runs:** gpt-oss `20261027-205753-b0`, `20261027-210506-b0`; Ornith `20261027-211809-b0`, `20261027-212400-b0`.

| Two runs pooled | gpt-oss-20b | Ornith 35B-A3B | Δ (95% CI) |
|---|---|---|---|
| FinanceBench accuracy | 15.3% | 17.3% | +2.0 [−3.0, +7.0] |
| SimpleQA accuracy | 11.3% | 14.3% | +3.0 [−2.3, +8.3] |
| Confident wrong answers (of 600) | 371 | 235 | −37% |
| Right when it answered (FinanceBench / SimpleQA) | 19% / 16% | 28% / 29% | |
| Decode, 1 stream / 32 total | 170 / 991 tok/s | 119 / 689 tok/s | |
| Wall time per B0 run | 6.7–7.2 min | 5.9–6.0 min | |
| Tool calls (2 × 12) | 20/24 | 24/24 | |
| Context / KV cache | 131K / 528K tokens | 262K / 1.82M tokens | |

Noise floor: same-config pairs differ by 0–2.7 points.

**Decision:** recommend **Ornith 35B-A3B (thinking on)** as the default LLM. Accuracy is tied. Ornith gives 37% fewer confident wrong answers, has 3.5× the KV cache (more parallel researchers with long contexts), twice the context, and vision, and its tool calls were perfect. gpt-oss decodes about 1.4× faster at the peak, but Ornith spends fewer tokens per answer (about 300 vs 1,150 on SimpleQA), so it finishes runs as fast or faster. Prefill (the design doc's argument for gpt-oss) isn't measured yet; check it at Stage 9.

## 2026-10-28 · B1 (naive RAG) vs B0 on FinanceBench

**Config:** `b1`, Ornith 1.5 (thinking on), top-10 chunks from the whole FinanceBench corpus (84 filings, 36,430 chunks, embeddinggemma-2 in Qdrant), `max_tokens=4096`, concurrency 32. **Judge:** Claude Opus 5.5 (blinded, 3 subagents).
**Runs:** B1 `20261028-162106-b1`; B0 `20261027-211809-b0`.

| FinanceBench | B0 | B1 | Retrieval hit (gold page in top 10) | Citation hit (cites a gold page) |
|---|---|---|---|---|
| All (150) | 17.3% | **73.3%** | 75% | 63% of 140 citing answers |
| metrics-generated (50) | 8% | 90% | 98% | 90% |
| novel-generated (50) | 12% | 70% | 78% | 60% |
| domain-relevant (50) | 32% | 60% | 50% | 36% |

**B1 − B0 = +56.0 points, 95% CI [+47.3, +64.7]** (+89 / −5 questions). Kept.

- Retrieval recall (`ttdr corpus recall`): 33% / 63% / 76% / 86% at k = 1 / 5 / 10 / 20 over the whole corpus, barely better when the filing is known (77% at 10). The embedder finds the right filing; the misses are the right page within it.
- Accuracy tracks retrieval: metric questions (one table, one page) nearly always hit; domain-relevant questions spread over several pages and miss half the time. That's where reranking (stage 7) and agentic search should help.
- 22 abstentions vs 18 wrong answers: with passages in hand Ornith says "I don't know" rather than guess.
- p50 latency (212 s) isn't comparable with B0: B1 shared the LLM with a deep research job, and the prompts are ~4K tokens longer.

## 2026-10-28 · Triage (stage 7): decision-model reranking, on vs off

**Setup:** top 30 chunks by embedding, reranked by Laya, FinanceBench questions (gold page in top k). Full set (150) for the shipped wording; a 50-question sample for the variants.

| Ranking | recall@1 | @5 | @10 |
|---|---|---|---|
| Embedding only (150) | 33% | 63% | 75% |
| Laya "useful" alone (150) | 8% | 27% | 48% |
| Embedding only (50) | 34% | 66% | 76% |
| Laya alone, best of 3 wordings (50) | 14% | 40% | 68% |
| Blend 0.8 embedding + 0.2 Laya (50) | 34–36% | 64–66% | 78% |

**Decision:** triage stays **off** (`features.triage = false`). Laya alone hurts at every k; blended it's within noise. The blend is what ships behind the flag, so a stronger decision model can be tried with one recipe change. Laya's 512-token state cuts a 1,500-character chunk, and its scores track claim support more than topical usefulness (also seen at stage 0a).

## 2026-10-28 · Decision model on claim-passage pairs (stage 6)

**Set:** 113 pairs from FinanceBench gold pages (40 supported, 33 contradicted by a changed figure, 40 not enough info from a far page of the same filing), labels by construction (`ttdr eval claims-build`).

| Model | Accuracy at 0.5 | Best thresholds | Per-label recall (best) | p50 per pair |
|---|---|---|---|---|
| Laya, 1,086-char windows | 42% | 49% | supported 40%, contradicted 12%, NEI 88% | 354 ms |

Word choice (plain, NLI-style, ours) and window size (600 vs 1,200 chars) all land at 42–50%. Kev-9B, the planned challenger, could not be served (see JOURNEY). Verification ships with Laya and is marked provisional: use its badges as a pointer, not a verdict, until a stronger decision model passes this set.

## 2026-10-28 · AI-Q (shallow researcher) on FinanceBench vs B0 and B1 (stage 4)

**Config:** `aiq`, `agent=shallow_researcher`, through AI-Q's job API with our MCP engine as its only source (web + the FinanceBench corpus), Ornith 1.5 with the prefix-caching bundle, concurrency 8. **Judge:** Claude Opus 5.5, blinded. **Run:** `20261028-175948-aiq` (27 answers re-fetched with `ttdr eval retry` after client polling errors; every job had succeeded server-side).

| FinanceBench | B0 | B1 (naive RAG) | AI-Q shallow |
|---|---|---|---|
| All (150) | 17.3% | 73.3% | **74.7%** |
| metrics-generated | 8% | 90% | 88% |
| novel-generated | 12% | 70% | 68% |
| domain-relevant | 32% | 60% | 68% |
| Wrong / abstained | — | 18 / 22 | 38 / 0 |
| p50 / p95 latency | — | — | 85 s / 306 s |

**AI-Q − B1 = +1.3 points, CI [−6.7, ~+9]:** a tie. AI-Q − B0 = +57.3 [+49.3, …]. The agent decides for itself whether to search the corpus or the web, and gets the same accuracy as a fixed RAG pipeline that was told where to look. It never says "I don't know": twice as many confident wrong answers as B1. Abstention is worth prompting for before customers see it.

## 2026-10-28 · Load test: shallow research jobs (stage 9)

`ttdr bench load -c 1 -c 2 -c 4 -c 8 --jobs 8`, FinanceBench questions, packaged stack (containers), idle box apart from the CPU OCR backfill.

| Jobs at once | Reports/hour | p50 | p95 | Prefill share of request time |
|---|---|---|---|---|
| 1 | 156 | 26 s | 38 s | 26% |
| 2 | 243 | 26 s | 70 s | 19% |
| 4 | 247 | 52 s | 68 s | 16% |
| 8 | 242 | 86 s | 119 s | 19% |

Throughput saturates at ~245 reports/hour from 2 concurrent jobs; beyond that jobs queue rather than overlap. Consistent with the batch effect below. Prefix-cache hits were 0%: shallow prompts don't share 2,048-token prefixes.

**Batch effect (the main lever):** one request with `tool_choice: required` or a JSON-schema `response_format` drops every request in the batch from ~118 to ~13 tok/s (guided decoding falls off the fused sampler). LangChain's structured output (`ToolStrategy`) forces `tool_choice` on every turn of AI-Q's planner and researchers. Fixing it on either side (server: guided decoding on device; harness: auto tool choice with a text fallback) is the largest speedup available.

**Prefix caching** (new Ornith bundle): a repeated 29K-token prompt goes from 6.2 s to 0.4 s. Over a day of agent traffic, 26% of 8.0M prompt tokens hit the cache.
