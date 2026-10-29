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
