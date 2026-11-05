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

## 2026-10-28 · Verify and revise on AI-Q answers (stage 6)

`ttdr eval verify-run runs/20261028-175948-aiq --revise`: every cited sentence in the 150 AI-Q answers checked by Laya against the passages it cites (corpus pages from Qdrant, web pages fetched), weak claims sent back to Ornith once, then re-checked. 99 s for all 150.

| | Cited claims | Supported | Contradicted | Not enough info | Source unreachable | Citation validity |
|---|---|---|---|---|---|---|
| AI-Q answers | 128 | 35 | 3 | 40 | 50 | 27% |
| After one revision | 87 | 34 | 0 | 3 | 50 | **39%** |

**Accuracy after revision: 74.7% → 74.7%** (+1 / −1 question, CI [−2.0, +2.0]; Claude judge, blinded). Revision removes or narrows unsupported claims without losing correct answers. Kept, with two caveats:
- Laya scores 42–49% on our claim pairs, so "not enough info" often means "Laya couldn't tell". Validity numbers are a lower bound until a stronger decision model passes the pair set.
- 50 of 128 claims cite pages the engine can't fetch (sec.gov and some mirrors block scripted clients). AI-Q cites the public URL it found on the web rather than our corpus page for the same filing, so the claim can't be checked against the copy we have.

## 2026-10-29 · Harnesses: Deep Agents vs AI-Q on the same engine and chips (stage 11)

Same Ornith, same MCP engine, same corpus; only the harness differs. FinanceBench graded by blinded Claude subagents (4 chunks, `judge-export`).

| FinanceBench (150) | AI-Q shallow | Deep Agents |
|---|---|---|
| Accuracy | 74.7% | **74.7%** (+0.0, CI [−7.3, +7.3]; +16/−16) |
| Wrong / abstained | 38 / 0 | **29** / 9 |
| p50 / p95 latency | 85 s / 306 s | 106 s / 299 s |
| Answers citing evidence IDs | — | 118 / 150 |

A tie on accuracy, with fewer confident wrong answers from Deep Agents. Four of its nine abstentions were "I have no search access": researchers called Deep Agents' built-in `grep`/`ls` file tools as if they searched the corpus. Fixed after this run (only the engine's tools are offered now), so the abstention count is an upper bound.

| Deep question (Blackhole vs Blackwell vs MI350) | AI-Q deep | Deep Agents |
|---|---|---|
| Time to report | 1 h 55 min | **3 min** |
| Model calls / searches / fetches | 336 / 276 / 260 | 30 / 15 / 33 |
| Words / cited evidence IDs | 2,537 / numbered URLs | 3,428 / 72 |
| Claude grader, overall (two graders, order swapped) | 4, 4 | **7, 7** |
| Citation validity (Laya, `ttdr verify`) | n/a (no evidence IDs) | 42% (11 supported, 1 contradicted, 14 not enough info) |

