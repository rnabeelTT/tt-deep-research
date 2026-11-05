# Journey

Findings and friction, logged at the end of each stage. **Friction** marks something worth raising with the owning team. Eval numbers and A/B decisions go in [LOG.md](LOG.md).

## Stage 0a: model bring-up (2026-10-27)

**Result:** gpt-oss-20b (2 chips), embeddinggemma-2 and Laya serve at the same time through `tt serve`, which assigns chips itself.

- **Friction:** tt 1.0.1's catalog listed gpt-oss-20b for t3k and galaxy only, not QB2. We serve it as a community bundle, `jashansinghTT/gpt-oss-20b-blackhole-fast`. Finding the right bundle took `tt model search`, plus the team knowing which bundle is the fast one.
- **Friction:** `tt-hous/laya` doesn't serve as shipped. Its pull leaves out the repo's `multilingual/` folder, but the server checks for the full snapshot and runs offline, so it exits with code 3. Workaround: one `hf download` at the pinned revision ([SETUP.md](SETUP.md)). It should be fixed in the bundle.
- **Friction:** Kev-9B isn't in the community catalog listing, but `tt model search kev` finds `tt-hous/kev-9b`. The two discovery paths disagree.
- `tt-hous/laya-typed-decisions` is a specialist (invoices, security incidents, customer service, agent traces). On claim support it called a clearly supported claim "contradicted". Base Laya got 3 of 3 right at about 10 ms per call.
- Laya's API is custom (`/v1/systemone`: a state plus typed questions) and its documentation lives only in the model card. Each state is capped at 512 tokens, so passages must be chunked.
- First-pull boot times: gpt-oss 12.5 min, embedder 6 min, Laya 2.5 min. A cached restart of gpt-oss takes 5.4 min.
- Every serve needs `--yes`, because these are unverified community bundles.

## Stage 0b: skeleton (2026-10-27)

**Result:** `ttdr health` checks all 6 endpoints with real requests.

- Dropped LiteLLM. It needed a master key to boot (new in 1.104) and added nothing with only two OpenAI-compatible endpoints. It was also one more hop that could mangle gpt-oss tool calls. It's deferred until we need replicas, live A/B routing or many external clients.
- gpt-oss reasons before every answer, so even "OK" takes about 1.8 s. `reasoning_effort` needs to be chosen per step.

## Stage 1: eval harness and B0 (2026-10-27)

**Result:** `ttdr eval` builds pinned suites, runs a system, grades it (deterministic match, then an LLM judge) and compares runs with a paired bootstrap. B0 scores 12% on FinanceBench and 10% on SimpleQA. The noise floor is about ±5 points at n = 150 ([LOG.md](LOG.md)).

- **Friction (performance):** our first measurement of gpt-oss-20b fast (34 tok/s on one stream, 162 tok/s total) was **wrong by 5–9×**. The default sampling (`temperature=1.0`, no `top_k`) silently drops off the bundle's fused decode path. With `top_k ≤ 32` it runs at 135–170 tok/s on one stream and 970 tok/s total at 32 users. Nothing warns you: no error, no log line. The card's numbers are greedy. Every client (AI-Q, Hermes, TT-Studio) needs `top_k` set, or it gets a fifth of the speed.
- The bundle's `p150x2` (interactive) profile beats `p150x2-throughput` on a single stream (34 vs 20 tok/s) and ties it when batched. We switched to `p150x2`.
- The reasoning parser comes from the bundle manifest. `content` comes back clean, and `reasoning_effort` is honored (296 vs 1,790 reasoning tokens at low vs high).
- FinanceBench is CC BY-NC 4.0. It's approved for internal eval only; anything external needs legal review ([DATASETS.md](DATASETS.md)).
- Not yet done: AI-Q's `nat eval`. It's needed at Stage 4, for DeepResearch Bench.

## Stage 1.5: LLM A/B, Ornith vs gpt-oss (2026-10-27)

**Result:** with both models on their fast paths, Ornith 1.5 35B-A3B (thinking) ties gpt-oss-20b on accuracy, makes 37% fewer confident wrong answers, and has 3.5× the KV cache. gpt-oss decodes about 1.4× faster at the peak. We recommend Ornith ([LOG.md](LOG.md)). The 3.5–4.2× speed gap we first saw came from gpt-oss's sampling settings, not the models. The results were confirmed by an independent Claude judge.

