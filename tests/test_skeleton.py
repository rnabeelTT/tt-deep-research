import asyncio
import json
from pathlib import Path

import httpx

from ttdr import health, recipe
from ttdr.clients import connect

RECIPE = recipe.load(Path(__file__).parents[1] / "recipe.toml")


def _mock(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_recipe_loads():
    assert RECIPE.models.llm.profile == "p300"
    assert RECIPE.models.decide.url.endswith(":8002")


def test_embedder_calls_endpoint_from_recipe():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    async def go():
        async with _mock(handler) as http:
            return await connect(RECIPE, http).embed.embed(["x"])

    assert asyncio.run(go()) == [[0.1, 0.2]]
    req = seen[0]
    assert str(req.url) == "http://localhost:8001/v1/embeddings"
    assert b'"model":"google/embeddinggemma-2"' in req.read()


def test_decider_posts_systemone():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/systemone"
        return httpx.Response(200, json={"answers": {"q": {"noul": 0.9}}})

    async def go():
        async with _mock(handler) as http:
            return await connect(RECIPE, http).decide.ask("s", {"q": {"type": "noul"}})

    assert asyncio.run(go())["q"]["noul"] == 0.9


def test_failed_probe_is_reported_not_raised():
    async def boom() -> str:
        raise ConnectionError("refused")

    check = asyncio.run(health._timed("x", boom()))
    assert not check.ok and "refused" in check.detail


def test_recipe_overlay_swaps_llm_but_keeps_judge():
    r = recipe.load(Path(__file__).parents[1] / "recipes" / "gpt-oss.toml")
    assert r.models.llm.model == "openai/gpt-oss-20b"
    assert r.models.llm.url == RECIPE.models.llm.url
    assert r.models.judge == RECIPE.models.judge


def test_llm_applies_recipe_sampling_and_request_overrides():
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.read()))
        return httpx.Response(200, json={"choices": []})

    async def go():
        async with _mock(handler) as http:
            llm = connect(RECIPE, http).llm
            await llm.chat([])
            await llm.chat([], temperature=0)

    asyncio.run(go())
    assert bodies[0]["top_k"] == 20 and bodies[0]["temperature"] == 0.6
    assert bodies[1]["temperature"] == 0 and bodies[1]["top_k"] == 20
    gptoss = recipe.load(Path(__file__).parents[1] / "recipes" / "gpt-oss.toml").models.llm
    assert gptoss.sampling == {"temperature": 1.0, "top_k": 20, "top_p": 1.0}
