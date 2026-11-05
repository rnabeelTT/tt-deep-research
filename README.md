# tt-deep-research

Deep research on a Tenstorrent QB2: an agent breaks a question into sub-questions, searches the web and private documents, and writes a cited report whose citations open the exact page. All models run on the box; web search is optional and pluggable.

```bash
uv sync
uv run ttdr up                         # serve the models (tt), start services + app (Compose), check all
uv run ttdr research "your question"   # live progress, then a cited report
```

The UI is at `http://<host>:3000` (Deep Agents, the default harness, with a research panel showing the plan, each researcher and every search); AI-Q's own harness is behind the same UI on :3001. `ttdr up` reads everything from `recipe.toml`: which bundles to serve, in which order, their chat templates, and the endpoints. `ttdr down` stops the app (`--models` stops the models too).

More: setup in [docs/SETUP.md](docs/SETUP.md), how the pieces fit in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), other clients of the engine (Hermes, coding agents, scripts) in [docs/CLIENTS.md](docs/CLIENTS.md), evals in [docs/EVALS.md](docs/EVALS.md) and [docs/DATASETS.md](docs/DATASETS.md), every A/B in [docs/LOG.md](docs/LOG.md), what we learned in [docs/JOURNEY.md](docs/JOURNEY.md), published competitor scores in [docs/COMPETITORS.md](docs/COMPETITORS.md), the demo plan in [docs/DEMO.md](docs/DEMO.md).

## The stack

Recommended serving (docs/LOG.md, 2026-11-05) is the same three models as one cohost bundle on all four chips; the default recipe still serves them separately:

| Chip | Model | Role |
|---|---|---|
| 0–1 | Ornith 1.5 35B-A3B (Qwen3.5-based, vision) | LLM: planner and researchers with thinking on, writer with thinking off |
| 2 | Qwen3-VL-Embedding-8B | Embeds chunks (and page images) |
| 3 | Qwen3-VL-Reranker-8B | Reorders the top 30 search candidates |

| Layer | What |
|---|---|
| Engine (MCP, :9901) | `search` (web + corpus, reranked), `fetch_page`, `read_page` (the LLM reads a page image), `find_in_document`, `open_pages`, `get_evidence`, `verify` |
| Web search | SearXNG: Yandex, Bing News, OpenAlex, with Brave as the fallback when they return nothing; keyed Tavily, Brave or Serper per recipe |
| Corpus | Qdrant: dense chunks, a BM25 keyword index whose 30 hits join the reranker's pool (on by default; `features.keywords = false` or `ttdr corpus index --no-keywords` per corpus), full-text index for in-document search |
| Harness | LangChain Deep Agents: planner, researcher subagents, outline-then-sections writer for report requests, per-section citation repair |

## Results so far

On one QB2, graded by blinded Claude subagents; dev sets are 40 questions (8 tasks for DRB), so differences under about 10 points are within noise. Details and run IDs in [docs/LOG.md](docs/LOG.md).

