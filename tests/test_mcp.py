import asyncio
from pathlib import Path

from ttdr import mcp_server, recipe
from ttdr.engine import web
from ttdr.engine.evidence import Evidence, web_id

RECIPE = recipe.load(Path(__file__).parents[1] / "recipe.toml")
EV = Evidence(web_id("https://a"), "web", "T", "https://a", "snippet")


class FakeEngine:
    def __init__(self):
        self.calls = []

    async def search(self, query, sources, k, corpus=None):
        self.calls.append(query)
        return [EV]

    async def fetch(self, url, focus, k):
        raise TimeoutError

    async def get(self, ids):
        return [EV] if EV.id in ids else []


def _call(server, tool, args):
    return str(asyncio.run(server.call_tool(tool, args)))


def test_search_returns_evidence_ids_and_nudges_on_repeat():
    server = mcp_server.build(RECIPE, FakeEngine())
    first = _call(server, "search", {"query": "Blackhole  memory"})
    again = _call(server, "search", {"query": "blackhole memory"})
    assert f"[{EV.id}]" in first and "already ran" not in first
    assert "already ran this exact search" in again


def test_fetch_errors_are_reported_not_raised():
    server = mcp_server.build(RECIPE, FakeEngine())
    assert "Could not fetch" in _call(server, "fetch_page", {"url": "https://x"})


def test_get_evidence_by_id():
    server = mcp_server.build(RECIPE, FakeEngine())
    assert "snippet" in _call(server, "get_evidence", {"ids": [EV.id]})


class FakeEmbedder:
    """Query and documents embedded as fixed 2-D vectors."""

    def __init__(self, vecs):
        self.vecs = vecs

    async def embed(self, texts):
        return self.vecs[: len(texts)]


def test_rerank_drops_weak_and_duplicate_results():
    results = [{"url": u, "title": u, "content": u} for u in ("good", "dupe", "other", "junk")]
    vecs = [[1, 0], [0.95, 0.31], [0.95, 0.31], [0.85, -0.53], [0.3, 0.95]]
    ranked = asyncio.run(web.rerank(FakeEmbedder(vecs), "q", results))
    assert [r["url"] for r, _ in ranked] == ["good", "other"]
