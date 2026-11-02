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
    assert RECIPE.llm.profile == "p300"
    assert RECIPE.decide.url.endswith(":8002")


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


def test_recipe_overlay_swaps_llm_and_judge_follows():
    r = recipe.load(Path(__file__).parents[1] / "recipes" / "gpt-oss.toml")
    assert r.llm.model == "openai/gpt-oss-20b"
    assert r.llm.url == RECIPE.llm.url
    assert r.judge == r.llm
    assert list(r.served()) == ["gpt-oss", "embeddinggemma", "laya"]


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
    gptoss = recipe.load(Path(__file__).parents[1] / "recipes" / "gpt-oss.toml").llm
    assert gptoss.sampling == {"temperature": 1.0, "top_k": 20, "top_p": 1.0}


def test_up_serves_models_from_the_recipe():
    from ttdr.stack import serve_command

    assert serve_command(RECIPE.llm) == [
        "tt",
        "serve",
        "raahemnabeel/ornith-1.5-35b-a3b",
        "--yes",
        "--port",
        "8000",
        "--",
        "--profile",
        "p300",
        "--chat-template",
        "/hf/ttdr/ornith.jinja",
        "--max-num-batched-tokens",
        "262144",
        "--no-enable-prefix-caching",
    ]
    assert serve_command(RECIPE.embed)[-2:] == ["--port", "8001"]


def test_instructed_embedder_sends_the_instruction_with_queries_only():
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.read()))
        return httpx.Response(200, json={"data": [{"embedding": [1.0]}]})

    r = recipe.load(Path(__file__).parents[1] / "recipes/qwen-embed.toml")

    async def go():
        async with _mock(handler) as http:
            c = connect(r, http)
            await c.embed.queries(["what?"])
            await c.embed.documents([("ACME_2020_10K", "revenue")])
            return c.corpus.store.collection

    assert asyncio.run(go()) == "chunks_qwen3vl"
    assert bodies[0]["input"] == ["what?"] and bodies[0]["instruction"].startswith("Given")
    assert bodies[1]["input"] == ["ACME_2020_10K\nrevenue"] and "instruction" not in bodies[1]


def test_prompted_embedder_wraps_queries_and_documents():
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.read()))
        return httpx.Response(200, json={"data": [{"embedding": [1.0]}]})

    async def go():
        async with _mock(handler) as http:
            e = connect(RECIPE, http).embed
            await e.queries(["what?"], task="search result")
            await e.documents([("ACME", "revenue")])

    asyncio.run(go())
    assert bodies[0]["input"] == ["task: search result | query: what?"]
    assert bodies[1]["input"] == ["title: ACME | text: revenue"]