- Bundle and **request settings** matter as much as model choice on Blackhole. The fused fast paths only cover some sampling configurations.
- With thinking off, Ornith answers in about 2 s but declines far more often. It could serve cheap steps such as routing and query rewriting, not answering.
- **The judge is now Claude:** blinded subagents grading an exported queue (`judge-export` / `judge-import`). It agrees with gpt-oss 88–94% of the time, and the conclusions held. They differ only on long-form answers, which ~30 hand-graded answers should settle.
- Added: recipe overlays (`extends`), a judge entry pinned in the recipe, `ttdr bench decode|tools`, and a model-agnostic reasoning control (`reasoning_effort` for gpt-oss, `enable_thinking` for Ornith).
- Per-model sampling now lives in the recipe (`sampling = {...}`), and every client inherits it, so fast-path settings can't be forgotten.

## Stage 4: AI-Q on the QB2 (2026-10-27/28)

**Result:** the shallow path works end to end: a cited answer in 12 s through our MCP `web_search`. The deep path doesn't finish within an hour yet. Four runs on one comparison question, each step decoded from AI-Q's checkpoint database:

| Run | Change | Outcome |
|---|---|---|
| 1 | AI-Q defaults | Budget of 100 searches spent in 3 min; researchers retried the budget error 1,272 times |
| 2 | Budgets raised to 1,000 calls / 60 queries | Planner made 276 searches (55 distinct), then called `think` with the same text 153 times |
| 3 | Search cache with a "you already ran this" note; `think` capped at 8 | Planner fixed (plan in 5 min). 8 researchers made ~170 searches each (20–36 distinct), ignored the note, exhausted 1,000 calls, wrote no notes |
| 4 | Thinking on for researchers | 6 of 8 researchers wrote notes (18–140 searches each, no budget errors). Two fell into runaway reasoning (~23K tokens each) and the writer never started |

- **Friction (performance): agentic research is prefill-bound, and the tt vLLM platform disables prefix caching.** Across run 4, Ornith prefilled ~2,100 tok/s and decoded ~27 tok/s, roughly 80:1. Every agent turn re-sends its whole 10–18K-token history and recomputes it, because `Automatic prefix caching is disabled` (chunked prefill is off too). Decode benchmarks don't predict this workload. Prefix caching would likely be the biggest single speedup for deep research on Blackhole.
- **Friction (harness fit): AI-Q's deep path assumes a frontier model.** Its defaults run the planner and researchers on Nemotron Ultra 550B. A 35B-A3B in those roles doesn't know when to stop, and ignores text nudges: our cache note, AI-Q's budget errors, and LangChain's retry message, which adds "Please try again." to a budget error.
- **Friction (configurability): AI-Q's limits can only be lowered.** Every resource limit is validated `le=default`, so raising one takes a source patch (`patches/aiq/0001`).
- AI-Q keeps only the last 10 tool results, truncated to 2,000 characters, so a researcher forgets earlier results and repeats searches.
- Ornith's stock chat template rejects system-only prompts, which AI-Q's intent classifier sends. Fixed with a one-line template change.
- With thinking on, `max_tokens: 32768` lets a looping reasoner run for ~30 minutes. Reasoning needs its own cap per step.
- `presence_penalty`, the usual cure for repetition, cuts Ornith's decode from ~123 to ~82 tok/s, so we don't use it.

## Stage 2: ingestion and B1 (2026-10-28)

**Result:** 84 FinanceBench filings (12,013 pages) indexed in 3 min 14 s. Naive RAG lifts FinanceBench from 17% to 73% ([LOG.md](LOG.md)).

