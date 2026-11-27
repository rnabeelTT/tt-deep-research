import asyncio
import json
from pathlib import Path

import httpx
import pytest

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


def _serve_fakes(monkeypatch, tmp_path, returncode=0):
    """Fake tt serve processes and endpoints that answer on the second health check."""
    from ttdr import stack

    monkeypatch.chdir(tmp_path)
    events: list[str] = []

    class Proc:
        def __init__(self, cmd, stdout, **_):
            self.bundle, self.returncode = cmd[2], returncode
            stdout.write("pulling…\ncontainer exited\n")
            events.append(f"start {self.bundle}")

        def poll(self):
            events.append(f"poll {self.bundle}")
            return self.returncode

    names = ["ornith", "qwen3vl_embed", "reranker"]
    rounds = iter([set(), set()])

    async def health_run(_recipe):
        answering = next(rounds, set(names))
        return [type("C", (), {"name": n, "ok": n in answering})() for n in names]

    monkeypatch.setattr(stack, "preflight", lambda *a: None)
    monkeypatch.setattr(stack.health, "run", health_run)
    monkeypatch.setattr(stack.subprocess, "Popen", Proc)
    monkeypatch.setattr(stack.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(stack.time, "sleep", lambda s: None)
    times = iter(range(0, 10_000, 11))  # each tick is past the 10 s between health checks
    monkeypatch.setattr(stack.time, "time", lambda: next(times))
    return stack, events, names


def test_up_starts_pinned_models_together_and_waits_for_all(monkeypatch, tmp_path):
    """Every model has its chips, so all three serves run at once (none waits on another),
    each logging to its own file; then ttdr up waits until each answers."""
    stack, events, names = _serve_fakes(monkeypatch, tmp_path)
    stack.serve_models(RECIPE, lambda m: None)
    assert [e.split()[0] for e in events[:3]] == ["start"] * 3  # all started before any polled
    assert all((tmp_path / "data/logs" / f"serve-{n}.log").exists() for n in names)


def test_up_stops_at_a_failed_start_and_shows_its_log(monkeypatch, tmp_path, capsys):
    stack, _, _ = _serve_fakes(monkeypatch, tmp_path, returncode=1)
    with pytest.raises(SystemExit) as err:
        stack.serve_models(RECIPE, lambda m: None)
    assert err.value.code == 1
    shown = capsys.readouterr().err
    assert "✗ Ornith-1.5-35B-A3B" in shown and "did not start" in shown
    assert "container exited" in shown  # the end of its log


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


@pytest.mark.parametrize("args, aiq", [([], False), (["--aiq"], True)])
def test_up_runs_ai_q_beside_deep_agents_only_when_asked(monkeypatch, args, aiq):
    from typer.testing import CliRunner

    from ttdr import cli, ports, stack

    started = []
    monkeypatch.setattr(
        stack, "up", lambda r, *_: started.append(r) or ({"engine": True}, ports.APP)
    )
    result = CliRunner().invoke(cli.app, ["up", *args])
    assert result.exit_code == 0, result.output
    services = stack.app_services(started[0])
    assert ("aiq" in services and "ui" in services) is aiq and "deepagents" in services
