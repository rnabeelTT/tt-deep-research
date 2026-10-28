# Build Plan

Source: *Deep Research: Prototype One-Pager → Detailed Design* (Google Doc). This file tracks how we build it on our QB2.

## Target

A question goes in and a cited, verified report comes out. Everything runs on one QB2: the models on the chips, the data stack on the host CPU.

| Chip | Model | Role |
|---|---|---|
| 0–1 | Ornith 1.5 35B-A3B (`p300`, prefix caching) | Plan, read, write |
| 2 | embeddinggemma-2 | Embed queries, chunks and page images |
| 3 | Laya (`tt-hous/laya`); Kev-9B is the challenger | Verify claims (triage off) |
| CPU | Qdrant, SearXNG, Redis, docling, PaddleOCR-VL | Data, search, OCR |

## Host facts (2026-10-27)

- 4× p300c (Blackhole); tt detects the box as `p300x2`. 16 cores, 249 GB RAM, ~1 TB free disk.
- tt 1.2.1, uv 0.11, Docker 29 with Compose v5, Python 3.12.
- tt serve assigns chips itself. Models are served as tt-model bundles; see [SETUP.md](SETUP.md).
- Decision-model candidates (`tt model search`): `tt-hous/laya`, `tt-hous/laya-typed-decisions` and `tt-hous/kev-9b`.
- Base `laya` got 3 of 3 claim-support checks right (supported 0.89, contradicted 0.93, not enough info 0.88) at ~10 ms per call. `laya-typed-decisions` is a specialist and got 2 of 3; don't use it.
- Laya's `relevant` score follows support, not topic: a passage that contradicts the claim scored 0.03. For triage, word the question around answering the sub-question.

## Principles

- **One recipe** (`recipe.toml`) is the single source of truth for models, chips, ports, image versions and feature flags.
- **Clients call model endpoints directly**, as listed in the recipe, so swapping a model is a recipe change. A gateway such as LiteLLM is deferred until we need several replicas, live A/B routing or many external clients. Until then it's only another hop that can mangle gpt-oss tool calls.
- **The engine sits behind MCP.** The harness (AI-Q, Deep Agents or Hermes) can be swapped without touching search or verify.
- **Evals come before features.** Every stage ends with a command that proves it works, and with numbers once the harness exists.
- Containers never touch the chips. tt serve owns the devices.

## Stages

Each stage ends with something you can run and check. Done: **0a–10**. Results per stage are in [LOG.md](LOG.md); friction in [JOURNEY.md](JOURNEY.md).

| # | Stage | Builds | Done when |
|---|---|---|---|
| 0a | Model bring-up | Serve the 3 models pinned to chips 0, 1 and 2–3; record the exact commands | One curl call to each endpoint succeeds; all 3 run at the same time |
| 0b | Skeleton | uv project, `recipe.toml`, typed clients, Compose (Qdrant, SearXNG, Redis), `ttdr health` | `ttdr health` shows every endpoint up, with its latency |
| 1 | Eval harness + B0 | Small gold set (10–20 Qs), runner, exact/numeric match plus an LLM judge pass, results store, `ttdr compare` | Baseline B0 (LLM only) recorded |
| 2 | Ingestion + B1 | docling extraction, page flags, embeddings into Qdrant; naive RAG | recall@k reported; B1 vs B0 |
| 3 | MCP server | `search` tool (web + corpus), `fetch_page` with extraction, evidence IDs and cache. Port Vane's (MIT, ex-Perplexica) SearXNG search, scraping and re-ranking logic, and its SearXNG settings | MCP tool tests pass; B1 through MCP matches B1 |
| 4 | Harness | AI-Q pointed at the model endpoints and our MCP; fall back to Deep Agents if it isn't working within 1–2 days | One research job end to end; time per report |
| 5 | UI + demo | Rebranded AI-Q UI and a scripted demo | Demo runs end to end |
| 6 | Verify | Pick Laya or Kev, then `verify` tool and revision loop | Citation validity vs stage 4 |
| 7 | Triage | Rank by embedding, then by the decision model | Triage on vs off |
| 8 | Lazy OCR | PaddleOCR-VL worker, queue, cache, backfill | Accuracy on scanned and table questions |
| 9 | Perf + CI | Load test at 1–16 jobs, A/B command, QB2 runner | Reports per hour; prefill/decode split |
| 10 | Packaging | Images, recipe, pip, `tt launch deep-research` | Fresh install to first report |

## Status at stage 10 (2026-10-28)

| # | Gate | Result |
|---|---|---|
| 1 | B0 recorded | FinanceBench 17%, SimpleQA 12% (Ornith) |
| 2 | recall@k; B1 vs B0 | recall@10 75%; B1 73% (+56) |
| 3 | Tool tests; B1 through MCP | 30 unit tests; `b1mcp` system |
| 4 | One job end to end; suite vs B0/B1; time per report | AI-Q shallow 75% (ties B1); shallow p50 26 s idle; deep: see LOG |
| 5 | Scripted demo | `ttdr research`, branded UI |
| 6 | Citation validity vs stage 4 | 27% → 39% after revision, accuracy unchanged; Laya weak (42–49% on pairs) |
| 7 | Triage on vs off | Off: Laya reranking lowers recall |
| 8 | Scanned pages | Found by page image before OCR; OCR text served after |
| 9 | Reports/hour; prefill/decode split | ~245 shallow reports/h, saturating at 2 jobs |
| 10 | Fresh install to first report | 35 s warm (models up, images cached) |

## Open questions

1. **Decision model.** Laya is near chance on claim support. Kev-9B didn't serve; get it running (or another 64K-state model) and re-run `ttdr eval claims-run`, then re-test triage.
2. **Forced tool calls.** Guided decoding takes the whole batch off the fused sampler (~9× slower). Fix on the server (guided decoding on device) or in the harness (auto tool choice with a text fallback for AI-Q's structured outputs).
3. **Abstention.** AI-Q's shallow researcher never says "I don't know"; B1 does. Prompt for it and measure wrong answers, not only accuracy.
4. **Citations to our corpus.** AI-Q cites public filing URLs we can't fetch; map them to corpus documents so every claim is checkable.
5. `nat eval` and DeepResearch Bench (English half) aren't wired in yet. Decided: grade it with our blinded Claude judge (scores not comparable with the public leaderboard).
6. `tt launch deep-research` needs a plugin hook in tt-cli; `ttdr up` is the entry point it would call.
7. Hand-grade ~30 answers to calibrate the Claude judge (design doc).

## Stage 11 (stretch)

- Hermes skill: `.hermes/skills/qb2-deep-research/` (done).
- Deep Agents harness over MCP: `ttdr research --harness deepagents` (built); comparison with AI-Q on FinanceBench and one deep question pending.

## Later (after the stack is settled)

- **Vanilla AI-Q baseline:** same Ornith, AI-Q's own sources (Tavily web search, its knowledge layer over the same PDFs), no patches, vs AI-Q on our engine. A Tavily free-tier key (about 1,000 searches a month) covers a sampled comparison, not full suites.
- **New eval sets:** DeepResearch Bench (English half) first, then MMLongBench-Doc (charts, tables, scans by evidence type), then FreshQA. Licences recorded in DATASETS.md before use.
- **Model-swap A/Bs:** Kev vs Laya, LLM bake-offs.