- Text layer first works: only 44 of 12,013 pages are image-only, so OCR (stage 8) is a small tail for 10-Ks. Scanned corpora will differ.
- pdfium isn't thread-safe; extracting in threads segfaulted. Extraction runs in a process pool.
- embeddinggemma-2 on one chip embeds ~57K tokens/s, so indexing is bound by PDF parsing, not the chip.
- FinanceBench pages are 0-indexed (page 59 of 3M's 2018 10-K is the cash flow statement), which matters for evidence matching.

## Stage 3: engine and MCP tools (2026-10-28)

**Result:** `search` (web + corpus), `fetch_page` (HTML and PDF) and `get_evidence`, every passage with an evidence ID stored in Redis. Search takes ~1.4 s, a page fetch ~2 s.

- **Friction (search supply):** SearXNG scrapes public engines, and they push back. After the deep runs' ~5,000 searches, Brave and Google returned "too many requests" and DuckDuckGo a CAPTCHA, so search returned nothing. More engines (Bing, Yahoo, Mojeek, Startpage) and a 24 h Redis cache spread and cut the load. A product needs a licensed search API or its own index for sustained use.
- Vane's reranking cutoffs don't transfer between embedders: embeddinggemma-2 scores relevant snippets 0.7–0.85 and distinct same-topic pages ~0.87 to each other, so Vane's 0.75 near-duplicate rule would keep one result per query. Re-tuned (relative floor, 0.97 duplicates).
- Snippet and page passages needed separate IDs (`W<hash>` vs `W<hash>.<n>`); sharing one let a fetched page overwrite the snippet a report had cited.

## Stage 5: UI and demo (2026-10-28)

**Result:** AI-Q's web UI as "Tenstorrent Deep Research" (`patches/aiq/0003`), served against our job API; `ttdr research "…"` streams the agents' searches and fetches live and prints the cited report.

- The rebrand is 14 files: NVIDIA's logo is inline SVG and its green is hard-coded in components and a generated stylesheet, not only in theme tokens. Kept as a patch so AI-Q upgrades stay mechanical.
- `npm run dev` (Turbopack) dies on this host with "OS file watch limit reached"; the production build (`npm run build && npm start`) has no watchers and suits a demo better anyway.
- The UI's Sessions list only shows conversations started in the UI; API-submitted jobs show in `ttdr jobs` and AI-Q's debug console (`:8100/debug`).

## Stage 7: triage (2026-10-28)

**Result:** built and measured; **off by default**. Laya as a reranker lowers recall@10 from 75% to 48%; blended with the embedding score it's neutral ([LOG.md](LOG.md)). The eval gate did its job: without it this would have shipped as an "improvement".

## Stage 8: lazy OCR (2026-10-28)

**Result:** scanned pages are findable before OCR and readable after it. 44 image-only pages were embedded as page images in 4.1 s (embeddinggemma-2 is multimodal); a text query finds a scanned Walmart exhibit at rank 1. On first retrieval the page is queued and returned marked "OCR pending, unverified"; once the worker caches the text, search serves it.

- PaddleOCR-VL on the CPU is accurate (tables, contents pages) but slow: 42 s for a sparse page, 6.6 min for a dense one on 16 cores. That's why it's lazy, with backfill in idle time (most-retrieved first).
- Ornith's vision reads the same dense page in 2.8 min while sharing the chips with a deep job, with cleaner tables. The worker takes either backend (`--backend paddle|llm`); the design doc's CPU default keeps the chips for research.
- PaddlePaddle has no wheels for our Python 3.14 venv, so the worker is its own Python 3.12 project (and image), talking to the engine only through Redis.
- Redis' socket timeout must exceed the blocking pop's timeout, or an idle worker crashes.

## Stage 4 (continued): AI-Q as a service (2026-10-28)

**Result:** AI-Q runs as a job service (`nat serve`, then as a container) with our engine as its only source. On FinanceBench its shallow researcher scores 74.7%, tying naive RAG (73.3%) and 57 points over B0 ([LOG.md](LOG.md)). Deep research: see run 7 below.

- After the loop guards (patch 0002) researchers search distinct queries (27–29 of 29) and stop; with thinking on, 6 of 8 wrote notes in run 4, and run 6 got through 7 research rounds (178 searches, 374 page fetches) before AI-Q's own 1-hour wall clock stopped it. That limit is also capped at its default; patch 0001 raises it to 3 hours.
- **Friction (performance): forced tool calls slow the whole batch ~9×.** LangChain's structured output forces `tool_choice` on every planner and researcher turn; vLLM serves that with guided decoding, which drops every request in the batch from ~118 to ~13 tok/s. It's the main reason deep jobs take an hour, and why throughput flattens at 2 concurrent jobs.
- AI-Q needs no NVIDIA keys or extra services for this setup: SQLite job store, local Dask, auth off by default. As a container, its entrypoint runs Dask separately and then insists on `NAT_JOB_STORE_DB_URL`.
- Polling AI-Q's job API through one shared HTTP client hit closed keep-alive sockets under load (27 of 150 polls), while every job succeeded server-side. The eval client retries, and `ttdr eval retry` re-answers only failed items.
- The shallow researcher never abstains: 38 wrong vs B1's 18 wrong + 22 "I don't know".

## Stage 6: verify (2026-10-28)

**Result:** `ttdr verify` / `ttdr eval verify-run` check every cited claim against its source; one revision pass raises citation validity on AI-Q's answers from 27% to 39% with accuracy unchanged (74.7% → 74.7%) ([LOG.md](LOG.md)).

- **Friction (decision model):** Laya scores 42–49% on 113 claim-passage pairs, near chance; its 512-token state can't hold a 10-K table and the claim together. Kev-9B (64K-token states) is the intended upgrade but didn't serve: first it couldn't write its adapter into the shared model cache (its card documents a pre-download; the recipe now does it via `prefetch`), then its server spun at 100% CPU for 50+ minutes after loading weights, with no log output. Laya went back on chip 3.
- 50 of 128 cited claims point at pages the engine can't fetch (sec.gov blocks scripted clients). AI-Q cites the public URL it found rather than our corpus copy of the same filing, so those claims can't be checked. Mapping public filing URLs to corpus documents would close most of the gap.
- pdfium's thread-unsafety bit a second time: concurrent PDF fetches in the engine segfaulted silently (no traceback, the process just exits). One lock in `fetch_page` fixed it.

## Stage 9: perf and CI (2026-10-28)

**Result:** `ttdr bench load` (reports/hour, latency, prefill/decode split from vLLM's metrics), `ttdr eval ab` (one command, one blinded queue), `ttdr eval check` (CI floor), CI on push and a nightly QB2 workflow. Shallow jobs peak at ~245 reports/hour, reached at 2 concurrent jobs ([LOG.md](LOG.md)).

- The new Ornith bundle's prefix caching makes a repeated 29K-token prompt 15× faster (6.2 s → 0.4 s); 26% of a day's 8.0M prompt tokens hit the cache.
- Before prefix caching, agent turns were prefill-bound (~80:1 prompt:output tokens). With it, request time is dominated by decode, and decode is dominated by the forced-tool-call batch effect (stage 4).
- The nightly workflow grades with the on-box judge: Claude subagents can't run unattended in CI without an API key, which this project avoids. Decisions still use the blinded Claude judge.

## Stage 10: packaging (2026-10-28)

**Result:** from a fresh clone, `ttdr up` (with models already served and image layers cached) brings up all endpoints in 19 s; the first cited report arrives 35 s after `git clone`. Cold, the box adds model serving (~15 min sequentially) and image builds (~3 min).

- **Friction (tt CLI):** `tt launch` has a fixed list of clients (opencode, aider, hermes, openwebui, …) and no plugin hook, so `tt launch deep-research` needs a tt-cli change. `ttdr up` is what it would call; the recipe already carries everything tt needs (bundles, profiles, ports, chat templates, adapters to pre-fetch, serve order).
- **Friction (chip allocation):** serving two models at once let tt give Kev chip 0 and Ornith chips 1 and 3, which aren't a board pair, so Ornith couldn't open its mesh. `ttdr up` serves strictly in `serve_order`, largest first.
- Images: engine 974 MB, OCR worker 4.3 GB (PaddlePaddle), AI-Q and UI built from the patched clone with AI-Q's own Dockerfiles. App containers use host networking so the recipe's localhost URLs work unchanged.
- Packaging bugs the fresh-install test caught: Docker creates missing bind-mount sources as root (now created first); `compose down` would have removed Redis and its caches (now only app services stop); OCR jobs carried host paths, so a worker from another checkout drained the queue (now corpus-relative, with retries).

## Modularity pass (2026-10-28)

**Result:** the stack follows the recipe: a model registry with roles, optional components that switch off cleanly, AI-Q's config rendered from the recipe, and a second top layer verified. Hermes, with Ornith and our MCP server as its only tools, answered a FinanceBench question with a correct cited figure in 65 s ([CLIENTS.md](CLIENTS.md), [ARCHITECTURE.md](ARCHITECTURE.md)).

- `tt launch hermes` configures the model but not MCP servers; one `hermes mcp add` connects the engine. A tt-cli option to attach MCP servers at launch would make the engine a first-class tt client.
- The MCP server has no authentication by default, so it stays on localhost; for remote use, an SSH tunnel or the optional bearer token (`TTDR_MCP_TOKEN`).

## Deep run 7: one researcher stalled the job (2026-10-28)

**Result:** cancelled after 2 h 14 min, no report. Five of six researchers finished within 50 minutes; the sixth spent 75 minutes in a loop: every reply thought until the 8,192-token cap, so it had no tool call; LangChain's structured-output retry asked again with the cut-off reply appended (+7.6K prompt tokens each time); each of those calls ran 12 minutes on the forced-tool-call slow path (~11 tok/s). The orchestrator waits for all researchers, so one stuck agent stalls the job. Nothing in the logs said so: we found it from the token counts in AI-Q's event store (`job_events`), since async jobs write no checkpoints.

- Fix: `patches/aiq/0005` (thinking off after a truncated reply, "return your result now" after three, 40 model calls per agent run).
- Lesson for operations: a job needs a progress signal (last tool call, last finished agent) surfaced in `ttdr jobs`, not just "running".

## Deep run 8: no stall, but out of time (2026-10-29)

**Result:** with patch 0005 nothing stalled (`ttdr jobs` never showed more than 4 idle minutes), but the job hit AI-Q's 3-hour limit (`DeepResearchExecutionTimeout`) still researching: 33 researcher runs, 656 searches, 1,720 page fetches, 688 model calls, no report. The orchestrator kept planning new research batches (the patched budget allows 60 queries) and never reached the writing step. The budgets we raised to stop early runs from ending empty-handed were now too generous for ~16 s per model call.

- Fix: `patches/aiq/0006` gives research half the job's time. After that, `run_research_batch` returns "research time is used up, write the report now" instead of starting researchers, so the job always finishes with a report from what it found.

## Deep run 9: the first deep report (2026-10-29)

**Result:** a 2,500-word cited report in **1 h 55 min** ([reports/deep-aiq-blackhole-blackwell-mi350.md](reports/deep-aiq-blackhole-blackwell-mi350.md)). Timeline: source routing and planning 20 min; three research batches (15 researchers, 276 searches, 260 page fetches, 336 model calls) 90 min; a fourth batch at 1 h 50 min got patch 0006's "write now" reply, and the writer finished 5 minutes later. No stalls.

- AI-Q's writer cites with numbered URLs ([1]…[26]), not our evidence IDs, so `ttdr verify` can't check its claims directly. Mapping URLs back to evidence IDs (or asking the writer for IDs) is open.
- Time is dominated by research batches of ~30 min each. The forced-tool-call slow path (open question 2) is the obvious lever; the auto-tool-choice A/B measures it.

## Stage 11 and wrap-up: harnesses and the tool-call lever (2026-10-29)

**Result:** every stage's gate is met (status in [PLAN.md](PLAN.md)). The stack runs from one recipe on one QB2: the LLM, embedder and decision model on the chips, and engine, harness, UI and OCR in containers. Two harnesses and Hermes drive the same MCP engine. Numbers are in [LOG.md](LOG.md).

**For managers:**
- **The biggest speedup came from the harness, not the model.** One forced tool call or JSON-schema request takes the whole batch off Blackhole's fused sampler (~118 → ~13 tok/s). AI-Q forces one on every agent turn. Letting the model choose cut an AI-Q deep report from 1 h 55 min to 18 min, with accuracy unchanged. Deep Agents never forces a call and writes the same report in 3 min. Guided decoding on device would remove this trap for every customer harness; worth raising with the inference-server team.
- **An off-the-shelf harness beat the reference one.** LangChain Deep Agents over our MCP engine tied AI-Q on FinanceBench (74.7% each, fewer confident wrong answers) and wrote the deep report that both blinded graders preferred (7 vs 4–5). AI-Q's report misidentified one of the three chips. One deep question, so this is indicative; DeepResearch Bench is next.
- **Agent jobs need wall-clock budgets, not only call budgets.** Deep runs 1–8 produced no report: they stalled, looped or ran out of time. Patches 0005 (truncation guard) and 0006 (research gets half the time, then write) made every later run finish.

Details:
- Deep Agents needed three fixes before it was fair to compare. Researchers left evidence IDs out of their notes (fixed with a format example, plus a middleware that attaches the passages behind the notes). They called Deep Agents' built-in `grep`/`ls` file tools as if they searched the corpus ("I have no search access"); now only the engine's tools are offered. The model's own preamble and Sources list are dropped.
- `ttdr verify` found 0 claims in the first Deep Agents report: its citations follow the full stop ("… 10 TB/s. [W586efbd0.8]"), and the sentence splitter cut before the bracket. Fixed; 42% of its 26 claims verify with Laya (a lower bound, given Laya's weakness).
- AI-Q's deep writer cites numbered URLs, not evidence IDs, so its deep reports can't be verified claim by claim yet (open question 4).
- The OCR backfill finished: all 44 image-only pages have text.

## Decision-model bake-off (2026-10-29)

- **Friction (Kev bundle):** Kev-9B hangs at 100% CPU after loading weights when it boots from its own tensor cache. The CLM card documents the cause, a tt-metal version that needs `TT_METAL_PINNED_MEMORY_CACHE_LIMIT_BYTES=0`; CLM's bundle sets it, Kev's doesn't. With it, Kev is up in under a minute (it hung for 50+ minutes at stage 6). `tt serve` can't pass an environment variable, so the workaround is tt-model's printed `docker run` line (SETUP.md). The bundle should set it.
- **Friction (API drift):** Laya has `/v1/systemone/batch`; Kev returns 404 and CLM 405 for it. The client now falls back to one request per state.
- CLM's server answered `/v1/models` within minutes, but `tt serve` kept waiting on its own readiness check for 10+ minutes.
- **Callout:** none of the three tt-hous decision models beats the LLM we already serve at checking claims (Ornith 69%, Kev 69%, Laya 49%, CLM 35%), and verify-then-revise didn't improve answers with any of them. A fourth chip spent on a decision model doesn't pay for itself yet.

## Retrieval on the CPU while the chips are lent out (2026-10-30)

Everything here ran on the CPU (embeddinggemma-2 on the CPU matches the chip's vectors, cosine ≈ 1.000), with numbers in [LOG.md](LOG.md).

- **A reranker is the retrieval lever; nothing else we tried is.** Qwen3-Reranker-0.6B: recall@10 76% → 84%. Qdrant's built-in hybrid search (BM25 + dense): 76% → 68%. Docling's layout-aware chunking: a wash. MiniCheck as a claim checker: 44% vs Ornith's 69%. The reranker is wired (`rerank` role, `recipes/rerank.toml`) and waits for its chip.
- **Import, don't build, held up:** every candidate was an off-the-shelf tool run as shipped (Qdrant's server-side BM25, Docling's own pipeline and chunker, published rerankers). None needed code of ours beyond a scratch eval harness.
- **Friction (Docling):** it can't share our environment (it needs huggingface-hub < 2 and pillow < 13; we pin newer), so it runs in its own venv or as docling-serve. ~1.7 s per page on 8 CPU threads; a full re-ingest of the corpus would take most of a working day without a GPU.
- **Friction (ours):** loading the A/B's Docling chunks as a second corpus overwrote 10,979 live FinanceBench points: point IDs are keyed by evidence ID (`DOC#pN.M`) without the corpus. Restored from the PDFs on the CPU (recall within 2 questions; a chip re-index and the 10 page-image points wait for the embedder); `ttdr corpus index` now refuses a document name another corpus has. No snapshot existed to roll back to.

## Reranker on a chip, a multimodal suite, and the LLM looking at pages (2026-10-31)

**Result:** naive RAG (B1) went from 72% to 80% on FinanceBench and from 58.5% to 69% on UniDoc-Bench, a new suite where half the answers live in figures and tables ([LOG.md](LOG.md)). Two changes did it: Qwen3-VL-Reranker-8B on chip 3, and showing Ornith the top 3 pages as images. Ornith turned out to be multimodal; it reads chart values the PDF text layer never had.

**For managers:**
- **The chips can see.** Image-only questions on UniDoc went from 26% to 44% correct once the LLM got the page images instead of extracted text. Reranking alone doesn't help them; looking does. This is the case for keeping a multimodal LLM as the default.
- **A reranker on one chip is worth more than a bigger embedder:** retrieval @1 33% → 55% on FinanceBench at ~1.5 s per search.

**Friction:**
- Chip 2 wouldn't initialise firmware after another user's job on the box ("Try resetting the board"); `tt-smi -r` and a full re-serve fixed it. `tt serve` reports "opening the Tenstorrent device" failed without saying a reset is the fix.
- Ornith's engine died on concurrent image requests: "chunked prefill is not supported". vLLM sized the image-encoder budget from its 2,048-token default before the tt platform raised the step budget, so it split prefills at image boundaries. Launching with `--max-num-batched-tokens 262144` fixes it; the bundle should set it. A crashed engine leaves the container up returning 500s, so health checks that only list models miss it.
- Then it died again under mixed agent traffic: "image prompts are prefilled whole". The guard in the port is batch-wide, so a page image batched with any agent turn that had a prefix-cache hit kills the engine; 197 of 350 agent answers were lost. Our only fix is to turn off the prefix cache (`--no-enable-prefix-caching`), which costs agents their prompt reuse. The port should check per request, or keep image prefills out of mixed batches. **Worth raising with the Ornith port owners:** multimodal plus prefix caching can't be used together in production today.
- `ttdr up --recipe <overlay>` never reached the engine container, which always loaded `recipe.toml`: agents' tools ran without the reranker. Fixed (`TTDR_RECIPE` in `.env`).
- UniDoc's energy and construction archives share 11 identical PDFs; the new cross-corpus guard caught it, and the suite became one corpus.
- Deep Agents at concurrency 16 thrashes Ornith (3 researchers per question, ~48 conversations for 32 sequence slots, prefills without chunking stall every decode): 67 s per question of throughput vs 28 s at concurrency 4.

## 2026-11-03 · Ornith's mixed-vision build: prefix caching back on

- The bundle owner shipped a fix for the batch-wide "image prompts are prefilled whole" guard. Text requests are now prefix-cached; requests with images never are.
- `tt model pull` kept the old cached revision even though the Hub had a newer commit. `tt model pull --bundle --force` fetched the new image (`4ce68d7f2568`). The HF cache's `refs/main` still names the old commit, but `tt serve --dry-run` shows which image will run.
- Check: 6 rounds of 4 text requests sharing a ~25k-token prefix, batched with 2 fresh image requests, completed 36/36. The prefix-cache hit rate was 82% of prompt tokens, and rounds after the first took ~2.5 s vs 26.5 s. Image answers were correct both alone and batched.
- Why it matters: DRB runs are 96% prefill (16.0M prompt vs 0.57M generated tokens over 1 h 50 min), and the agents re-send the same growing context each turn.

## 2026-11-03 · Free web search: Bing answers only the first word

**For managers:** web results on DeepResearch Bench were poor because Bing, our main free engine, quietly answers only the first word of a query when it sees automated traffic. "Saint Seiya Hades surplices" returns pages about saints, and "Rhadamanthys stealer" returns messenger.com. Yahoo is Bing underneath, and none of the other engines we had configured returned results of their own. Yandex answers the full query and held up under a 96-query burst. Keyed APIs (Tavily, Brave, Serper) are the real fix, but switching the free path to Yandex is free.

- Probe: 32 research queries × 12 SearXNG engines, each result scored by the Qwen3-VL embedder. Yandex had a median similarity of 0.70, with 230 of 320 results passing the 0.6 cutoff. Bing had a median of 0.40, with 14 of 306 passing. Bing News did well where it returned anything (61 of 94), and OpenAlex returned academic hits (89 of 235).
- Mojeek, Startpage, Presearch and Marginalia returned results identical to Yahoo's when asked for by name, so they never ran on their own. Google, Qwant, DuckDuckGo and Brave are blocked (captcha, access denied or "too many requests").
- The 0.6 similarity cutoff, set for embeddinggemma, still separates junk from relevant results under Qwen3-VL. Yandex results below it were login pages and unrelated sites.
- Friction: the engine's web cache keyed on "searxng" plus the query, so two engines on different SearXNG instances would have served each other's results in an A/B. The key now names the instance.

## 2026-11-03 · The UI: showing the work, sources that link to pages, Deep Agents behind it

**For managers:** the web UI now shows how each answer was reached: which agents ran, every search and its results, and the intent decision. Every citation opens the source: the web page, or the exact page image of our document. The faster Deep Agents harness has its own copy of the UI on :3001, next to AI-Q's on :3000, both on the same engine.

- **Why Sources was empty.** AI-Q only accepts `[n]` citations backed by URLs it captured from tool results. Our engine labels passages with evidence IDs and gave documents `corpus://` URLs, so AI-Q dropped every citation. Patch 0007 numbers the IDs, or links the model's own numbered references, from the tool results. The engine now gives document passages http page links (`/pages/DOC/PAGE`, proxied by the UI server).
- **Friction (AI-Q):**
  - The side panel opened only for deep-research jobs; quick answers sent the UI almost nothing (patch 0008).
  - Parallel calls of one tool overwrote each other in the deep-research view (patch 0009).
  - Inside AI-Q, tool output reaches the source scraper JSON-escaped, so URLs came out as `…/7\nare`.
  - AI-Q's sanitizer drops IP-address URLs, a sensible anti-phishing rule, so page links need a hostname (`TTDR_PAGES_URL` in `.env`).
- **Friction (ours):** `ttdr up` rewrote `.env` wholesale, which would have deleted search API keys added by hand. It now updates only its own keys.
- Web and documents are separate toggles in both UIs. The engine serves `/mcp/web` and `/mcp/docs` with search pinned to one source.

## 2026-11-04 · Thinking on, QUEST ruled out for good, BrowseComp-Plus offline

**For managers:** Ornith now thinks before each planning and research step. Answers are better on both short questions and long reports, at about 35–40% more time per answer. QUEST, a model trained for deep research, lost to Ornith in every setting we tried. We added BrowseComp-Plus, a published benchmark with a fixed set of 100,000 web pages. It runs fully offline on the QB2, so we can compare against GPT-5, o3 and open agents without spending any search budget.

- **Why a model trained for research loses here.** QUEST learned one loop: one agent, Google results, LLM-summarised pages, a context condenser, up to 400 turns. Our harness works differently: a planner, short researchers with call limits, and a section writer. QUEST ignores the call limits and never stops (up to 147 model calls for a one-line answer). In its own loop it stops, but writes thin reports. Ornith was not trained for any particular loop, and our harness was tuned on it. RL-trained agents tend to fit the loop they were trained in, much as fine-tuned models fit their prompt format.
- **Thinking had a hidden cost.** The report writer has a token budget per section, and its reasoning counted against that budget. With thinking on everywhere, reports came out half as long and one stopped mid-section. Thinking on for the agents and off for the writer fixed it.
- **Friction (ours):**
  - The engine image copies the source in at build time. Engine changes don't reach the running stack until `docker compose --profile app build engine` (or `ttdr up --build`). We caught this when the offline switch did nothing.
  - Indexing read 100,000 files before sending its first embedding batch. By then the embedder had dropped the idle connection.
  - Free search fails silently. For a while Yandex returned nothing; 42 searches came back empty and one report became a refusal. Before the final runs we need a fallback engine, or a warning when a run's searches return no web results.
- **Friction (Ornith):** with thinking off, one section fell into a loop, the same four sentences about 50 times. The writer now drops long lines it has already written.

## 2026-11-04 (evening) · Retrieval: keyword search in, end-to-end A/Bs out of time

- **Retrieval was better than the headline number.** One search with the whole question found a BrowseComp-Plus gold document in the top 10 for 43% of questions. But agents that actually searched found one in 16 of 19 runs: they split the puzzle into short searches. The failures were Ornith thinking in circles. A retry without thinking when a call uses its whole budget fixed the empty answers.
- **Keyword search (Qdrant's own BM25) was the cheap retrieval win:** +10 points at 10 on BrowseComp-Plus, built in 5 minutes on CPU. It made no difference on 10-Ks. It's on by default for new corpora.
- **The reranker pool is the ceiling.** The reranker picks out nearly every gold document it is given, so recall depends on how many candidates reach it.
- **Friction (ours):**
  - **Bigger pools need the reranker to themselves.** On one shared reranker chip, 200-candidate requests from parallel runs built a backlog: tiny requests waited over 10 minutes, and 40% of searches timed out. Restarting the container cleared it.
  - **`kill $!` on a `nohup uv run` stops only the wrapper.** The eval kept running for 3 hours alongside its replacement.

## 2026-11-05 · A bigger LLM didn't help; serving all three models as one bundle did

- **Model size wasn't the bottleneck.** Ornith 122B, Qwen3.8-27B and Ornith 35B tied on 12-question FinanceBench and BrowseComp-Plus sets. The 122B spent three times the tokens on hard puzzles for the same score. At this harness and retrieval, more parameters buy nothing we can measure.
- **One bundle on four chips is faster than three bundles on their own chips.** The cohosted 35B decodes 197 tok/s for one user and 1,039 in total at 32 (vs 119 / 689), and the embedder and reranker lost nothing (cosine 0.9995, recall within 1 point).
- **Friction (tooling):**
  - `tt serve` refuses cohost bundles ("unsupported kind 'cohost'"); `tt-model serve BUNDLE --local-only --port 8000 --detach` works, and readiness is on the bundle's status port (:8006).
  - Qwen3.8's default bundle profile has no multi-user vision; one page-image request killed the engine and the container. Recipes now say `vision = false` and agents lose `read_page`.
  - A cold cohost start takes 9–12 minutes.

## 2026-11-05 (evening) · Users' own documents, through the UI we already had

- **Imported, not built:** AI-Q's UI already had a polished file-upload flow, switched off unless the backend advertises it. Implementing its small API in our server turned it on; no frontend code changed.
- **One library, not per-chat files:** AI-Q ties uploads to a chat and deletes them with it. For a standing private corpus, every chat's collection now maps to one shared library.
- **Friction (ours):** the cohosted embedder is text-only, which we hadn't hit because the benchmark corpora were indexed with the separate vision embedder. A scanned PDF failed to upload. Scans are now OCR'd while indexing on text-only embedders.
- **Friction (ours):** a reviewer spotted a 10-K cited in a fashion-design report. Every eval searched web and benchmark documents together, because "search everything" was the harness default. Web suites now search the web only, and the agents hear only of collections that hold documents, so a fresh install describes none.
