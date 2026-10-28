# tt-deep-research

Deep research on a Tenstorrent QB2: an agent breaks a question into sub-questions, reads the web and private documents, and writes a cited, verified report.

```bash
uv sync
uv run ttdr up                         # serve the models (tt), start services + app (Compose), check all
uv run ttdr research "your question"   # live progress, then a cited report
```

The UI is at `http://<host>:3000`. `ttdr up` reads everything from `recipe.toml`: which bundles to serve, in which order, their chat templates and pre-fetched adapters, and the endpoints. `ttdr down` stops the app (`--models` stops the models too). Step-by-step setup and the pieces behind `ttdr up` are in [docs/SETUP.md](docs/SETUP.md). How the pieces fit and where a change goes (new model, removed component, new tool) is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The roadmap is in [docs/PLAN.md](docs/PLAN.md); what we learned along the way is in [docs/JOURNEY.md](docs/JOURNEY.md).

| Command | What |
|---|---|
| `ttdr up` / `ttdr down` | Whole stack from the recipe |
| `ttdr research "…"` [`--shallow`] | Research through AI-Q's job API, with live progress |
| `ttdr verify report.md` [`--revise`] | Check each cited claim against its source with the decision model |
| `ttdr jobs` | Recent research jobs |
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
| `src/ttdr/mcp_server.py` | The engine as MCP tools: `search`, `fetch_page`, `get_evidence`, `verify` |
| `src/ttdr/evals/` | Eval harness ([EVALS.md](docs/EVALS.md)) |
| `patches/aiq/` | Small patches to the pinned AI-Q clone |
| `docker/`, `workers/ocr/` | Engine and OCR worker images; AI-Q and UI images build from the patched clone |
| `.github/workflows/` | CI: lint and unit tests on push; nightly evals on a self-hosted QB2 runner |

Third-party credits are in [NOTICE.md](NOTICE.md).
