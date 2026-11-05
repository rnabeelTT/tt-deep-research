# Clients: the swappable top layer

The engine is an MCP server (`services.mcp` in the recipe, `http://localhost:9901/mcp` by default). Anything that speaks MCP can sit on top of it. AI-Q is the default harness; Hermes, coding agents and your own scripts work the same way.

| Tool | Does |
|---|---|
| `search(query, sources, max_results, corpus)` | Web and/or private documents; every passage has an evidence ID |
| `fetch_page(url, focus, max_passages)` | Reads a web page or PDF; with `focus`, the most relevant passages |
| `find_in_document(doc, terms)` | Exact words inside one document |
| `open_pages(evidence_id, before, after)` | A passage's whole page and the pages after it |
| `read_page(evidence_id, question)` | The LLM looks at the page image (charts, scans, dense tables) |
| `get_evidence(ids)` | Full text of passages by evidence ID |
| `verify(claim, evidence_ids)` | Checks a claim against the passages it cites (only when the recipe has a `decide` role) |

## AI-Q

The alternative harness: `ttdr up` starts it with the engine's web and document endpoints as its data sources, its UI on :3001 and job API on :8100; `ttdr research "…"` from the terminal.

## LangChain Deep Agents (default)

The default harness since the 2026-11-03 A/Bs (docs/LOG.md: ties AI-Q on reports at a quarter of the time, +10 on UniDoc): `ttdr research --harness deepagents "…"` (and the `deepagents` eval system). Deep Agents plans with `write_todos`, sends each sub-question to a researcher subagent, and writes the cited report; `src/ttdr/harness.py` only connects the recipe's LLM, the engine's MCP tools and call limits. It asks for no structured output, so no request forces a tool call.

### In the web UI (:3000)

`ttdr ui-server` (`src/ttdr/ui_server.py`, default `127.0.0.1:8200`) speaks AI-Q's UI protocol on top of the Deep Agents harness, so the same UI image runs on either harness: Deep Agents on :3000, AI-Q on :3001. `ttdr up` starts both; by hand:

```bash
docker compose --profile app up -d deepagents ui-deepagents   # API :8200, UI http://<host>:3000
uv run ttdr ui-server --recipe recipe.toml                     # or the API from a checkout
```

| UI | Deep Agents |
|---|---|
| Chat message | Always a deep-research job: the reply is the UI's `job_escalation` signal; the harness decides report vs short answer itself |
| Data sources | "Web search" and "Our documents", both on by default; with only one on, `search` is pinned to it (`DeepAgentsHarness.sources`) |
| Agent cards | One per researcher (each `task` call), with its sub-question and notes; a "planner" card shows the request kind and plan budget |
| Tool calls | Under their researcher, with the full input and output |
| Tasks, sources, report | `write_todos`, every passage the tools returned (cited ones marked), the final report |

Jobs live in the server's memory: a reconnecting stream replays from its last event ID, but a restart forgets them. Cancel stops the run. With several researchers searching at once, the stock UI can attach a tool result to another call of the same tool name, because it matches results by name.

## Hermes

Hermes is in the tt CLI (`tt launch hermes`), which points it at a served model but doesn't add MCP servers. Add the engine once, then launch as usual:

```bash
hermes mcp add ttdr --url http://localhost:9901/mcp   # press Enter at the token prompt (no auth), Y to enable the tools
hermes mcp test ttdr                                  # lists the engine's tools
tt launch hermes                                      # Hermes on the recipe's LLM, with the engine's tools
```

Or add it to `~/.hermes/config.yaml` directly:

```yaml
mcp_servers:
  ttdr:
    url: http://localhost:9901/mcp
```

**Skill:** `.hermes/skills/qb2-deep-research/SKILL.md` teaches Hermes the research procedure for these tools (plan, search, fetch, cite evidence IDs, verify, deliver). Run `hermes skills trust` in this repo to load it, or copy the folder into `~/.hermes/skills/`.

Checked on 2026-10-28 with Hermes on Ornith: asked for 3M's FY2018 capital expenditure from the FinanceBench corpus, it called `search` and answered "$1,577 million [3M_2018_10K#p48.2]" in 65 s.

## Any other MCP client

Point it at the same URL with the streamable-HTTP transport (Claude Code: `claude mcp add --transport http ttdr http://localhost:9901/mcp`; opencode, Cursor and others have equivalents). From Python:

```python
from mcp import Client

async with Client("http://localhost:9901/mcp") as c:
    r = await c.call_tool("search", {"query": "3M capex 2018", "sources": "corpus"})
```

## From another machine

Bind the engine to all interfaces with `TTDR_MCP_HOST=0.0.0.0 uv run ttdr up` (or `ttdr mcp --host 0.0.0.0`) and use `http://<qb2-host>:9901/mcp`, or use an SSH tunnel (`ssh -L 9901:localhost:9901 <user>@<qb2-host>`). There's no auth by default; setting `TTDR_MCP_TOKEN` makes every request need `Authorization: Bearer <token>`.

## What a client needs to do well

- **Cite evidence IDs.** The server's instructions ask for `[ID]` citations; `ttdr verify report.md` can then check them.
- **Stop searching.** Repeated identical searches come back with a note to search differently or answer. Small models in AI-Q also needed hard caps (`patches/aiq/0002`); other harnesses may need their own.
