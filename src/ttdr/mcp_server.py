"""The engine's MCP server: research tools any MCP client (AI-Q, Hermes) can call."""

import httpx
from mcp.server.mcpserver import MCPServer

from ttdr.recipe import Recipe


def _format(results: list[dict]) -> str:
    if not results:
        return "No results."
    return "\n\n".join(
        f"[{n}] {r.get('title', '').strip()}\nURL: {r['url']}\n{r.get('content', '').strip()}"
        for n, r in enumerate(results, 1)
    )


def _key(query: str) -> str:
    return " ".join(query.lower().split())


def build(recipe: Recipe) -> MCPServer:
    server = MCPServer(
        "ttdr",
        instructions="Research tools on a Tenstorrent QB2. Cite sources by their URL.",
    )
    searxng = recipe.services.searxng
    seen: dict[str, str] = {}

    @server.tool()
    async def web_search(query: str, max_results: int = 8) -> str:
        """Search the web. Returns numbered results with title, URL and snippet."""
        key = f"{max_results}:{_key(query)}"
        if key in seen:
            return (
                "You already ran this exact search; the results below are unchanged. "
                "Search for something different, or stop searching and give your answer.\n\n"
                + seen[key]
            )
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.get(f"{searxng}/search", params={"q": query, "format": "json"})
            r.raise_for_status()
        seen[key] = _format(r.json()["results"][:max_results])
        return seen[key]

    return server
