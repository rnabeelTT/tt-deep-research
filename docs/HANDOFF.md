# Handoff

What someone picking this up on another machine, or in a fresh session, needs beyond the code. Results and their reasoning are in [LOG.md](LOG.md), friction in [JOURNEY.md](JOURNEY.md), the roadmap in [PLAN.md](PLAN.md).

## State (2026-10-30)

Stages 0–11 are done. Defaults: Ornith on chips 0–1, embeddinggemma-2 on chip 2, Laya on chip 3; AI-Q with auto tool choice (patches 0001–0006); Deep Agents as the second harness (`--harness deepagents`). The chips were lent out on 2026-10-30 (`ttdr down --models`; Qdrant, Redis, SearXNG still up) and retrieval was settled on the CPU meanwhile.

### Waiting on the board

Direction (2026-10-29): improve quality on corpus-answerable questions (FinanceBench) and settle the models first; web-dependent evals wait for a search API key.

1. **Reranker: wired, waiting for its chip.** Qwen3-Reranker-0.6B on the CPU lifted recall@10 76% → 84% (LOG.md); the bring-up target is Qwen3-Reranker-4B (or Qwen3-VL-Reranker for page images). The engine has a `rerank` role (`recipes/rerank.toml`): search fetches 30 by embedding, reranks, keeps the top k; any Cohere-style `POST /v1/rerank` server works. vLLM needs the `hf_overrides` in that recipe, and the client wraps query and documents in Qwen's prompt (templates in the recipe). Once up: `ttdr health --recipe recipes/rerank.toml`, then `ttdr corpus recall --rerank --recipe recipes/rerank.toml` (expect ≥ 84% @10 whole corpus; also try `--pool 50`: dense recall is 89% @30, 94% @50, 96% @100, so a wider pool gives the reranker headroom), then `ttdr eval run b1 -s financebench -p rerank=true --recipe recipes/rerank.toml` vs B1 (73.3%), then the harnesses.
2. **Re-index 30 filings on the chip.** An A/B load overwrote 10,979 FinanceBench points (JOURNEY.md). The 10,969 text chunks were restored with CPU vectors (recall@10 74.7% vs 76.0% before, near-ties flipping); the 10 image-only pages need the chip embedder. Once it's up: `.venv/bin/python data/ab/docling30/restore_chip.py` (a few minutes), then `ttdr corpus recall` should read 76.0% @10 again.
3. **Docling tables, end to end (optional).** Docling didn't beat our chunking on recall; whether its markdown tables help the LLM read is open. Staged: `.venv/bin/python data/ab/docling30/corpus.py load`, then `ttdr eval run b1 -s financebench-docling30 -p known_doc=true -p corpus=financebench-docling` vs the same with `corpus=financebench`, then `corpus.py drop`.
4. **Then the LLM.** With the reranker in, compare bigger models (4-chip Ornith, Ornith-122B, gpt-oss-120b) on FinanceBench; co-hosting (tt-model-manager #192) could put the embedder beside a 4-chip LLM, but mixed kinds (vLLM + tt-dit-server) and one tt-metal build per process block it today.
5. **Decision model: decided pending sign-off.** `decide = ornith` (`recipes/llm-verify.toml`), no auto-revise; the default recipe still serves Laya until that's agreed. A rerank chip can take Laya's chip 3.
6. **DeepResearch Bench: parked** until a search API key (free SearXNG engines got suspended). With one: `ttdr eval retry runs/20261029-202659-deepagents`, then the AI-Q run (`-p agent=deep_researcher -p timeout_s=10800 --concurrency 2`), then `race-export` / Claude subagents / `race-import`.

### Settled on the CPU (2026-10-30, LOG.md)

| Tried | Result | Decision |
|---|---|---|
| Rerankers (bge-reranker-v2-m3, Qwen3-Reranker-0.6B) | recall@10 76% → 82% / 84% | Adopt; on a chip |
| Qdrant built-in hybrid (BM25 + dense, RRF/DBSF) | recall@10 76% → 68% / 65% | Dense only |
| Docling conversion + HybridChunker | wash (@5 +), slightly behind @1/@10 | Not for PDFs; docling-serve for other formats when needed |
| MiniCheck-Flan-T5-Large as claim checker | 44% vs Ornith 69% | Not adopted (`check` kind stays) |

### Known issues to fix next

- Ornith still invents evidence IDs in long reports; the harness's citation guard catches them (one rewrite, then removal). A real ID on an unsupported claim needs a working decision model to catch.
- AI-Q cites numbered URLs, not evidence IDs, so its claims can't be checked claim by claim.
- Qdrant point IDs are keyed by evidence ID without the corpus, so a document name must be unique across corpora (`ttdr corpus index` refuses a clash). Take a Qdrant snapshot before experiments that write to the `chunks` collection.

## What isn't in git

| Thing | Where | Recreate with |
|---|---|---|
| Eval runs (answers, grades, summaries) | `runs/` | Rerun; the numbers that matter are in LOG.md |
| Suites, judge queues, corpus PDFs, AI-Q job DB | `data/` | `ttdr eval build`, `ttdr corpus …` (SETUP.md) |
| Patched AI-Q clone | `external/aiq` | `scripts/setup-aiq.sh` (applies `patches/aiq/`) |
| Docling A/B chunks, vectors, loader | `data/ab/docling30` | The scripts there (`convert_chunk.py` in a Docling venv, `ab_recall.py`) |
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