Both graders preferred the Deep Agents report. AI-Q's report misidentified MI350 ("a companion chip to the MI300X") and so mostly described MI300X, put TensorRT-LLM in AMD's stack, and leaked planning residue into the text. The Deep Agents report had correct core specs for all three chips and a deeper prefill/decode analysis; its errors were smaller (H100's CUDA-core count for B200, MI350X FP4 "not published", Infinity Fabric per-link vs per-GPU). Reports: [reports/](reports/).

**Why so different in time:** Deep Agents asks for no structured output, so every request stays on Ornith's fast decode path (~118 tok/s); AI-Q forces a tool call on every planner and researcher turn (~13 tok/s for the whole batch) and runs ~10× more model calls. One question each, so the quality gap is indicative, not measured; the time gap is large enough not to need more samples.

## 2026-10-29 · AI-Q with auto tool choice vs forced tool calls (stage 9 lever)

`recipes/forced-tool-choice.toml` (AI-Q as shipped) vs the default with `features.auto_tool_choice = true` (`patches/aiq/0004`). Same stack and patches (0001–0006), 8 jobs at a time, run back to back; one blinded Claude queue for both runs (6 chunks).

| | Forced tool calls | Auto tool choice |
|---|---|---|
| FinanceBench accuracy (150) | 76.0% | **78.7%** (+2.7, CI [−3.3, +8.7]; +12/−8) |
| Wrong / abstained | 34 / 2 (both empty reports) | 31 / 1 |
| Shallow p50 / p95 | 100 s / 170 s | **90 s / 140 s** |
| Deep report (Blackhole question) | 1 h 55 min | **18 min** |
| Deep: researchers / model calls / searches / fetches | 15 / 336 / 276 / 260 | 8 / 123 / 44 / 90 |
| Deep: Claude graders, overall (two graders, three reports blinded) | 5.5, 5 | 5, 4 |

**Kept as the default.** Accuracy holds (the difference is inside the CI), shallow jobs are 10–18% faster, and deep jobs 6× faster because no request in the batch falls off the fused sampler. The deep report from the auto run did less research (8 researchers instead of 15) and graded slightly lower: its prefill/decode section was short and uncited. One sample each, so that gap is within noise, but AI-Q deep reports trail Deep Agents' (7, 7.5 from the same graders) either way.

The forced baseline here (76.0%) is 1.3 points above the earlier AI-Q run (74.7%, patches 0001–0003); the two differ by patches 0004–0006 (off), the containerized stack, and run-to-run noise.

## 2026-10-29 · Decision models: Laya vs Kev vs CLM vs the LLM itself

**Question:** which decision model should verify claims, and do we need one at all? Web search is parked (DeepResearch Bench throttled our SearXNG), so this is on the FinanceBench corpus.

**Claim-passage pairs** (113, labels by construction; `ttdr eval claims-run --recipe …`):

| Verifier | Accuracy (default / best thresholds) | Supported | Contradicted | Not enough info | p50 per pair | Chip |
|---|---|---|---|---|---|---|
| Laya (`tt-hous/laya`, current default) | 42% / 49% | 40% | 12% | 88% | 0.3 s | 3 |
| CLM-v0.1-8B (`tt-hous/clm-v0.1-8b`) | 35% / 35% | 100% | 0% | 0% | 1.4 s | 3 |
| Kev-9B (`tt-hous/kev-9b`) | 64% / 69% | 75% | 36% | 90% | 4.4 s | 3 |
| Ornith, thinking off (`recipes/llm-verify.toml`) | 69% / 69% | 70% | 36% | 95% | 3.8 s | none extra |

CLM says "supported" to everything: it's a ranker, not a claim checker. Kev and Ornith tie; Ornith needs no chip of its own (the `decide` role can now point at the LLM).

**Does better verification improve answers?** Verify and revise on the 150 AI-Q FinanceBench answers (auto tool choice, 78.7%), then the blinded Claude judge:

| | Accuracy | vs unrevised | Citation validity before → after |
|---|---|---|---|
| Unrevised | 78.7% | | |
| Revised with Kev | 76.0% | −2.7 (CI [−5.3, −0.7]), +0/−4 | 36% → 67% |
| Revised with Ornith | 78.0% | −0.7 (CI [−2.0, 0.0]), +0/−1 | 38% → 65% |

- Revision raises validity by **removing** claims (81 → 42–48), not by fixing them, and it never turned a wrong answer right. With Kev it cost 4 correct answers: Kev's false "not enough info" flags send good sentences to the reviser, and in one case the reviser didn't get the source text and replied "I need to fact-check… the source content wasn't included" instead of an answer.
- Laya's earlier run (stage 6) also left accuracy flat. So on this corpus a decision model buys citation badges, not accuracy.

**Recommendation:** no dedicated decision model for now. Point `decide` at Ornith for verify badges, don't auto-revise answers, and free chip 3. Revisit when a decision model clears ~85% on the pair set, and fix the reviser to always get the source text.

## 2026-10-30 · MiniCheck-Flan-T5-Large on the CPU as the claim checker

**Question:** can a dedicated claim checker on the CPU (no chip) beat Ornith's 69% on the pair set? MiniCheck-Flan-T5-Large (770M, MIT, `lytang/MiniCheck-Flan-T5-Large@96eafd0`) scores 74.7% on LLM-AggreFact, level with GPT-4. It's wired in as a new model kind, `check` (`src/ttdr/checker.py`, `recipes/minicheck.toml`); it only verifies (no triage) and never says "contradicted".

| Verifier | Accuracy | Supported | Contradicted | Not enough info | p50 per pair |
|---|---|---|---|---|---|
| Ornith, thinking off | 69% | 70% | 36% | 95% | 3.8 s |
| MiniCheck-FT5-L, CPU (8 threads) | 44% / 45% | 28% | 0% (can't) | 100% | 10.7 s |

- The model works as published: "Revenue in 2022 was $5.2 billion" against a passage saying so scores 0.96, with $9.1 billion 0.01.
- It fails on our pairs because their "claims" are FinanceBench gold answers, often terse and computed ("1.73", "$400,000,000 increase", "Yes, change in PPNE was positive"). MiniCheck checks a standalone sentence against the text; it doesn't do the arithmetic or know the question. Ornith does both. Even scored as supported vs not (where it can't lose on contradictions), it reaches 71%, missing 29 of 40 supported pairs.
- On the CPU it's slow: ~1–2 s per 2,048-token window, serialized, so ~11 s for a page-length passage.

**Decision:** not adopted. A fair test would use real report sentences labeled by the Claude judge; it isn't worth chip time unless that test shows a clear win.

## 2026-10-30 · Rerankers on the CPU: bge-reranker-v2-m3 vs Qwen3-Reranker-0.6B

**Setup:** the 150 FinanceBench questions, top 30 chunks over the whole corpus by embeddinggemma-2 (run on the CPU; vectors match the chip's, cosine ≈ 1.000), reranked on the CPU; gold page in the top k. Script: a scratch harness, not in the engine yet. A 50/50 blend mixes the min-max-normalized embedding score with the reranker's rank percentile.

| Ranking (whole corpus) | @1 | @5 | @10 | @30 | p50 per query (30 passages, CPU) |
|---|---|---|---|---|---|
| Embedding only | 32.7% | 62.7% | 76.0% | 90.0% | — |
| Laya (stage 7) | 8% | 27% | 48% | | |
| bge-reranker-v2-m3 alone | 33.3% | 64.0% | 78.7% | 90.0% | 13.2 s |
| bge 50/50 blend | 40.7% | 70.0% | 82.0% | 90.0% | |
| **Qwen3-Reranker-0.6B alone** | **38.0%** | **72.7%** | **84.0%** | 90.0% | 18.9 s |
| Qwen3 50/50 blend | 36.7% | 70.7% | 84.0% | 90.0% | |

- Paired against embedding only at k = 10: bge blend +13/−4 questions, Qwen3 blend +16/−4. At k = 5: +14/−3 and +15/−3.
- Qwen3 needs no blend (it takes an instruction; ours names filings, metrics and periods). Within a known filing it reaches 87% at 10 (embedding 79%).
- Expected answer gain for naive RAG from B1's split (84% correct when the gold page is retrieved, 41% when not): ~+5 correct answers, ~+3 points. Measured end to end once a reranker is on a chip.
- The CPU is far too slow for agents (13–19 s per search); a reranker needs a chip.

- The instruction needn't be domain-specific. With the general one the engine sends (`recipes/rerank.toml`: "Given a research question, retrieve passages that contain information needed to answer it"), Qwen3-0.6B scores 43.3% / 74.7% / 83.3% at 1 / 5 / 10, against 38.0% / 72.7% / 84.0% with the finance-specific one.
- How deep the pool goes: dense recall is 89% at 30, 94% at 50 and 96% at 100, so a pool of 50 would give the reranker 5 more points to find (`ttdr corpus recall --rerank --pool 50` on the chip).

**Decision:** a reranker is worth a chip. Qwen3-Reranker family; 4B or the multimodal Qwen3-VL-Reranker for the general-purpose tool (text and page images in one model). Not tested at 4B/8B: the CPU would take hours, the chip minutes.

## 2026-10-30 · Hybrid search: Qdrant's built-in BM25 + dense vs dense alone

**Question:** does keyword search fused with the embeddings find more gold pages? Built with what Qdrant already has (1.19: server-side BM25 inference, `qdrant/bm25`, English stopwords and stemmer, IDF modifier; RRF or DBSF fusion over 50 candidates each), no code of our own. A copy of the collection with the same dense vectors plus a BM25 sparse vector per chunk (filing name + text); 150 FinanceBench questions, gold page in the top k.

| Whole corpus | @1 | @5 | @10 | @20 | @30 |
|---|---|---|---|---|---|
| **Dense (embeddinggemma-2)** | **32.7%** | **62.0%** | **76.0%** | **86.0%** | **90.0%** |
| BM25 alone | 8.7% | 14.0% | 19.3% | 24.7% | 28.7% |
| RRF (dense + BM25) | 22.7% | 55.3% | 68.0% | 78.7% | 84.7% |
| DBSF | 18.7% | 50.7% | 65.3% | 76.0% | 84.7% |

| Known filing | @1 | @5 | @10 | @30 |
|---|---|---|---|---|
| **Dense** | **36.7%** | **66.0%** | **79.3%** | **92.0%** |
| BM25 alone | 15.3% | 30.0% | 46.0% | 66.0% |
| RRF | 35.3% | 64.7% | 75.3% | 88.7% |

- Paired against dense at k = 10: RRF +3/−15 (whole corpus), +4/−10 (known filing). At 30, the reranker's pool, RRF never adds a page dense misses (+0/−8).
- Why: FinanceBench questions are long, instruction-laden ("Assume that you are a public equities analyst… balance sheet…") and every 10-K shares the same vocabulary, so BM25 ranks other companies' cash-flow pages high; the filing name in the text didn't fix it. BM25 works as designed (checked by hand); it just has no edge here.
- Caveat for the general-purpose tool: keyword search wins on exact identifiers (part numbers, error codes, rare names), which this suite doesn't test, and agents write shorter queries than these questions. Worth rechecking with a suite that has them.

**Decision:** dense only. The reranker (+8 points at 10) is the retrieval lever; hybrid fusion would shrink its pool.

## 2026-10-30 · Ingestion: Docling vs our pdfium extraction and character chunker

**Question:** does Docling (IBM, MIT; layout model, table structure, OCR, HybridChunker) retrieve better than what we have, pdfium's text layer cut into 1,500-character chunks? If so it would replace our extraction, chunker and lazy OCR, and bring ~30 input formats with it. Docling's own pipeline end to end: `PdfPipelineOptions(do_ocr, do_table_structure)`, `HybridChunker` with embeddinggemma-2's tokenizer at 384 tokens (about our chunk size), each chunk embedded as `contextualize()` (section headings + text). 30 filings (3M 2018, Amcor 2020 and 28 random), the 47 FinanceBench questions on them, same CPU query vectors for both; current chunks use their stored chip vectors.

| | @1 | @5 | @10 | @20 |
|---|---|---|---|---|
| Current, known filing | **46.8%** | 74.5% | **91.5%** | 91.5% |
| Docling, known filing | 42.6% | **76.6%** | 89.4% | 91.5% |
| Current, across the 30 filings | **44.7%** | 72.3% | **91.5%** | 91.5% |
| Docling, across the 30 filings | 36.2% | **74.5%** | 85.1% | 91.5% |

- Paired, Docling vs current: @5 +4/−3 and +5/−4, @10 +1/−2 and +0/−3. A wash at 5, slightly behind at 1 and 10; 47 questions can't separate them.
- Docling makes more, smaller chunks (18,089 vs 12,309, ~4.3 per page vs ~2.9), so its top 10 carries less text than ours.
- What it does well is visible in the output: tables come out as clean markdown with their headers, sections are labeled, figures are found. That may help the LLM *read* the passage even at equal recall, which retrieval recall doesn't measure.
- Cost: ~1.7 s per page on 8 CPU threads (4,197 pages in 2 h with two workers), so ~5–6 h for the 12,013-page corpus, plus a full re-embed.

**Decision:** not adopted for PDFs; by the rule set beforehand (adopt if it favours Docling) it doesn't. Open: an end-to-end B1 run on the 47 questions with Docling's chunks vs ours, to test whether markdown tables lift answer accuracy (staged in `data/ab/docling30`, needs Ornith). For formats we can't read today (docx, pptx, html, xlsx, scanned images), docling-serve (`quay.io/docling-project/docling-serve-cpu:v1.38.2`, pulled) is still the import to use when a corpus needs them.

## 2026-10-31 · Qwen3-VL-Reranker-8B on a chip, a multimodal suite, and page images for the LLM

**Setup:** Qwen3-VL-Reranker-8B (`raahemnabeel/qwen3-vl-reranker-8b`, tt-dit-server) on chip 3 in Laya's place; Ornith on chips 0–1 also answers verify (`recipes/rerank.toml`). New suite **UniDoc-Bench** (DATASETS.md): 200 questions from the energy and construction domains, 50 each whose answer is text only, needs a table, is in an image only, or needs image and text, searched against both domains' 1,491 PDFs (16,946 pages; 973 image-only, 529 figure-heavy). B1 runs today on one stack; Claude graders, blinded across runs.

**Retrieval** (gold page in top k, whole corpus, pool of 30 unless noted):

| | FinanceBench @1 | @5 | @10 | UniDoc @1 | @5 | @10 |
|---|---|---|---|---|---|---|
| Embedding only | 33.3% | 62.7% | 76.0% | 56.5% | 86.5% | 90.0% |
| Rerank, chunk text | **55.3%** | **82.7%** | **86.0%** | 73.0% | **91.0%** | 92.5% |
| Rerank, chunk text, pool 50 | 56.7% | 82.7% | 89.3% | | | |
| Rerank, page image | 46.7% | 71.3% | 80.0% | **80.0%** | 90.0% | **93.0%** |

- The 8B VL reranker beats the 0.6B on the CPU (84% @10) and costs ~0.13 s per 3 passages, ~1.5 s per search under load; page images ~0.2 s each.
- Page images win on UniDoc (figures, plans) and lose on FinanceBench (dense 10-K tables are hard to read at 72 dpi). Text stays the default; "both" (text and page together) is untested (it dropped connections under a 16-way load).

**Answers (B1, top 10 passages):**

| | FinanceBench | UniDoc | UniDoc text only | table | image + text | image only |
|---|---|---|---|---|---|---|
| B1 | 72.0% | 58.5% | 78% | 74% | 56% | 26% |
| + rerank | 78.0% | 60.5% | 82% | 80% | 54% | 26% |
| + rerank + top 3 pages as images to Ornith | **80.0%** | **69.0%** | 88% | 84% | 60% | **44%** |

- Paired vs B1: FinanceBench +6.0 [0, +12.7] with rerank, **+8.0 [+2.0, +14.0]** (+18/−6) with pages; UniDoc +2.0 [−3, +7] with rerank, **+10.5 [+5.0, +16.0]** (+27/−6) with pages.
- Ornith is multimodal: given a page, it reads chart and table values the text layer doesn't have, and stops swapping figures it saw only as flattened text. "I don't know" fell 46 → 26 on UniDoc.
- Reference points (paper, Claude 4.5 Sonnet with their best retrieval, top 10 pages): 76.6% completeness on UniDoc, a different metric than ours.

**Friction:** concurrent image requests killed Ornith's engine ("chunked prefill is not supported"): vLLM sized the image-encoder budget from the 2,048-token step budget before tt raised it, so it split a request's prefill at an image. `--max-num-batched-tokens 262144` at launch fixes it (`serve_args` in the recipe). A second crash came mid-agent-run ("image prompts are prefilled whole"). The port's guard is batch-wide: a fresh image prompt batched with any text prompt that has a prefix-cache hit kills the engine. Ornith now launches with `--no-enable-prefix-caching`. A unique `cache_salt` per image request was tried first and doesn't help, because the hit is on the other request.

**Decision:** rerank (chunk text, pool 30) on; page images to the LLM on for B1. Next: the same for agents, whose tools return text.

### Deep Agents on the new engine (2026-10-31)

Recipe `recipes/rerank.toml`: reranker in `search`, the `read_page` tool, and the corpus catalog in prompts. Concurrency 4; 3 sub-questions, 8 tool calls. Graded by blinded Claude graders (`data/judge/queue_da2`). Compared with B1 + rerank + pages.

| | FinanceBench | UniDoc | text | table | image+text | image only | p50 s |
|---|---|---|---|---|---|---|---|
| B1 + rerank + pages | 80.0% | 69.0% | 88% | 84% | 60% | 44% | 55 |
| Deep Agents | **82.0%** | **72.0%** | 96% | 80% | 62% | 50% | 122 / 149 |

- Paired: FinanceBench +2.0 [−4, +8] (+12/−9); UniDoc +3.0 [−3, +9] (+23/−17). Ahead on both, significant on neither.
- Deep Agents never abstained (0 "I don't know" on FinanceBench). It wins on text questions and on image-only ones. It loses some table questions, where B1's page images show the whole table at once.
- About 2.5× the latency of B1.
- The run records don't say how often agents called `read_page`. The auto page-reading run (`recipes/rerank-read.toml`) tests whether pushing page readings into every search is better than leaving it to the agent.

## Embedder: Qwen3-VL-Embedding-8B replaces embeddinggemma (2026-11-02)

**Question:** does a larger, multimodal embedder find the gold page more often, and should we search page images?

**Setup:** raahemnabeel/qwen3-vl-embedding-8b, 4,096-d, served on all four chips (p300x2) for indexing; about 85 chunks/s and 6 pages/s. Each embedder searches the same documents: all 84 FinanceBench filings, and a UniDoc subset of 346 documents (the 173 gold documents plus 173 random distractors, seed 0). Metric: rank of the first gold page among distinct pages. Scratch scripts: `qwen_embed/ab.py` and `gemma_cpu.py`. For UniDoc, embeddinggemma's queries were embedded on the CPU; on FinanceBench the CPU queries reproduce the on-chip numbers to within 0.7 points.

| Page recall | @1 | @5 | @10 | @20 | @30 |
|---|---|---|---|---|---|
| FinanceBench, embeddinggemma chunks | 32.7 | 63.3 | 78.7 | 88.0 | 92.7 |
| FinanceBench, Qwen chunks | **37.3** | **74.0** | **84.7** | **94.0** | **97.3** |
| FinanceBench, Qwen page images | 36.7 | 62.7 | 76.7 | 83.3 | 87.3 |
| FinanceBench, Qwen chunks + pages (RRF) | 44.0 | 71.3 | 83.3 | 90.0 | 94.7 |
| UniDoc subset, embeddinggemma chunks | 60.5 | 89.5 | 92.5 | 95.5 | 96.0 |
| UniDoc subset, Qwen chunks | 63.0 | 89.5 | 94.0 | 95.0 | 96.5 |
| UniDoc subset, Qwen page images | **71.5** | **95.0** | **99.0** | **99.5** | **100.0** |
| UniDoc subset, Qwen chunks + pages (RRF) | 68.5 | 93.5 | 95.0 | 97.5 | 99.5 |

- Qwen chunks beat embeddinggemma on both suites. On FinanceBench, misses at @30 (the rerank pool) fall from 7.3% to 2.7%.
- Page-image search is the best by far on figure-heavy UniDoc, and the worst on text-dense filings. Fusing the two splits the difference. Choosing pages or chunks per corpus, or adding the reranker on top, is the follow-up.

**Decision:** Qwen3-VL-Embedding-8B is the default embedder, indexing chunks (collection `chunks_qwen3vl`). embeddinggemma stays in the recipe and its index (`chunks`), so a rollback is one role change. Page-image search is not in the product yet; the vectors are in `pages_qwen3vl` (all of FinanceBench and part of UniDoc).

## A/B: in-document tools from AgenticRAG (2026-11-02)

**Technique:** Microsoft's AgenticRAG (arXiv 2605.05538) gets 92% on FinanceBench with four tools: search, find, open and summarize. We had search and our own read_page. We added `find_in_document` (exact terms inside one document, on Qdrant's full-text index) and `open_pages` (a passage's whole page and its neighbours).

**Setup:** Deep Agents (3 sub-questions, 8 tool calls), recipe `rerank.toml` with the Qwen3-VL embedder. The baseline and the variant (`doc_tools=true`) ran at the same time on one engine, over the dev sets (40 FinanceBench, 40 UniDoc). Graded by blinded Claude graders (`data/judge/ab_doctools`). Runs `20261102-203018` (baseline) and `20261102-203023` (variant).

| | Baseline | + doc tools | Diff [95% CI] | Fixed / broke | Median s |
|---|---|---|---|---|---|
| FinanceBench dev | 77.5% | **85.0%** | +7.5 [−2.5, +20] | +4 / −1 | 315 → 262 |
| UniDoc dev | 72.5% | **77.5%** | +5.0 [−5, +15] | +3 / −1 | 345 → 349 |

- Agents use the tools unprompted: 1.2 finds and 1.5 opens per question. They replace some searches (6.5 → 5.7) and page reads (2.1 → 1.6).
- 6 of the 7 fixed questions used the tools:
  - Ulta's registered debt securities: `find` reached the 10-K cover page list, and the agent answered "none".
  - CVS capital intensity: `find` and `open` got the balance-sheet lines, and the agent computed ROA ≈ 1.8%.
  - UniDoc G1 reflectance: `open` read the pages continuing the figure discussion.
- The 2 broken questions look like noise. One used neither tool and flipped on a judgement call; the other is a UniDoc near-miss.

**Decision: adopt.** The direction is consistent on both suites, the mechanism is visible in the traces, and the run is faster on FinanceBench. `doc_tools` now defaults to on, and AI-Q's data-source description lists the tools. The full-suite eval later confirms it.

## A/B: context condensation (2026-11-02)

**Technique:** QUEST and similar agents condense the context once it grows (QUEST-35B on BrowseComp: 45.5 → 64.6). Deep Agents already ships the middleware: it summarizes at 85% of the model's `max_input_tokens` and keeps the last 10%. Without a profile it assumes 170k, so on our runs it never fired. The variant sets `context_tokens=40000`, which condenses past ~34k tokens.

**Setup:** Deep Agents (5 sub-questions, 12 tool calls, web and corpus), DeepResearch Bench dev (8 English tasks). Both arms ran at the same time on one engine. The grading was RACE by blinded Claude graders against the benchmark's reference report, where 0.5 means parity (`data/judge/ab_condense`). Runs `20261102-224021` (baseline) and `20261102-224026` (variant).

| | Baseline | Condensed | Diff [95% CI] | Wins / losses |
|---|---|---|---|---|
| RACE overall | **0.317** | 0.274 | −0.043 [−0.116, +0.019] | 3 / 5 |
| without drb_88 | | | −0.015 | |

- Condensation fired: input tokens per LLM call fell from 17.5k to 7.0k on drb_90.
- On drb_88, the variant's agent searched only the corpus, got Netflix 10-Ks back, and gave up. That happened before any condensation, so it is noise rather than the technique.
- drb_90 lost the most (0.335 → 0.200). The condensed sub-agents re-ran searches (279 vs 161) and their report cites fewer sources (11 vs 26). Condensation drops the evidence the writer needs.
- Every report in both arms trails the reference (0.32 vs 0.5). The gap is in insight (0.26) and comprehensiveness (0.30), which comes down to the writer, not the context.

**Decision: reject.** No gain, and a visible loss when the summary discards evidence. `context_tokens` stays a harness switch, off by default. The next A/B targets the writer (WebWeaver's outline then section-by-section writing).

## A/B: outline-then-sections writer, after WebWeaver (2026-11-03)

**Technique:** WebWeaver (arXiv 2509.13312) writes from an outline whose sections each list the evidence they use, then writes one section at a time from only that evidence. Ours (`ttdr.writer`, switch `sections`) runs after the research:
1. An outline of 4-8 sections, each with a goal and its evidence IDs, drawn from the researchers' notes.
2. One call per section, in parallel, seeing the outline, its own note lines and its passages (up to 800 characters each).
3. A title and summary written from the finished body.

The planner's one-pass report is kept only when no outline comes back.

**Setup:** as in the condensation A/B (Deep Agents 5/12, DRB dev, RACE against the reference by blinded Claude graders, `data/judge/ab_sections`). This time it ran on Ornith's mixed-vision build with prefix caching. Runs `20261103-003831` (baseline) and `20261103-003836` (sections).

| | Baseline | Sections | Diff [95% CI] | Wins / losses |
|---|---|---|---|---|
| RACE overall | 0.304 | **0.326** | +0.022 [−0.075, +0.098] | 5 / 3 |
| without drb_91 | | | **+0.064** | 5 / 2 |
| insight | 0.243 | **0.297** | +0.054 | |
| comprehensiveness | 0.278 | **0.309** | +0.031 | |

- The writer did not run on drb_91: the researchers got "No results" from the web on every Saint Seiya query, and the planner gave up. That is a search failure, the same kind as drb_88 in the last A/B.
- On the other 7 tasks there are 5 wins, up to +0.186 (drb_89) and +0.128 (drb_90). The two losses are −0.018 and −0.001.
- Reports are 3× longer (median 35k vs 11k characters) and cite more (32.5 vs 23.5 IDs per report). Invented IDs fell from 10 to 2, because each section sees only real IDs. The median time rose from 977 s to 1,062 s.
- Weakness seen in the traces: sections sometimes comment on their own material ("only two have any grounding in the provided material").

**Decision: adopt.** `sections` defaults to on. Follow-ups: fix web-search give-ups (both A/Bs lost a task to them), and stop sections commenting on their material.

## A/B: what fills the reranker's pool, chunks, page images or both (2026-11-03)

**Technique:** ColPali-style systems such as VisRAG and M3DocRAG retrieve page images rather than text chunks. We already have both in one 4,096-d Qwen3-VL space (`chunks_qwen3vl`, `pages_qwen3vl`). The pilot measured page recall after the Qwen3-VL reranker, over each whole corpus, on the dev sets (scratch `fuse.py`):
- A: the top 30 chunks reranked on text (the product today).
- B: the top 30 page images reranked on the image.
- C: the pages of the top 15 chunks plus the top 15 page images, reranked on page text and image together.

| | @1 | @3 | @5 | @10 |
|---|---|---|---|---|
| UniDoc dev, A chunks | 65.0 | 85.0 | 92.5 | 95.0 |
| UniDoc dev, B pages | **80.0** | **92.5** | **100** | **100** |
| UniDoc dev, C fused | 75.0 | 90.0 | **100** | **100** |
| FinanceBench dev, A chunks | 47.5 | **77.5** | **85.0** | **95.0** |
| FinanceBench dev, B pages | **52.5** | 67.5 | 77.5 | 87.5 |
| FinanceBench dev, C fused | 45.0 | **77.5** | **85.0** | 92.5 |

- On UniDoc, image-only questions at @5 go from 70% (chunks) to 100% with pages or fused. The other answer types are at 100% for every arm.
- Fused matches the best arm at @5 on both corpora, without knowing the corpus type in advance. That matters for users' own collections.

**Decision: adopt fused retrieval for the engine, pending an end-to-end dev A/B.** Retrieval is not the main source of errors (reading is), so it becomes the default only if answers improve.

### End to end: fused retrieval loses (2026-11-03)

Deep Agents (3/8, doc tools, sections writer) on the dev sets, with chunk retrieval (engine :9901, `rerank.toml`) and fused retrieval (a second engine on :9902, `recipes/pages.toml`) running at the same time. Graded by blinded Claude graders (`data/judge/ab_pages`). Runs `20261103-022029` (chunks) and `20261103-022035` (fused).

| | Chunks | Fused | Diff [95% CI] | Fixed / broke |
|---|---|---|---|---|
| FinanceBench dev | **77.5%** | 62.5% | −15.0 [−27.5, −2.5] | +1 / −7 |
| UniDoc dev | **77.5%** | 70.0% | −7.5 [−20, +5] | +2 / −5 |

- In 11 of the 12 broken questions the gold page was retrieved in both arms, so retrieval was not the cause.
- Fused hits carry a whole page cut to 2,000 characters under the page's first ID. On dense filing pages the exact figure falls past the cut, where a chunk hit would have delivered it. Citations also lose precision, since one ID covers a whole page.

**Decision: reject as built.** `recipes/pages.toml` stays opt-in. A second version would rerank on the page (image and text) but hand the agent the page's best-matching chunks, keeping chunk IDs.

### Sections writer on short-answer questions (2026-11-03)

The writer was adopted on DRB. On the corpus dev sets it was checked by a paired run with `sections=false` (run `20261103-045147`), on the same engine as the sections run `20261103-022029`. Graded by blinded Claude graders (`data/judge/ab_sections_qa`).

| | Sections off | Sections on | Fixed / broke by sections | p50 s |
|---|---|---|---|---|
| FinanceBench dev | **85.0%** | 77.5% | +2 / −5 | 106 → 314 |
| UniDoc dev | 75.0% | **77.5%** | +3 / −2 | 132 → 386 |

- Turning a one-number answer into a 12k-character report buries or garbles the number, and it triples latency.
- The p50 of 106 s against 262 s in the doc-tools A/B is the prefix cache at work (Ornith's mixed-vision build).

**Decision: gate the writer by request kind.** One short call classifies the request as ANSWER (a figure, date, name, yes/no, calculation or short list) or REPORT (analysis, a survey, a comparison, a review, a plan). Only REPORT requests get the outline and sections. On the dev sets the classifier marks REPORT for 7/8 DRB tasks, 0/40 FinanceBench and 9/40 UniDoc (its summarization and causal questions). An earlier attempt to let the outline step reply DIRECT marked 6/8 DRB tasks direct, so it was dropped.

## A/B: free web search, Yandex + Bing News + OpenAlex vs Bing + Yahoo (2026-11-03)

**Why:** Bing answers only the first word of automated queries ("Saint Seiya Hades surplices" returns pages about saints). Yahoo is Bing underneath, and the other scraped engines were either blocked or never ran (docs/JOURNEY.md). A probe of 32 research queries × 12 engines, scored by the embedder, found Yandex (median similarity 0.70; 230 of 320 results pass the 0.6 cutoff) and Bing News (61 of 94) on topic. OpenAlex adds academic hits (89 of 235). Bing's median was 0.40, with 14 of 306 passing.

**Setup:** Deep Agents with the gated sections writer on DRB dev. The baseline arm (`20261103-072550`) used SearXNG as configured. The variant (`20261103-074210`) used a second SearXNG on :8889 with `keep_only: [yandex, bing news, openalex]`, behind a second engine on :9902. The web cache key now names the SearXNG instance, so the arms couldn't share results. RACE against the reference was scored by four blinded Claude graders (`data/judge/ab_web`). Both arms ran while AI-Q's deep researcher was also running, so wall-clock times aren't comparable.

| | Bing + Yahoo | Yandex + Bing News + OpenAlex | Diff [95% CI] | Wins / losses |
|---|---|---|---|---|
| RACE overall | 0.289 | **0.378** | +0.090 [−0.030, +0.196] | 7 / 1 |
| without drb_88 | 0.277 | **0.416** | **+0.139** | 7 / 0 |
| comprehensiveness / insight | 0.264 / 0.234 | **0.362 / 0.363** | | |

- The searches that used to fail now work. drb_98 (non-alcoholic cocktails) went from 0.05 to 0.41: the baseline wrote a research-failure notice from one source, and the variant wrote a 54k-character report with 71 cited IDs. drb_91 (Saint Seiya) cited 81 IDs vs 10, and its grader called the baseline's characters "mostly made up". drb_90 went from 0.27 to 0.43, and drb_99 from 0.24 to 0.46.
- Fetches rose (median 32.5 vs 7 per task) because the hits are now worth reading. Searches fell (median 31.5 vs 93), because agents no longer re-query after "No results".
- The one loss, drb_88, was not a search issue. The planner answered from memory without searching (3 model calls) and the request classifier marked the task ANSWER, so the result was a 2k-character answer that invented a Ciro Guerra film. The classifier is fixed (commit 716ef4a: how/why questions count as reports, classified at temperature 0).
- Invented IDs rose (median 1.5 vs 0; 15 on drb_99), as median citations went from 10.5 to 42 per report. Worth watching.

**Decision: adopt.** `configs/searxng/settings.yml` now keeps Yandex, Bing News and OpenAlex only. One scraped engine is a single point of failure: a 96-query burst at 8 concurrent passed with no captchas, but a keyed backend (Tavily, Brave or Serper) remains the robust option.

## A/B: AI-Q vs Deep Agents, short questions (2026-11-03)

**Setup:** both harnesses ran concurrently on the same engine (Qwen3-VL embedder and reranker, in-document tools, prefix-cached Ornith), at 4 questions each. AI-Q used its shallow researcher with patches 0001–0006 and auto tool choice. Deep Agents used the defaults (3 researchers, 8 tool calls each, writer gated to report requests). Graded by two blinded Claude graders (`data/judge/ab_harness`). Runs: `20261103-072610-aiq` and `20261103-072550-deepagents`.

| Dev sets (40 each) | AI-Q shallow | Deep Agents | Diff [95% CI] | +/− | p50 latency |
|---|---|---|---|---|---|
| FinanceBench | **80.0%** | 77.5% | −2.5 [−12.5, +7.5] | +2/−3 | **75 s** vs 324 s |
| UniDoc | 67.5% | **77.5%** | **+10.0** [0.0, +22.5] | +5/−1 | **135 s** vs 908 s |
| Wrong / abstained | 21 / 0 | 16 / 2 | | | |

- FinanceBench is a tie. Deep Agents' three new misses against its earlier no-writer run were all calculation slips (capex average, dividends, FCF conversion), not the writer, which fired on 1 of 40 questions.
- UniDoc favors Deep Agents (+10, at the edge of significance), with fewer confident wrong answers (7 vs 13).
- Latency favors AI-Q by a wide margin. The p50 gap is partly load: Deep Agents' UniDoc questions overlapped three DRB arms. But the FinanceBench half ran under the same load for both, and AI-Q was still 4× faster. Deep Agents makes 3 researchers × up to 8 tool calls per question, while AI-Q's shallow researcher makes one short loop.

**Decision: keep Deep Agents** as the harness. It is as accurate or better, with fewer confident wrong answers. Its cost on short questions is time, which points to a cheaper path: route short-answer requests (the writer's ANSWER class) to a one-researcher, shallow plan. That is the next A/B. DRB (AI-Q deep vs Deep Agents) follows below once it finishes.

## A/B: AI-Q deep researcher vs Deep Agents on DRB dev (2026-11-03)

**Setup:** each harness ran alone on the chips, 4 tasks at a time, with the adopted search (Yandex + Bing News + OpenAlex).
- AI-Q's deep researcher: patches 0001–0006, auto tool choice, run `20261103-121147-aiq`.
- Deep Agents: 5 researchers × 12 tool calls, sections writer, fixed request classifier, run `20261103-155011-deepagents`.
- The earlier Yandex arm (3 × 8, old classifier, `20261103-074210`) was graded in the same blinded queue, giving four reports per task with the reference (`data/judge/ab_harness_drb`).

| DRB dev (8) | AI-Q deep | Deep Agents 5×12 | Deep Agents 3×8 (old classifier) |
|---|---|---|---|
| RACE overall | **0.428** | **0.429** | 0.368 |
| Deep Agents vs AI-Q, paired | | +0.001 [−0.047, +0.057], 3 wins / 5 losses | |
| Median / slowest task | 107 min / 134 min | **24 min / 40 min** | |

- **Quality is a tie.** Deep Agents wins drb_88 (+0.16; the classifier fix turned a 2k-character answer into a full report) and drb_90 (+0.08). AI-Q wins drb_98 (−0.10), drb_57 and drb_89 (−0.05 each).
- **Deep Agents is 4.5× faster** on long reports. On short questions, AI-Q was 4× faster.
- **Grader consistency:** the 3×8 arm scored 0.378 in the previous queue and 0.368 in this one, from different graders.
- **Why Deep Agents lost drb_98, drb_57 and drb_89:** the graders flagged sections that stop mid-sentence and summaries that promise content the body never has. Those three reports had the most invented IDs before repair (10, 101, 119). In drb_57 the whole-report citation rewrite was kept, and it had an 8,192-token cap on reports of 35–80k characters. The repair now runs per section (commit after this entry), and an A/B is running.

**Decision: keep Deep Agents.** Quality is equal, it is 4.5× faster on reports, and its known loss cause is fixable. AI-Q stays configured as the alternative harness.

## A/B: citation repair per section instead of whole-report rewrite (2026-11-03)

**Setup:** Deep Agents 5×12 on DRB dev, run twice under identical conditions; the only change is the repair (`20261103-155011` whole-report, `20261103-164551` per section). Blinded Claude RACE graders, `data/judge/ab_repair`.

| | Whole-report rewrite | Per section | Diff [95% CI] | Wins / losses |
|---|---|---|---|---|
| RACE overall | 0.421 | **0.433** | +0.013 [−0.023, +0.050] | 5 / 3 |
| Sections cut mid-sentence (8 reports) | 4 | **0** | | |
| Invented IDs before repair, max per report | 119 | 13 | | |

- The two reports that had been cut off gained the most: drb_98 0.34 → 0.46 and drb_57 0.40 → 0.45. Graders again flagged the old reports for being "cut off mid-sentence" and for summaries promising content the body lacks, and did not flag the new ones for this.
- The losses are run-to-run variation in research, not repair defects. drb_71 (−0.07) leaned on US cases and repeated one trial.
- The whole-report run scored 0.429 in the previous queue and 0.421 here.
- One grader spotted invented IDs like `W5e6f7g8h` in a new report. The check only recognized hex web IDs, so these survived. Fixed: an ID-like token of W + 8 letters and digits now counts as a citation.

**Decision: adopt** (committed before the run). With it, Deep Agents is at 0.433 vs AI-Q deep's 0.428, in about a quarter of the time.

## A/B: a lighter plan for short-answer requests (2026-11-03)

**Why:** AI-Q's shallow researcher answered short questions about 4× faster than Deep Agents. The variant classifies the request before research (it was already classified, for the writer) and gives ANSWER requests 1 researcher × 8 tool calls instead of 3 × 8 (`quick_subquestions=1 quick_tool_calls=8`).

**Setup:** both arms concurrent on FinanceBench dev and UniDoc dev, Deep Agents with the gated writer. Runs `20261103-173042` (default) and `20261103-173057` (quick); blinded Claude graders, `data/judge/ab_quick`.

| | Default plan | Quick plan | Diff | +/− | p50 latency |
|---|---|---|---|---|---|
| FinanceBench | 77.5% | 77.5% | +0.0 [−10.0, +10.0] | +2/−2 | 224 s → 199 s |
| UniDoc | 75.0% | 72.5% | −2.5 [−7.5, 0.0] | +0/−1 | 404 s → 401 s |
| Model calls / input tokens, median | 9.5 / 76k | 9.0 / 70k | | | |

- On short questions the planner usually sends one researcher anyway, so capping the plan changes little. Deep Agents' cost there is per-call context (~70k input tokens a question), not plan breadth.
- Latencies are inflated by running two arms at 4 each, plus deployment restarts mid-run (no errors).

**Decision: reject.** The switch stays, off by default. The latency gap to AI-Q is about context size, which is the lever to try next: a smaller first-turn prompt and condensing passages.

## A/B: QUEST-35B-RL as the LLM (2026-11-03)

**Model:** QUEST-35B-RL (OSU NLP), a fine-tune of Qwen3.5-35B-A3B (Ornith's architecture, with vision) after mid-training, SFT and RL on synthetic deep-research tasks. Served as `raahemnabeel/quest-35b-rl` on chips 0–1 (`recipes/quest.toml`), with Ornith's sampling.

**First attempt, in Deep Agents (thinking off): stopped.** No question finished in an hour (about 120 model calls per question).
- Part of the cause was a bug of ours: passages had just gained page-image links, and agents fetched them as web pages and got "No results". Fixed: `fetch_page` on a page link now reads that page from the corpus.
- With the bug fixed, QUEST still never stopped. Researchers ignored "tool call limit exceeded" (210 times in one question). The planner re-sent the same sub-question about 17 times after a researcher had already found the answer.

**Second attempt, in QUEST's own scaffold** (`ttdr.quest`, after OSU-NLP-Group/QUEST, MIT): one ReAct agent with thinking on, `search` (a list of queries) and `visit` (URLs plus a goal) on the engine, ending with `<answer>`. Turns go through the completions API, because the server's chat tool parser expects Qwen3.5 XML calls and dropped QUEST's JSON ones. Runs: `20261103-212654-quest` (DRB dev) and `20261103-212704-quest` (FinanceBench dev). Baselines: Ornith + Deep Agents, `20261103-164551` (DRB) and `20261103-173042` (FinanceBench). Each pair was graded in one blinded Claude queue (`data/judge/ab_quest_drb`, `ab_quest_fb`).

| | Ornith + Deep Agents | QUEST, own scaffold | Diff [95% CI] | Wins / losses |
|---|---|---|---|---|
| DRB dev, RACE | **0.445** | 0.355 | −0.090 [−0.123, −0.056] | 0 / 8 |
| FinanceBench dev | 77.5% | 80.0% | +2.5 [−7.5, +12.5] | +3 / −2 |
| Median time | 24 min / 224 s | 8 min / 212 s | | |
| Median model calls | — / 9.5 | 5.5 / 4 | | |

- QUEST's reports were shorter (median 14.7k characters vs about 46k) and more thinly researched. Graders flagged reports built on one or two sources, generic bullet lists, and chatbot-style closing offers ("if you want, I can…").
- The scaffold is a partial reproduction. QUEST was trained with Google (Serper) results, Jina page text summarised toward the goal by an LLM, a context condenser, a Python tool and up to 400 turns. Ours has none of the summariser, condenser or Python tool. Two FinanceBench answers ended in an unexecuted `PythonInterpreter` call.

**Decision: reject; Ornith stays.** It loses every DRB task and only ties on FinanceBench. Ornith's lead here comes as much from the harness (sections writer, per-section citation repair, request classifier, all tuned on Ornith) as from the model. Thinking has never been A/B'd in Deep Agents (it runs off); that's the next A/B, and if it helps, a short QUEST probe in Deep Agents with thinking on.

## A/B: thinking on in Deep Agents (2026-11-04)

**Change:** Ornith with thinking on (`-p thinking=true`) for every agent in the Deep Agents harness; until now it ran with thinking off. Everything else as the default (rerank recipe, 3 sub-questions, 8 tool calls, sections writer). Runs: `20261103-221836-deepagents` (off) and `20261103-221851-deepagents` (on), at the same time on the same stack. One blinded Claude queue for both (`data/judge/ab_thinking`).

| | Thinking off | Thinking on | Diff [95% CI] | Wins / losses |
|---|---|---|---|---|
| FinanceBench dev (40) | 82.5% | **87.5%** | +5.0 [+0.0, +12.5] | 2 / 0 |
| UniDoc dev (40) | 75.0% | **80.0%** | +5.0 [−7.5, +17.5] | 5 / 3 |
| Median / mean time | 258 / 319 s | 278 / 453 s | | |
| Mean output tokens | 4.1k | 8.4k | | |
| Mean model calls | 11 | 15 | | |

- Run-to-run noise is about this size: the thinking-off arm scored 82.5% on FinanceBench dev here and 77.5% in the QUEST A/B a few hours earlier. Running both arms side by side is what makes the comparison fair.
- Thinking roughly doubles output tokens and adds about 40% to mean time; the slowest questions get much slower.

**Decision: passes the short-question gate.** Next, both arms side by side on DRB dev, where the cost matters more.

### Thinking on, DRB dev (2026-11-04)

Same arms on the 8 DRB dev tasks, side by side: `20261104-005512-deepagents` (off) and `20261104-005527-deepagents` (on), one blinded RACE queue (`data/judge/ab_thinking_drb`).

| | Thinking off | Thinking on |
|---|---|---|
| RACE overall | 0.415 | 0.410 (−0.005 [−0.028, +0.015], 3 / 5) |
| Insight | 0.422 | 0.376 |
| Median report length | ~40k chars | ~18k chars |
| Median time | 27 min | 35 min |
| Mean output tokens | 18k | 33k |

- A tie overall, but for a fixable reason: the sections writer ran with thinking too, and its reasoning shares each section's 8k-token budget. Sections came out short and one report stopped mid-section.
- The thinking-off arm had its own failure: one report (drb_89) fell into a loop, the same four sentences about 50 times each (203 repeated lines). Graders marked it down on readability.

**Decision: not yet.** Next arm: thinking on for the planner and researchers, off for the writer (`writer_thinking`, new, default off).

### Thinking on for the agents, off for the writer, DRB dev (2026-11-04)

`writer_thinking=false` keeps each section's 8k-token budget for the section itself. Runs: `20261104-021721-deepagents` (off) and `20261104-021736-deepagents` (agents on, writer off), side by side; blinded RACE queue `data/judge/ab_writer_think`.

| | Thinking off | Agents on, writer off |
|---|---|---|
| RACE overall | 0.381 | **0.445** (+0.064 [+0.011, +0.142], 5 / 3) |
| Without drb_91 | 0.436 | **0.462** (4 / 3) |
| Median report length | 43k chars | 48k chars |
| Median time | 21 min | 29 min |
| Mean output tokens | 13k | 29k |

- In the off arm, drb_91 got no web results from any of its 42 searches (Yandex through SearXNG answered nothing for a while, then recovered) and the report was a refusal, scored 0. Without that task the gain is smaller but still positive.
- Report length is back to normal: the short reports of the first thinking run came from the writer's reasoning, not the agents'.

**Decision: adopt.** Thinking is now on by default for the planner and researchers (harness and eval), and off for the writer. It wins on short questions (+5 on FinanceBench and UniDoc dev) and on reports, at about 35–40% more time and twice the output tokens.

### QUEST-35B-RL in Deep Agents with thinking on: probe (2026-11-04)

The follow-up promised above: QUEST as the LLM in the Deep Agents harness, now with thinking on (writer off), on 4 FinanceBench dev questions and 2 DRB dev tasks, capped at 90 minutes. Runs `20261104-032646-deepagents` and `20261104-032656-deepagents`.

- FinanceBench: two empty answers (one after 20 min and 45 model calls), one context-length error, and two answers after 72 and 84 minutes with 91 and 147 model calls (Ornith: about 4–5 minutes and 10–15 calls).
- DRB: one empty report after 89 minutes and 125 model calls; the second task never finished.

**Decision: reject, not graded.** With thinking on QUEST still doesn't stop in this harness; it keeps searching (up to 90 searches a question). Its own scaffold is the only place it works, and there it lost every DRB task.

## 2026-11-04 · BrowseComp-Plus retrieval: BM25 hits added to the embeddings before reranking

**Question:** BrowseComp-Plus questions are full of exact clues (names, dates, odd phrases). Does keyword search over every page's full text find gold documents the embeddings (one lead chunk per page) miss? Imported, not built: Qdrant's server-side BM25 (`qdrant/bm25`, English stemmer, IDF) in a sparse collection beside the dense one, 2.9M chunks indexed in 287 s on CPU. Unlike the rejected 2026-10-30 fusion (RRF shrank the reranker's pool), BM25 hits are **added** to the 30 dense candidates and the reranker orders the union.

150 questions, the whole question as the query, a gold document anywhere in the top k:

| | @1 | @5 | @10 | @20 |
|---|---|---|---|---|
| BM25 alone | 14.0% | 22.0% | 26.0% | 30.0% |
| Dense + rerank | 27.3% | 40.7% | 42.7% | 43.3% |
| Dense + 10 BM25, rerank | 34.7% | 48.7% | 50.0% | 50.7% |
| **Dense + 30 BM25, rerank** | **34.7%** | **51.3%** | **52.7%** | **53.3%** |

- +10 points at 10. The cost is a reranker pool of up to 60 instead of 30 per corpus search.
- Inside real runs retrieval was already better than this single-query test suggests: in the 22 finished questions of the stopped dev run, agents' many short searches found a gold document in 16 of 19 runs that searched. The 3 misses are what this targets.

**Decision: adopt for BrowseComp-Plus retrieval** (`features.keywords`, 30 hits); end-to-end A/B on the 40-question dev set next.

### The same on FinanceBench and UniDoc (2026-11-04)

Page recall over the whole corpus after reranking (BM25 index: `chunks_qwen3vl_keywords`, 74,681 chunks, built in 5 s). Searches within a known document don't use BM25, so that row is unchanged.

| | @1 | @5 | @10 | @20 |
|---|---|---|---|---|
| FinanceBench (150), dense + rerank | 56.0% | 80.7% | 88.7% | 95.3% |
| FinanceBench, + 30 BM25 | 54.7% | 80.7% | 89.3% | 94.7% |
| UniDoc (200), dense + rerank | 71.5% | 91.5% | 93.5% | 93.5% |
| UniDoc, + 30 BM25 | 72.0% | 93.0% | 95.0% | 95.0% |

- FinanceBench: no change (the 10-Ks share their vocabulary, as on 2026-10-30). UniDoc: +1.5 points, within noise.
- The reranker no longer lets BM25 hurt (October's RRF lost 8 points here); it just has little to add where the embeddings already find 89–94%.

**Decision: on by default, off for FinanceBench and UniDoc.** Customers' documents may hinge on exact names, codes and phrases the way BrowseComp-Plus does, and BM25 hurt nowhere. `features.keywords` defaults to on and `ttdr corpus index` builds the BM25 index; FinanceBench and UniDoc have none (`--no-keywords`), so their searches add no hits and the reranker does no extra work. A corpus indexed before BM25 searches as before until `ttdr corpus keywords NAME`.

### End-to-end BrowseComp-Plus A/Bs: stopped (2026-11-04)

- **Thinking fallback works:** in the dev rerun with it (`20261104-151458`), all 9 finished questions had answers, including bcp_1046, which came back empty in both earlier runs. Before it, 7 of 18 were empty.
- **BM25 and pool A/B, 12 questions, arms side by side:** stopped before grading. The first attempt ran the 100 + 100 pool arm and a 150-question single-search check against one reranker chip alongside two other runs. The reranker fell behind (a 2-document request took over 10 minutes), and about 40% of searches in the new arms timed out, so the reranker was restarted. A clean rerun (`20261104-183749` without BM25, `20261104-183753` with) was stopped early to free the chips. No result.
- An earlier "stopped" dev run (`20261104-123931`) had kept running for 3 hours, because `kill $!` hit the nohup wrapper, not the eval; it halved the next run's speed.

**Decision:** keep the tested stack (`recipes/rerank.toml`, thinking on with the fallback, task cap, Brave fallback). BM25 stays on by default, but only BrowseComp-Plus has a keyword index. The open retrieval work is listed in PLAN.md under "Future improvements: retrieval".

## 2026-11-05 · A/B: the LLM — Ornith 1.5 122B, Qwen3.8-27B and Ornith 35B, each cohosted with the embedder and reranker

**Question:** does a bigger or newer LLM answer better, and does serving the LLM, embedder and reranker as one bundle on all four chips ("cohost") cost retrieval or speed? Each arm is one bundle (`recipes/ornith-122b.toml`, `recipes/qwen38-27b.toml`, `recipes/ornith-35b-cohost.toml`) against the 35B on its own chips with separate embed and rerank bundles. Thinking on, 4 questions at a time, blinded Claude grading.

**Retrieval is unchanged by cohosting.** Re-embedding 32 stored chunks gives cosine 0.9995 against the stored vectors in every bundle. FinanceBench page recall, 150 questions, dense + rerank:

| | @1 | @5 | @10 | @20 |
|---|---|---|---|---|
| Separate chips | 56.0% | 80.7% | 88.7% | 95.3% |
| 122B cohost | 56.0% | 80.7% | 88.0% | 96.0% |
| Qwen3.8 cohost | 55.3% | 81.3% | 88.0% | 95.3% |
| 35B cohost | 55.3% | 80.7% | 88.0% | 96.0% |

**Quality and speed** (seconds per question at concurrency 4; decode tok/s for one user and total at 32):

| | FinanceBench 12 correct | median s | BrowseComp-Plus, 10 paired | all 12 | median / mean / max s | decode 1 / 32 |
|---|---|---|---|---|---|---|
| 35B, separate chips | 10 | 585* | 5 | — | 2181 / 2917 / 11214 | 119 / 689 |
| 122B cohost | 10 | 322 | 5 | 6 | 3157 / 6074 / 17611 | 134 / 336 |
| Qwen3.8-27B cohost (no vision) | 8 | 162 | 5 | 6 | 423 / 852 / 2663 | 62 / 282 |
| **35B cohost** | 9 | 189 | **6** | **8** | 1023 / 1170 / 4160 | **197 / 1039** |

\* older run, before the researcher task cap and with a second eval on the box.

- Runs: FinanceBench 122B `20261104-210423`, Qwen3.8 `20261105-032957`, 35B cohost `20261105-045741`; BrowseComp-Plus (offline, keywords off to match `20261104-151458`) 122B `20261104-213443`, Qwen3.8 `20261105-034038`, 35B cohost `20261105-053151`.
- **122B:** no better on either set, twice as slow on hard puzzles (100k output tokens a question vs 31k), a third the throughput.
- **Qwen3.8-27B:** fastest end to end because its bundle caps thinking (about 4k output tokens per FinanceBench question vs 12–15k), but lost 2 FinanceBench questions, left one BrowseComp-Plus answer empty, and its bundle has no multi-user vision: one image request kills the server, so `models.X.vision = false` takes `read_page` away.
- **35B cohost:** the same model as before, decoding 1.5–1.7× faster; one FinanceBench question either way and one more BrowseComp-Plus question are within noise at 12 items.

**Decision: keep Ornith 1.5 35B-A3B, served cohosted** (`recipes/ornith-35b-cohost.toml`). Quality ties across all three LLMs at this size of test, so speed decides: the cohost is the fastest bundle with vision. Reject the 122B (slower, no gain) and Qwen3.8-27B (no vision, lower on documents).

## Your own documents: uploads in the web UI (2026-11-05)

**What:** AI-Q's web UI already ships a finished file-upload feature (drop zone, progress, ingest polling, a Files tab), off unless the backend lists a `knowledge_layer` source. `ttdr ui-server` now implements its collections/documents API over one shared library corpus (`data/corpus/library`), so no frontend code changed. `ttdr corpus upload FOLDER` sends a folder through the same API.

- Each upload batch indexes in the background, one batch at a time: PDFs as before (text chunks, page flags), Word and Markdown converted to text, and text files with every chunk embedded (the BrowseComp-Plus path embeds only a document's first chunk). All chunks go into BM25 too.
- Documents are named from the file plus a short ID (`Board_memo-7e974e`), so citations read as the file name and two users' `report.pdf` don't collide.
- The UI's per-chat collections all map to the library; dropping a chat no longer deletes anything. Deleting a file removes its points from the chunk, page-image and BM25 collections.
- **Scanned pages on the cohost bundle:** its embedder is text-only (the server rejects images unless started with `QWEN3VL_COHOST_VISION=1`), so image-only pages couldn't be embedded as page images and the whole upload failed. Embedders now take `vision = false` (set in the cohost recipe): their scanned pages are queued for the PaddleOCR worker while indexing, and each page's OCR text is embedded and BM25-indexed when it lands (up to 15 minutes; pages still unread are listed on the file). With a vision embedder, scans are embedded as page images and OCR stays lazy, as before.
- **Checked end to end** on the live stack (a second ui-server on :8201): a text PDF (12 chunks), a Markdown note, a Word memo and an image-only scanned PDF (OCR'd in 48 s) all indexed, and corpus search put the right upload first for each fact.

## Search scope: each suite sees only what it tests; collections the user picks (2026-11-05)

**Why:** a DRB report (drb_87) cited Adobe's 2022 10-K for a claim about AI democratizing fashion design; the passage is about IP litigation. Every Deep Agents eval searched with scope "all", and the planner is told to prefer the private collections, so web tasks could pull FinanceBench 10-Ks and UniDoc reports. 13 of the 50 final DRB reports cite at least one; some fit the task (Adobe's or Nike's own filings), some don't. SimpleQA, FRAMES and HLE ran the same way.

- **Evals:** Deep Agents now pins search per suite (`SUITE_SOURCES`): DRB, SimpleQA, FRAMES and HLE web only; FinanceBench, UniDoc and BrowseComp-Plus their own corpus only. `scoped=false` restores the old behaviour, and retries of earlier runs keep it. Runs already finished (SimpleQA, FinanceBench, UniDoc, DRB 50) and the FRAMES and HLE runs in progress used scope "all".
- **DRB 20 rerun:** free search and Tavily, both web only, side by side at 07:00 UTC, judged against the same 20 tasks of the scope-"all" run.
- **Agents hear only of collections that hold documents** (a facet on Qdrant): a fresh install lists none, where the recipe's catalog used to describe FinanceBench and UniDoc to every agent.
- **Collections:** `ttdr corpus upload FOLDER --collection NAME --description …` makes named collections beside the UI's uploads. The UI lists each collection with documents as a data source; the chat's selection pins search to those collections (`corpus` takes a comma-separated list).

## UI: thinking trace and tasks fill while a job runs (2026-11-05)

- **Thinking trace was empty:** AI-Q's UI fills it only from `llm.start`/`llm.chunk`/`llm.end` events, which the Deep Agents server never sent, and LangChain's `ChatOpenAI` drops the server's `reasoning_content`. A small subclass keeps it (not sent back to the model); every planner and researcher reply now becomes one start/chunk/end with its reasoning and token counts.
- **Tasks tab was often empty:** it shows only the planner's `write_todos`, which the planner often skips. Until it writes one, each researcher is a task (in progress, then completed). A researcher's own `write_todos` no longer leaves a tool call "running" forever.
- **Files tab "setup backend to enable files":** the deployed app server predated uploads; it lists `knowledge_layer` after the restart.
- Live check on a test server: one FinanceBench question streamed 5 model replies, all with reasoning, and a task list that ended completed.
- `ttdr up` now serves cohost bundles with `tt-model serve --detach` (models a bundle cohosts aren't served separately) and waits for them; `tt serve` refuses cohost bundles.

## Sandboxed Python for agents: set up, not yet A/B'd (2026-11-05)

**Why:** FRAMES final 66%, weakest on numerical reasoning (21/42); grader notes on the misses show off-by-one counts and wrong totals.

**What:** Pydantic's `mcp-run-python` 0.0.22 (Pyodide in Deno) as compose service `pyrun` on 127.0.0.1:9910 (profile `code`), read-only container. Its own `--disable-networking` also denies the server its listening port, so the image runs the package's Deno program with network limited to that port: sandboxed code reaches nothing else and sees only Pyodide's virtual filesystem (checked: date and mean arithmetic correct, an HTTP request fails, `/` lists only Pyodide's dirs). `services.code` in a recipe (`recipes/code.toml`) gives researchers and the planner `run_python_code` and one prompt line asking them to compute rather than do arithmetic in their heads; unset, nothing changes.

**First look (2 FRAMES misses rerun, not a benchmark):** both used the tool and computed correctly, and both still missed for other reasons: Steve Jobs' share of 1984 US life expectancy (56/75 = 74.67%; the gold 75.68% uses 74 years), and the Galileo library question (Laurentian 1571 vs Picton 1879 = 308; gold 222, a different fact). So some numerical misses are facts, not arithmetic; the gain needs the FRAMES dev A/B (40 questions, with and without `recipes/code.toml`), queued for when benchmarks resume.
