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

The default recipe serves three models on separate chips (what the A/Bs ran on); the cohost recipe serves the same three as one bundle on all four chips:

| Chip | Model | Role |
|---|---|---|
| 0–1 | Ornith 1.5 35B-A3B (Qwen3.5-based, vision) | LLM: planner and researchers with thinking on, writer with thinking off |
| 2 | Qwen3-VL-Embedding-8B | Embeds chunks (and page images) |
| 3 | Qwen3-VL-Reranker-8B | Reorders the top 30 search candidates |

Two ways to serve them with the same app; pick one recipe and pass it to `ttdr up`:

```bash
# Separate bundles, one per chip group (the default recipe): the embedder and reranker take images
uv run ttdr up

# One cohost bundle on all four chips: fastest; the embedder and reranker take images too (one per input)
# (needs tt-model with mixed cohost groups:
#  uv tool install --force git+https://github.com/rnabeelTT/tt-kernel-package-manager@raahem/cohost-mixed-groups)
uv run ttdr up --recipe recipes/ornith-35b-cohost.toml
```

The cohost bundle is built from `models/autoports/cohost_ornith_qwen3vl` on branch `raahem/cohost-ornith-qwen3vl` of [rnabeelTT/tt-metal](https://github.com/rnabeelTT/tt-metal/tree/raahem/cohost-ornith-qwen3vl/models/autoports/cohost_ornith_qwen3vl) (build, card and vision fixes); its runtime is `tt-model` branch `raahem/cohost-mixed-groups` of [rnabeelTT/tt-kernel-package-manager](https://github.com/rnabeelTT/tt-kernel-package-manager/tree/raahem/cohost-mixed-groups).

To switch, stop the running models first (`uv run ttdr down --models --recipe <the recipe now serving>`), then `ttdr up` with the other. Either way, uploaded scans are searchable by their page image at once and by their OCR text once it lands.

| Layer | What |
|---|---|
| Engine (MCP, :9901) | `search` (web + corpus, reranked), `fetch_page`, `read_page` (the LLM reads a page image), `find_in_document`, `open_pages`, `get_evidence`, `verify` |
| Web search | SearXNG: Yandex, Bing News, OpenAlex, with Brave as the fallback when they return nothing; keyed Tavily, Brave or Serper per recipe |
| Corpus | Qdrant: dense chunks, a BM25 keyword index whose 30 hits join the reranker's pool (on by default; `features.keywords = false` or `ttdr corpus index --no-keywords` per corpus), full-text index for in-document search |
| Harness | LangChain Deep Agents: planner, researcher subagents, outline-then-sections writer for report requests, per-section citation repair |

## Final runs (2026-11-05/06)

One locked recipe (`recipes/ornith-35b-cohost.toml`) on one QB2, free web search, blinded Claude graders. Per-type breakdowns and run IDs in [docs/LOG.md](docs/LOG.md).

| Benchmark | Questions | Ours | Published reference points |
|---|---|---|---|
| DeepResearch Bench (RACE) | 50 English tasks | **41.5** | Tongyi DeepResearch 30B below us; frontier products higher, see the deck |
| FinanceBench (10-Ks, shared index) | 150 | **82.7%** | GPT-4-Turbo, shared vector store 19%; whole filing in context 79% |
| UniDoc-Bench (figures and tables in PDFs) | 100 | **76%** | |
| SimpleQA | 100 | **89%** | Perplexity Deep Research 93.9 |
| FRAMES (multi-hop) | 100 | **66%** | Kimi K2-Thinking 87.0 (self-reported), Gemini 1.5 multi-step 66 |
| BrowseComp-Plus (fixed 100k pages, offline) | 96 of first 100 | **72.9%** | Qwen3-Embed-8B rows: GPT-5 71.7, o3 65.9, Tongyi 30B 44.5 |
| HLE, text-only | 50 of first 51 | **26%** | 2025 OpenAI DR 26.6; 2026 agents with tools ~54 |

BrowseComp-Plus and HLE were stopped early for time; every answered question was graded.

## Dev-set results

On one QB2, graded by blinded Claude subagents; dev sets are 40 questions (8 tasks for DRB), so differences under about 10 points are within noise. Details and run IDs in [docs/LOG.md](docs/LOG.md).

| | Result |
|---|---|
| FinanceBench dev (10-K filings) | **87.5%** (thinking on; 82.5% off). Published: GPT-4-Turbo with a shared vector store 19% |
| UniDoc-Bench dev (figures and tables in PDFs) | **80.0%** (thinking on; 75.0% off) |
| DeepResearch Bench dev (cited reports, RACE vs the reference; 0.5 = on par) | **0.445** (agents thinking, writer not; 0.381 off) |
| Deep Agents vs AI-Q deep, same engine, DRB dev | Quality tie (0.429 vs 0.428), Deep Agents 4.5× faster (24 vs 107 min median) |
| BrowseComp-Plus retrieval (100k pages, one search per question, gold doc in top 10) | 42.7% dense + rerank → **52.7%** with 30 BM25 hits added |
| LLM size, 12 FinanceBench + 12 BrowseComp-Plus | Ornith 122B and Qwen3.8-27B tie the 35B; the 35B cohosted with the embedder and reranker is the fastest (197 tok/s single, 1,039 at 32) and is the recommended serving |
| Models rejected by A/B | gpt-oss-20b (tie, fewer features), QUEST-35B-RL (lost every DRB task, doesn't stop in this harness), Ornith 122B (tie, 2× slower), Qwen3.8-27B (no multi-user vision, lower on 10-Ks) |

Competitor numbers come with different judges and subsets; [docs/COMPETITORS.md](docs/COMPETITORS.md) lists them with sources, and the final DRB comparison re-judges competitors' published reports with the same Claude graders.

## Your own documents

Documents live in collections; a fresh install has none, and agents are told only about collections that hold documents.

- **Upload in the web UI** (http://localhost:3000): add files at the chat input or in the Files tab of the data-sources panel. It takes PDF, Word (.docx), Markdown and text files, into the shared "My uploads" collection.
- **Named collections:** in the Files tab, "+ New collection" takes a name and a description (agents read it to decide when to search it); open a collection to add, watch and delete its files. From a folder: `ttdr corpus upload ~/contracts --collection contracts --description "Signed leases and supplier contracts"`.
- **Choose what to search:** every collection with documents is a switch in the data-sources panel, beside web search. Research searches only the ones switched on.

Each file is indexed in the background: text chunks embedded and BM25-indexed; scanned pages are queued for OCR and each page's OCR text is embedded and BM25-indexed when it lands. The scan is also embedded as a page image, so search finds it at once (with a recipe whose embedder sets `vision = false`, the upload instead waits up to 15 minutes for OCR, and pages read later are indexed in the background). Delete a file in the Files tab to drop it from the index. The UI caps uploads at 1,000 files and 2 GB (`TTDR_UPLOAD_MAX_FILES`, `TTDR_UPLOAD_MAX_MB`); the CLI has no cap. Benchmark document sets (`ttdr corpus fetch`) appear as collections only once indexed.

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

Recipes worth knowing: `recipe.toml` (default: the three models on separate chips; `recipes/rerank.toml` is the same setup under its old name, what evals use), `recipes/browsecomp-plus.toml` (offline, BrowseComp-Plus corpus only), `recipes/tavily.toml` (keyed web search, reserved for final runs), `recipes/ornith-35b-cohost.toml` (recommended: the 35B, embedder and reranker as one bundle on all four chips; serve it with `tt-model serve raahemnabeel/cohost-ornith-35b-qwen3vl --local-only --port 8000 --detach`), `recipes/ornith-122b.toml` and `recipes/qwen38-27b.toml` (the A/B alternatives).

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
