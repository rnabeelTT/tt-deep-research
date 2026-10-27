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
| `compose.yaml`, `config/` | Data services: Qdrant, SearXNG, Redis |
| `src/ttdr/` | `recipe` loader, `clients` (LLM, embeddings, Laya), `health`, `cli` |
