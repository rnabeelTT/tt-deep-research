# Handoff

What someone picking this up on another machine, or in a fresh session, needs beyond the code. Results and their reasoning are in [LOG.md](LOG.md), friction in [JOURNEY.md](JOURNEY.md), the roadmap in [PLAN.md](PLAN.md).

## State (2026-10-29)

Stages 0–11 are done. Defaults: Ornith on chips 0–1, embeddinggemma-2 on chip 2, Laya on chip 3; AI-Q with auto tool choice (patches 0001–0006); Deep Agents as the second harness (`--harness deepagents`).

### In progress

1. **DeepResearch Bench, both harnesses** (English half, 50 tasks, `-s deepresearch_bench`).
   - Deep Agents: `runs/20261029-193856-deepagents` (started 19:38 UTC, 2 tasks at a time, ~3–5 min per report). Earlier attempts are superseded: one lost 27 tasks to an engine segfault (fixed: pdfium pages closed inside the lock); one ran 6 at a time, which thrashed Ornith's prefix cache on ~100k-token researcher contexts (14 min per report); one was hurt by the engine's repeat-search nudge being global across clients (fixed: 10-minute window).
   - AI-Q: starts after it (same script, 2 at a time), `ttdr eval run aiq -s deepresearch_bench -p agent=deep_researcher -p timeout_s=10800 --concurrency 2`.
   - Then grade: `ttdr eval race-export <deepagents run> <aiq run> --out data/judge/race-drb`, one Claude subagent per entry in `chunks.json` (it reads `rubric.md` and its `task-*.md` files and writes `verdicts-NN.jsonl`), then `ttdr eval race-import data/judge/race-drb --judge claude-opus-5-5`. Log in LOG.md and JOURNEY.md.
2. **Decision-model bake-off on chip 3** (approved, runs after the benchmark): Kev-9B (`recipes/kev.toml`) and CLM-v0.1-8B (`recipes/clm.toml`) against Laya on the 113 claim-passage pairs (`ttdr eval claims-run --recipe …`). Stop Laya, serve the challenger, run, swap back. Kev's server hung after loading weights last time (JOURNEY, stage 6). The engine's `verify` calls `/v1/systemone/batch`; Kev's and CLM's cards don't list it, so the client may need a fallback to single calls. Clef needs two chips and is out of scope.

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
