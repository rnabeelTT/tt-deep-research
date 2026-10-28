# Clients: the swappable top layer

The engine is an MCP server (`services.mcp` in the recipe, `http://localhost:9901/mcp` by default). Anything that speaks MCP can sit on top of it. AI-Q is the default harness; Hermes, coding agents and your own scripts work the same way.

| Tool | Does |
|---|---|
| `search(query, sources, max_results, corpus)` | Web and/or private documents; every passage has an evidence ID |
| `fetch_page(url, focus, max_passages)` | Reads a web page or PDF; with `focus`, the most relevant passages |
| `get_evidence(ids)` | Full text of passages by evidence ID |
| `verify(claim, evidence_ids)` | Checks a claim against the passages it cites (only when the recipe has a `decide` role) |

## AI-Q (default)

`ttdr up` starts it with the engine as its only data source: UI on :3000, job API on :8100, `ttdr research "…"` from the terminal.

## Hermes

Hermes is in the tt CLI (`tt launch hermes`), which points it at a served model but doesn't add MCP servers. Add the engine once, then launch as usual:

```bash
hermes mcp add ttdr --url http://localhost:9901/mcp   # press Enter at the token prompt (no auth), Y to enable the tools
hermes mcp test ttdr                                  # lists search, fetch_page, get_evidence, verify
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

The MCP server has no authentication and listens on localhost only. Reach it from a laptop over SSH rather than opening the port:

```bash
ssh -L 9901:localhost:9901 <user>@<qb2-host>    # then use http://localhost:9901/mcp locally
```

Exposing it on the network needs an auth layer in front first (a reverse proxy with tokens), which isn't built yet.

## What a client needs to do well

- **Cite evidence IDs.** The server's instructions ask for `[ID]` citations; `ttdr verify report.md` can then check them.
- **Stop searching.** Repeated identical searches come back with a note to search differently or answer. Small models in AI-Q also needed hard caps (`patches/aiq/0002`); other harnesses may need their own.
