"""The engine's MCP server: research tools any MCP client (AI-Q, Hermes) can call."""

from mcp.server.mcpserver import MCPServer

from ttdr.engine import Engine
from ttdr.engine.evidence import Evidence
from ttdr.recipe import Recipe

REPEAT_NOTE = (
    "You already ran this exact search; the results below are unchanged. "
    "Search for something different, or stop searching and give your answer.\n\n"
)


def _format(items: list[Evidence]) -> str:
    if not items:
        return "No results."
    return "\n\n".join(f"[{e.id}] {e.title}\nURL: {e.url}\n{e.text}" for e in items)


def _key(*parts) -> str:
    return " ".join(" ".join(str(p).lower().split()) for p in parts)


def build(recipe: Recipe, engine: Engine | None = None) -> MCPServer:
    server = MCPServer(
        "ttdr",
        instructions=(
            "Research tools on a Tenstorrent QB2. Every passage has an evidence ID in square "
            "brackets; cite claims with those IDs, e.g. [W1a2b3c4d], [W1a2b3c4d.3] or "
            "[3M_2018_10K#p59.0]."
        ),
    )
    state: dict = {"engine": engine, "seen": set()}

    def eng() -> Engine:
        if state["engine"] is None:
            state["engine"] = Engine.open(recipe)
        return state["engine"]

    @server.tool()
    async def search(
        query: str, sources: str = "all", max_results: int = 8, corpus: str | None = None
    ) -> str:
        """Search the web and private document collections. Use short keyword queries.
        sources: "web", "corpus" (private documents such as 10-K filings) or "all";
        corpus narrows to one collection (e.g. "financebench").
        Returns passages with evidence IDs, titles, URLs and snippets."""
        key = _key(query, sources, max_results)
        repeat = key in state["seen"]
        state["seen"].add(key)
        items = await eng().search(query, sources, max_results, corpus)
        note = REPEAT_NOTE if repeat else ""
        if sources in ("web", "all") and not recipe.services.searxng:
            note += "Web search is off in this deployment; results are from private documents.\n\n"
        return note + _format(items)

    @server.tool()
    async def fetch_page(url: str, focus: str | None = None, max_passages: int = 5) -> str:
        """Read a web page or PDF. With focus (the question you are answering), returns the
        most relevant passages, each with an evidence ID; without it, the first passages."""
        try:
            items = await eng().fetch(url, focus, max_passages)
        except Exception as e:  # noqa: BLE001 - reported to the model, which can try another URL
            return f"Could not fetch {url}: {type(e).__name__}"
        return _format(items)

    @server.tool()
    async def get_evidence(ids: list[str]) -> str:
        """Full text of passages by evidence ID, e.g. to quote or check a claim."""
        return _format(await eng().get(ids))

    if recipe.decide:  # tools follow the recipe: no decide role, no verify tool

        @server.tool()
        async def verify(claim: str, evidence_ids: list[str]) -> str:
            """Check one claim against the passages it cites (by evidence ID).
            Returns supported, contradicted or not_enough_info with the model's confidence."""
            from ttdr.engine.verify import from_recipe

            found = await eng().get(evidence_ids)
            if not found:
                return "not_enough_info: none of those evidence IDs are known"
            v = await from_recipe(recipe, eng().clients.decide).check(
                claim, "\n\n".join(e.text for e in found)
            )
            return f"{v.label} (supported {v.supported:.2f}, contradicted {v.contradicted:.2f})"

    return server
