# tt-deep-research

Deep research on a Tenstorrent QB2: an agent breaks a question into sub-questions, reads the web and private documents, and writes a cited, verified report.

```bash
uv sync
docker compose up -d
uv run ttdr health
```

Model serving and first-time setup are in [docs/SETUP.md](docs/SETUP.md). The roadmap is in [docs/PLAN.md](docs/PLAN.md).

| Path | What |
|---|---|
| `recipe.toml` | Models, bundles and endpoints: the single source of truth |
| `compose.yaml`, `configs/` | Data services (Qdrant, SearXNG, Redis), AI-Q config, chat templates |
| `src/ttdr/` | `recipe` loader, `clients` (LLM, embeddings, Laya), `health`, `cli` |
| `src/ttdr/corpus/` | Private documents: PDF text and page flags, chunks, Qdrant store ([SETUP.md](docs/SETUP.md#documents)) |
| `src/ttdr/engine/` | Research engine: web + corpus search, page fetch, evidence store |
| `src/ttdr/mcp_server.py` | The engine as MCP tools: `search`, `fetch_page`, `get_evidence` |
| `src/ttdr/evals/` | Eval harness ([EVALS.md](docs/EVALS.md)) |
| `patches/aiq/` | Small patches to the pinned AI-Q clone |

Third-party credits are in [NOTICE.md](NOTICE.md).
