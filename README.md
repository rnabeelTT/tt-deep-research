# tt-deep-research

Deep research on a Tenstorrent QB2: an agent breaks a question into sub-questions, reads the web and private documents, and writes a cited, verified report.

```bash
uv sync
uv run ttdr up                         # serve the models (tt), start services + app (Compose), check all
uv run ttdr research "your question"   # live progress, then a cited report
```

The UI is at `http://<host>:3000` (Deep Agents; AI-Q's own harness behind the same UI on :3001). `ttdr up` reads everything from `recipe.toml`: which bundles to serve, in which order, their chat templates and pre-fetched adapters, and the endpoints. `ttdr down` stops the app (`--models` stops the models too). Step-by-step setup and the pieces behind `ttdr up` are in [docs/SETUP.md](docs/SETUP.md). How the pieces fit and where a change goes (new model, removed component, new tool) is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The engine works under other top layers too (Hermes, coding agents, scripts): see [docs/CLIENTS.md](docs/CLIENTS.md). The roadmap is in [docs/PLAN.md](docs/PLAN.md); what we learned along the way is in [docs/JOURNEY.md](docs/JOURNEY.md). Picking this up on another machine or in a new session: [docs/HANDOFF.md](docs/HANDOFF.md).

| Command | What |
|---|---|
| `ttdr up` / `ttdr down` | Whole stack from the recipe |
| `ttdr research "…"` [`--shallow`] [`--harness deepagents`] | Research through AI-Q's job API (default) or LangChain Deep Agents, with live progress |
| `ttdr verify report.md` [`--revise`] | Check each cited claim against its source with the decision model |
| `ttdr jobs` | Recent research jobs, with progress (agents done, searches, fetches, idle minutes) |
| `ttdr corpus fetch\|index\|search\|recall\|ocr-backfill` | Private documents |
| `ttdr eval run\|ab\|compare\|check …` | Evals ([docs/EVALS.md](docs/EVALS.md)) |
| `ttdr bench decode\|tools\|load` | Component and load benchmarks |

| Path | What |
|---|---|
| `recipe.toml`, `recipes/` | Model registry, roles, services and features: the single source of truth; overlays for alternatives |
| `compose.yaml`, `configs/` | Data services (Qdrant, SearXNG, Redis), AI-Q config, chat templates |
| `src/ttdr/` | `recipe` loader, `clients` (LLM, embeddings, Laya), `health`, `cli` |
| `src/ttdr/corpus/` | Private documents: PDF text and page flags, chunks, Qdrant store ([SETUP.md](docs/SETUP.md#documents)) |
| `src/ttdr/engine/` | Research engine: web + corpus search, page fetch, evidence store |
| `src/ttdr/harness.py` | LangChain Deep Agents over the engine's MCP tools (the second harness) |
| `src/ttdr/mcp_server.py` | The engine as MCP tools: `search`, `fetch_page`, `get_evidence`, `verify` |
| `src/ttdr/evals/` | Eval harness ([EVALS.md](docs/EVALS.md)) |
| `patches/aiq/` | Small patches to the pinned AI-Q clone |
| `docs/reports/` | Sample deep reports from each harness |
| `docker/`, `workers/ocr/` | Engine and OCR worker images; AI-Q and UI images build from the patched clone |
| `.github/workflows/` | CI: lint and unit tests on push; nightly evals on a self-hosted QB2 runner |

## Results so far

On one QB2 (Ornith 1.5 35B-A3B as the LLM), graded by blinded Claude subagents; details in [docs/LOG.md](docs/LOG.md).

| | Result |
|---|---|
| FinanceBench (150 questions, 10-K filings + web) | LLM alone 17%; naive RAG 73%; AI-Q 76–79%; Deep Agents 75% |
| Deep report (3 accelerators compared, ~2,500–3,500 words, cited) | Deep Agents 3 min; AI-Q 18 min (1 h 55 min with forced tool calls) |
| Deep report quality (two blinded graders, 1–10) | Deep Agents 7–7.5; AI-Q 4–5.5 |
| Shallow research throughput | ~245 reports/hour (saturates at 2 concurrent jobs) |
| Fresh clone to first report | 35 s (models served, images cached) |

FinanceBench (CC BY-NC 4.0) is used for internal evaluation only.

Third-party credits are in [NOTICE.md](NOTICE.md).
