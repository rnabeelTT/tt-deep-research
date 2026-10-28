# Architecture

How the pieces fit, what each one promises the others, and where a change goes. The build history is in [PLAN.md](PLAN.md); setup steps are in [SETUP.md](SETUP.md).

## Pieces

```
                 recipe.toml  ──  models (registry) · roles · services · features
                      │
   ttdr up ───────────┼──────────────────────────────────────────────────────────┐
                      ▼                                                          ▼
  tt serve (chips)   LLM · embedder · decision model · (optional extra models)   Compose
                      ▲                                                          │
                      │ OpenAI-style / System One HTTP                           │
  ┌───────────────────┴─────────────────┐    MCP     ┌───────────────┐  jobs  ┌──┴──────┐
  │ engine (ttdr mcp)                   │◀──────────▶│ harness: AI-Q │◀──────▶│ web UI  │
  │ search · fetch_page · get_evidence  │            │ (or any MCP   │        └─────────┘
  │ · verify                            │            │  client)      │◀── ttdr research
  └──┬────────┬─────────┬───────────────┘            └───────────────┘
     ▼        ▼         ▼
  Qdrant   SearXNG    Redis ◀── OCR worker
  chunks   web        evidence, caches, OCR queue
```

- **The recipe is the only place that names models, ports and options.** Everything else reads it: clients, health checks, `ttdr up`, the engine, the MCP tool list, and AI-Q's config (rendered from it).
- **The engine is the product; the harness is replaceable.** The engine sits behind MCP, so AI-Q, Hermes or Deep Agents can drive it without changes on either side.
- **Containers never touch the chips.** tt serves the models; Compose runs everything else with host networking, so the recipe's localhost URLs work inside containers.

## The recipe

```toml
[models.<name>]        # any number; kind = "chat" | "embed" | "decide"
kind = "chat"
bundle = "..."         # tt bundle; plus profile, chat_template, prefetch, serve_order
model = "..."          # model ID the server reports
url = "http://localhost:8000/v1"
sampling = {...}       # applied to every request (some bundles need it to stay fast)
reasoning = "enable_thinking"   # or "reasoning_effort": how thinking is switched

[roles]                # which model does which job
llm = "<name>"         # required: plan, read, write
embed = "<name>"       # required
decide = "<name>"      # optional: verify, triage
judge = "<name>"       # optional: on-box judge (defaults to llm)
ocr = "<name>"         # optional: vision OCR on the chips instead of PaddleOCR on the CPU

[services]             # qdrant, redis, mcp required; searxng optional
[features]             # triage, ocr, ui
```

Overlays (`extends = "../recipe.toml"`) change only what differs. Setting a role or optional service to `""` removes it. Examples: `recipes/gpt-oss.toml` (another LLM), `recipes/kev.toml` (another decision model), `recipes/minimal.toml` (LLM and embedder only). The recipe is checked when loaded: unknown roles, missing required roles, and a role pointed at the wrong kind of model are all errors.

## Making changes

| Change | Where | Code? |
|---|---|---|
| Swap a model for another of the same kind | Add `[models.x]`, point the role at it | No |
| Remove the decision model | `decide = ""` | No: verify and triage switch off, the `verify` tool disappears |
| Remove web search | `searxng = ""` | No: search covers the corpus and says web is off |
| Remove OCR or the UI | `[features] ocr = false` / `ui = false` | No: their containers don't start; scanned pages are marked unverified |
| OCR with a vision model on the chips | `ocr = "<chat model>"` | No: `ttdr up` points the worker at it |
| Change how AI-Q's agents think, or their token limits | `configs/aiq/ttdr.yml` (role intent) | No |
| **Add a new kind of model** (reranker, ASR, …) | Client class + entry in `clients.KINDS`; kind in `recipe.Kind`; role in `recipe.ROLES`; probe in `health.PROBES`; use it in the engine | Small. Serving, health and `ttdr up` then work unchanged |
| Add an MCP tool | Engine method + `@server.tool()` in `mcp_server.py` | Small. AI-Q lists the tool automatically through its MCP client |
| Add a document collection | Fetcher in `corpus/sources.py`, then `ttdr corpus index <name>` | Small |
| Add an eval system or suite | `evals/systems.py` (`SYSTEMS`) or `evals/datasets.py` (`BUILDERS`) | Small |
| Replace or add a top layer (Hermes, coding agents, scripts) | Point any MCP client at `services.mcp` ([CLIENTS.md](CLIENTS.md)) | No change to the engine |

After any change, `ttdr health` shows what's live and `ttdr up` brings the stack to match the recipe.

## Contracts

| Between | Contract |
|---|---|
| Engine ↔ harness | Streamable HTTP, no auth by default (optional bearer token via `TTDR_MCP_TOKEN`). MCP tools `search(query, sources, max_results, corpus)`, `fetch_page(url, focus, max_passages)`, `get_evidence(ids)`, `verify(claim, evidence_ids)` (only with a decide role). Every passage carries an evidence ID. |
| Evidence IDs | Corpus chunks `DOC#pPAGE.N` (0-based page). Web snippets `W<hash>`; passages of a fetched page `W<hash>.N`. Stored in Redis (`ev:<id>`, 7 days). |
| Engine ↔ Qdrant | One collection `chunks`. Payload: corpus, eid, doc, page, text, flags. Image-only pages are stored as page-image vectors with empty text. |
| Engine ↔ OCR worker | Redis list `ocr:queue` of `{corpus, doc, page}`; text in `ocr:text:DOC#pPAGE`; `ocr:pending` dedupes; `ocr:hits` ranks backfill. Workers resolve PDFs under their own `--corpus-dir`. |
| Harness ↔ UI and clients | AI-Q job API on :8100: `POST /v1/jobs/async/submit`, then status, SSE stream and report (`docs/SETUP.md`). |
| Models | Chat and embedding models speak the OpenAI API; decision models speak System One (`/v1/systemone[/batch]`). |

## Where state lives

| State | Store | Lifetime |
|---|---|---|
| Chunks and vectors | Qdrant volume `ttdr_qdrant` | Until re-indexed |
| Evidence, search cache, page cache, OCR text and queue | Redis (in memory) | Restart of the Redis container; `ttdr down` leaves it running |
| AI-Q jobs, checkpoints | SQLite in the `aiq` volume (`data/aiq/` when run on the host) | 24 h job expiry |
| Eval runs | `runs/` (gitignored) | Kept |
| Rendered config | `data/aiq/config/ttdr.yml`, `.env` | Rewritten by every `ttdr up` |
