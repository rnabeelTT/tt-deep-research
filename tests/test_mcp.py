import asyncio
from functools import partial
from pathlib import Path

import httpx

from ttdr import mcp_server, recipe

RECIPE = recipe.load(Path(__file__).parents[1] / "recipe.toml")


def test_repeated_search_is_served_from_cache_with_a_nudge(monkeypatch):
    hits = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(req)
        return httpx.Response(200, json={"results": [{"title": "T", "url": "https://a", "content": "c"}]})

    monkeypatch.setattr(
        mcp_server.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(handler))
    )
    server = mcp_server.build(RECIPE)

    async def search(q):
        return str(await server.call_tool("web_search", {"query": q}))

    first = asyncio.run(search("Blackhole  memory"))
    again = asyncio.run(search("blackhole memory"))
    assert len(hits) == 1
    assert "https://a" in first and "already ran" not in first
    assert "already ran this exact search" in again and "https://a" in again
