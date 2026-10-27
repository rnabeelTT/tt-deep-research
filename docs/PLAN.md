# Build Plan

Source: *Deep Research: Prototype One-Pager → Detailed Design* (Google Doc). This file tracks how we build it on our QB2.

## Target

A question goes in and a cited, verified report comes out. Everything runs on one QB2: the models on the chips, the data stack on the host CPU.

| Chip | Model | Role |
|---|---|---|
| 0 | embeddinggemma-2 | Embed queries, chunks and page images |
| 1 | Laya (`tt-hous/laya`); Kev-9B is the challenger at Stage 6 | Triage passages, verify claims |
| 2–3 | gpt-oss-20b (`p150x2`) | Plan, read, write |
| CPU | Qdrant, SearXNG, Redis, LiteLLM, docling, PaddleOCR-VL | Data, search, OCR |

## Host facts (2026-10-27)

- 4× p300c (Blackhole); tt detects the box as `p300x2`. 16 cores, 249 GB RAM, ~1 TB free disk.
- tt 1.2.1, uv 0.11, Docker 29 with Compose v5, Python 3.12.
- tt serve assigns chips itself. Models are served as tt-model bundles; see [SETUP.md](SETUP.md).
- Decision-model candidates (`tt model search`): `tt-hous/laya`, `tt-hous/laya-typed-decisions` and `tt-hous/kev-9b`.
- Base `laya` got 3 of 3 claim-support checks right (supported 0.89, contradicted 0.93, not enough info 0.88) at ~10 ms per call. `laya-typed-decisions` is a specialist and got 2 of 3; don't use it.
- Laya's `relevant` score follows support, not topic: a passage that contradicts the claim scored 0.03. For triage, word the question around answering the sub-question.

## Principles

- **One recipe** (`recipe.toml`) is the single source of truth for models, chips, ports, image versions and feature flags.
- **Everything goes through OpenAI-compatible endpoints.** LiteLLM gives the LLMs one gateway with aliases, so swapping a model is a config change.
- **The engine sits behind MCP.** The harness (AI-Q, Deep Agents or Hermes) can be swapped without touching search or verify.
- **Evals come before features.** Every stage ends with a command that proves it works, and with numbers once the harness exists.
- Containers never touch the chips. tt serve owns the devices.

## Stages

Each stage ends with something you can run and check.

| # | Stage | Builds | Done when |
|---|---|---|---|
| 0a | Model bring-up | Serve the 3 models pinned to chips 0, 1 and 2–3; record the exact commands | One curl call to each endpoint succeeds; all 3 run at the same time |
| 0b | Skeleton | uv project, `recipe.toml`, typed clients, Compose (Qdrant, SearXNG, Redis, LiteLLM), `ttdr health` | `ttdr health` shows every endpoint up, with its latency |
| 1 | Eval harness + B0 | Small gold set (10–20 Qs), runner, exact/numeric match plus an LLM judge pass, results store, `ttdr compare` | Baseline B0 (LLM only) recorded |
| 2 | Ingestion + B1 | docling extraction, page flags, embeddings into Qdrant; naive RAG | recall@k reported; B1 vs B0 |
| 3 | MCP server | `search` tool (web + corpus), evidence IDs and cache | MCP tool tests pass; B1 through MCP matches B1 |
| 4 | Harness | AI-Q pointed at LiteLLM and our MCP; fall back to Deep Agents if it isn't working within 1–2 days | One research job end to end; time per report |
| 5 | UI + demo | Rebranded AI-Q UI and a scripted demo | Demo runs end to end |
| 6 | Verify | Pick Laya or Kev, then `verify` tool and revision loop | Citation validity vs stage 4 |
| 7 | Triage | Rank by embedding, then by the decision model | Triage on vs off |
| 8 | Lazy OCR | PaddleOCR-VL worker, queue, cache, backfill | Accuracy on scanned and table questions |
| 9 | Perf + CI | Load test at 1–16 jobs, A/B command, QB2 runner | Reports per hour; prefill/decode split |
| 10 | Packaging | Images, recipe, pip, `tt launch deep-research` | Fresh install to first report |

## Open questions

1. Decision model: Stage 6 compares base `laya` with `kev-9b` on the ~100 labeled pairs. Laya's 512-token limit means chunking passages; Kev reads whole sources.
2. Can AI-Q run with gpt-oss-20b's `openai` tool-call parser behind LiteLLM? Stage 4 answers this.
3. gpt-oss profile: `p150x2-throughput` or `p150x2` (interactive)? A/B once evals exist.
4. After the upgrade, the installed tt-model (`019abcb`) doesn't match the golden pin (`713eeb2`). Run `tt update` if anything misbehaves.