| | Result |
|---|---|
| FinanceBench dev (10-K filings) | **87.5%** (thinking on; 82.5% off). Published: GPT-4-Turbo with a shared vector store 19% |
| UniDoc-Bench dev (figures and tables in PDFs) | **80.0%** (thinking on; 75.0% off) |
| DeepResearch Bench dev (cited reports, RACE vs the reference; 0.5 = on par) | **0.445** (agents thinking, writer not; 0.381 off) |
| Deep Agents vs AI-Q deep, same engine, DRB dev | Quality tie (0.429 vs 0.428), Deep Agents 4.5× faster (24 vs 107 min median) |
| BrowseComp-Plus retrieval (100k pages, one search per question, gold doc in top 10) | 42.7% dense + rerank → **52.7%** with 30 BM25 hits added |
| BrowseComp-Plus accuracy | not yet graded (Published: GPT-5 71.7, o3 65.9, QUEST 61.0, Tongyi 44.5). Retrieval follow-ups in [docs/PLAN.md](docs/PLAN.md#future-improvements-retrieval-2026-11-04) |
| LLM size, 12 FinanceBench + 12 BrowseComp-Plus | Ornith 122B and Qwen3.8-27B tie the 35B; the 35B cohosted with the embedder and reranker is the fastest (197 tok/s single, 1,039 at 32) and is the recommended serving |
| Models rejected by A/B | gpt-oss-20b (tie, fewer features), QUEST-35B-RL (lost every DRB task, doesn't stop in this harness), Ornith 122B (tie, 2× slower), Qwen3.8-27B (no multi-user vision, lower on 10-Ks) |

Competitor numbers come with different judges and subsets; [docs/COMPETITORS.md](docs/COMPETITORS.md) lists them with sources, and the final DRB comparison re-judges competitors' published reports with the same Claude graders.

## Your own documents

Documents live in collections; a fresh install has none, and agents are told only about collections that hold documents.

- **Upload in the web UI** (http://localhost:3000): add files at the chat input or in the Files tab of the data-sources panel. It takes PDF, Word (.docx), Markdown and text files, into the shared "My uploads" collection.
- **Named collections:** `ttdr corpus upload ~/contracts --collection contracts --description "Signed leases and supplier contracts"` makes one from a folder (or adds to it). The description tells agents what's inside.
- **Choose what to search:** every collection with documents is a switch in the data-sources panel, beside web search. Research searches only the ones switched on.

Each file is indexed in the background: text chunks embedded and BM25-indexed; scanned pages are read by OCR while indexing when the embedder is text-only (the cohosted bundle), or embedded as page images otherwise. Delete a file in the Files tab to drop it from the index. The UI caps uploads at 1,000 files and 2 GB (`TTDR_UPLOAD_MAX_FILES`, `TTDR_UPLOAD_MAX_MB`); the CLI has no cap. Benchmark document sets (`ttdr corpus fetch`) appear as collections only once indexed.

## Commands

| Command | What |
|---|---|
| `ttdr up` / `ttdr down` | Whole stack from the recipe |
| `ttdr research "…"` [`--shallow`] [`--harness deepagents`] | Research with live progress |
| `ttdr verify report.md` [`--revise`] | Check each cited claim against its source |
| `ttdr jobs` | Recent research jobs, with progress |
| `ttdr corpus upload FOLDER` [`--collection NAME --description "…"`] | Add a folder of your own documents to a collection (default: the UI's uploads), through the running app (`--url`, default :8200) |
| `ttdr corpus fetch\|index\|keywords\|search\|recall NAME` | Private documents: download, embed and BM25-index (`--no-keywords` to skip BM25), add BM25 to an indexed corpus, search, measure recall (`--rerank`, `--keywords N`) |
| `scripts/setup-evals.sh` [`SNAPSHOT_DIR`] | Everything the evals need on a new machine (docs/EVALS.md) |
| `ttdr corpus snapshot\|restore` | Save the search indexes to files / load them on another machine, instead of re-embedding |
| `ttdr eval build\|run\|ab\|compare\|retry …` | Evals: `ttdr eval run deepagents -s financebench-dev --recipe recipes/rerank.toml --concurrency 4` |
| `ttdr eval judge-export\|judge-import` | Blinded queues for short answers, graded by Claude subagents |
| `ttdr eval race-export\|race-import` | DeepResearch Bench RACE queues (reports vs the reference) |
| `ttdr bench decode\|tools\|load` | Component and load benchmarks |

Recipes worth knowing: `recipe.toml` (default), `recipes/rerank.toml` (with the reranker; what evals use), `recipes/browsecomp-plus.toml` (offline, BrowseComp-Plus corpus only), `recipes/tavily.toml` (keyed web search, reserved for final runs), `recipes/ornith-35b-cohost.toml` (recommended: the 35B, embedder and reranker as one bundle on all four chips; serve it with `tt-model serve raahemnabeel/cohost-ornith-35b-qwen3vl --local-only --port 8000 --detach`), `recipes/ornith-122b.toml` and `recipes/qwen38-27b.toml` (the A/B alternatives).

## Layout

| Path | What |
|---|---|
| `recipe.toml`, `recipes/` | Model registry, roles, services and features: the single source of truth; overlays for alternatives |
| `compose.yaml`, `configs/`, `docker/` | Data services (Qdrant, SearXNG, Redis), engine and UI images, chat templates |
| `src/ttdr/engine/` | Research engine: web + corpus search, reranking, page fetch, evidence store |
| `src/ttdr/corpus/` | Documents: PDF text and page flags, chunks, Qdrant dense and BM25 stores, corpus sources |
| `src/ttdr/mcp_server.py` | The engine as MCP tools |
| `src/ttdr/harness.py`, `writer.py` | Deep Agents harness and the sections writer |
| `src/ttdr/quest.py` | QUEST's own ReAct scaffold over the engine (kept for the A/B record) |
| `src/ttdr/evals/` | Eval suites, runner, grading queues, RACE |
| `patches/aiq/` | Small patches to the pinned AI-Q clone |
| `docs/reports/` | Sample deep reports from each harness |

FinanceBench (CC BY-NC 4.0) and UniDoc-Bench are used for internal evaluation only; HLE, GAIA and BrowseComp-Plus data stay out of the repo. Third-party credits are in [NOTICE.md](NOTICE.md).
