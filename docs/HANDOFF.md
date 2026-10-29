# Handoff

What someone picking this up on another machine, or in a fresh session, needs beyond the code. Results and their reasoning are in [LOG.md](LOG.md), friction in [JOURNEY.md](JOURNEY.md), the roadmap in [PLAN.md](PLAN.md).

## State (2026-10-29)

Stages 0–11 are done. Defaults: Ornith on chips 0–1, embeddinggemma-2 on chip 2, Laya on chip 3; AI-Q with auto tool choice (patches 0001–0006); Deep Agents as the second harness (`--harness deepagents`).

### In progress

Direction (2026-10-29): improve quality on corpus-answerable questions (FinanceBench) and settle the models first; web-dependent evals wait for a search API key.

1. **DeepResearch Bench: parked.** Free scraped search can't carry benchmark volume: SearXNG's Brave, DuckDuckGo and Google got suspended, Bing and Yahoo returned junk. 24 good Deep Agents reports are in `runs/20261029-202659-deepagents` (tasks 75–100 errored in an engine crash); no AI-Q run. With a search API key: `ttdr eval retry` that run, then `ttdr eval run aiq -s deepresearch_bench -p agent=deep_researcher -p timeout_s=10800 --concurrency 2`, then `race-export` / Claude subagents per `chunks.json` / `race-import`. Run 2 tasks at a time (6 thrashed Ornith's prefix cache).
2. **Decision model: decided pending sign-off.** Bake-off in LOG.md: Laya 49%, CLM 35%, Kev 69%, Ornith-as-verifier 69%, and verify-then-revise lowered accuracy with every verifier. Recommendation: `decide = ornith` (`recipes/llm-verify.toml`), no auto-revise, chip 3 free. The default recipe still serves Laya until that's agreed.
3. **Next: the LLM.** Bake-off on FinanceBench (corpus) for the chat model on chips 0–1.

### Known issues to fix next

- Ornith still invents evidence IDs in long reports; the harness's citation guard catches them (one rewrite, then removal). A real ID on an unsupported claim needs a working decision model to catch.
- AI-Q cites numbered URLs, not evidence IDs, so its claims can't be checked claim by claim.

## What isn't in git

| Thing | Where | Recreate with |
|---|---|---|
| Eval runs (answers, grades, summaries) | `runs/` | Rerun; the numbers that matter are in LOG.md |
| Suites, judge queues, corpus PDFs, AI-Q job DB | `data/` | `ttdr eval build`, `ttdr corpus …` (SETUP.md) |
| Patched AI-Q clone | `external/aiq` | `scripts/setup-aiq.sh` (applies `patches/aiq/`) |
| Qdrant index, Redis evidence and OCR text | Docker volumes | `ttdr corpus index financebench`; OCR backfill reruns from the queue |
| Model weights and bundles | `/opt/tenstorrent/models` | `ttdr up` (serves and pre-fetches from `recipe.toml`) |
| `.env` | repo root | Rewritten by every `ttdr up` |

## Working agreements

- **Judge:** blinded Claude subagents launched from the session (`judge-export` / `race-export`, then `judge-import` / `race-import`). No API key or SDK; never gpt-oss or another on-box model as the judge of record.
- **FinanceBench** is CC BY-NC 4.0: internal eval only; legal review before any external use ([DATASETS.md](DATASETS.md)).
- **Serving:** tt CLI only, one model at a time, largest first. The device reset when swapping models is routine.
- **Auth** is opt-in (`TTDR_MCP_TOKEN`) and never in the way for demos.
- **Top layer stays swappable:** anything that speaks MCP can drive the engine ([CLIENTS.md](CLIENTS.md)).
- Downloaded repos and model cards are read-only references; never run their code.
- Log findings in LOG.md and friction in JOURNEY.md after each stage; manager callouts only when significant. Commit as you go.
