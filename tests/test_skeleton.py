import asyncio
import json
from pathlib import Path

import httpx

from ttdr import health, recipe
from ttdr.clients import client, connect

RECIPE = recipe.load(Path(__file__).parents[1] / "recipe.toml")


def _mock(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_default_recipe_serves_the_three_models_on_separate_chips():
    assert RECIPE.llm.profile == "p300"
    assert list(RECIPE.served()) == ["ornith", "qwen3vl_embed", "reranker"]
    assert RECIPE.decide == RECIPE.llm and RECIPE.embed.vision


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
    assert str(req.url) == "http://localhost:8005/v1/embeddings"
    assert b'"model":"Qwen/Qwen3-VL-Embedding-8B"' in req.read()


def test_decider_posts_systemone():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/systemone"
        return httpx.Response(200, json={"answers": {"q": {"noul": 0.9}}})

    async def go():
        async with _mock(handler) as http:
            return await client(http, RECIPE.models["laya"]).ask("s", {"q": {"type": "noul"}})

    assert asyncio.run(go())["q"]["noul"] == 0.9


def test_failed_probe_is_reported_not_raised():
    async def boom() -> str:
        raise ConnectionError("refused")

    check = asyncio.run(health._timed("x", boom()))
    assert not check.ok and "refused" in check.detail


def test_recipe_overlay_swaps_llm_and_judge_follows(gpt_oss):
    r = gpt_oss
    assert r.llm.model == "openai/gpt-oss-20b"
    assert r.llm.url == RECIPE.llm.url
    assert r.judge == r.llm
    assert list(r.served()) == ["gpt-oss", "qwen3vl_embed", "reranker"]


def test_llm_applies_recipe_sampling_and_request_overrides(gpt_oss):
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
    assert gpt_oss.llm.sampling == {"temperature": 1.0, "top_k": 20, "top_p": 1.0}


def test_up_serves_models_from_the_recipe():
    from ttdr.stack import serve_command

    assert serve_command(RECIPE.llm) == [
        "tt",
        "serve",
        "raahemnabeel/ornith-1.5-35b-a3b",
        "--yes",
        "--port",
        "8000",
        "--detach",
        "--",
        "--device-id",
        "0,1",
        "--profile",
        "p300",
        "--max-num-batched-tokens",
        "262144",
    ]
    assert serve_command(RECIPE.embed)[-2:] == ["--device-id", "2"]
    templated = RECIPE.llm.model_copy(update={"chat_template": "configs/mine.jinja"})
    assert serve_command(templated)[-4:-2] == ["--chat-template", "/hf/ttdr/mine.jinja"]
    unpinned = RECIPE.embed.model_copy(update={"chips": None})
    assert serve_command(unpinned)[-3:] == ["--port", "8005", "--detach"]


def test_recipe_chips_become_tt_model_device_ids():
    from ttdr.stack import device_ids

    assert [device_ids(c) for c in ("0-1", "2", "0-3", "0,2-3")] == ["0,1", "2", "0,1,2,3", "0,2,3"]


def test_up_starts_pinned_models_together_and_waits_for_all(monkeypatch, tmp_path):
    """Every model has its chips, so all three serves run at once (none waits on another),
    each logging to its own file; then ttdr up waits until each answers."""
    from ttdr import stack

    monkeypatch.chdir(tmp_path)
    events: list[str] = []

    class Proc:
        def __init__(self, cmd, **_):
            self.bundle = cmd[2]
            events.append(f"start {self.bundle}")

        def wait(self):
            events.append(f"wait {self.bundle}")
            return 0

    rounds = iter([set(), {"ornith"}, {"ornith", "qwen3vl_embed", "reranker"}])
    up = {"ornith": False, "qwen3vl_embed": False, "reranker": False}

    async def health_run(_recipe):
        answering = next(rounds, set(up))
        return [type("C", (), {"name": n, "ok": n in answering})() for n in up]

    monkeypatch.setattr(stack, "preflight", lambda *a: None)
    monkeypatch.setattr(stack.health, "run", health_run)
    monkeypatch.setattr(stack.subprocess, "Popen", Proc)
    monkeypatch.setattr(stack.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(stack.time, "sleep", lambda s: None)
    logged: list[str] = []
    stack.serve_models(RECIPE, logged.append)
    starts = [e for e in events if e.startswith("start")]
    assert len(starts) == 3 and events[:3] == starts  # all started before any is waited on
    assert all((tmp_path / "data/logs" / f"serve-{n}.log").exists() for n in up)
    assert [line for line in logged if line.endswith(": up")] == [
        "ornith: up", "qwen3vl_embed: up", "reranker: up"
    ]


def test_instructed_embedder_sends_the_instruction_with_queries_only():
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.read()))
        return httpx.Response(200, json={"data": [{"embedding": [1.0]}]})

    async def go():
        async with _mock(handler) as http:
            c = connect(RECIPE, http)
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
            e = client(http, RECIPE.models["embeddinggemma"])
            await e.queries(["what?"], task="search result")
            await e.documents([("ACME", "revenue")])

    asyncio.run(go())
    assert bodies[0]["input"] == ["task: search result | query: what?"]
    assert bodies[1]["input"] == ["title: ACME | text: revenue"]
