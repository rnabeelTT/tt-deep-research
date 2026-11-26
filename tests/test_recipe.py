from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from ttdr import aiq_config, recipe
from ttdr.clients import connect
from ttdr.stack import app_services

ROOT = Path(__file__).parents[1]
RECIPE = recipe.load(ROOT / "recipe.toml")


def test_minimal_overlay_removes_roles_services_and_features():
    r = recipe.load(ROOT / "recipes" / "minimal-stack.toml")
    assert r.decide is None and r.services.searxng is None
    assert list(r.served()) == ["ornith", "qwen3vl_embed"]
    assert app_services(r) == ["engine"]
    assert app_services(RECIPE) == ["engine", "deepagents", "ui-deepagents", "ocr"]  # one UI
    assert connect(r, httpx.AsyncClient()).decide is None


def test_roles_are_checked_against_model_kinds():
    data = RECIPE.model_dump()
    with pytest.raises(ValidationError, match="needs a decide or chat or check model"):
        recipe.Recipe.model_validate(
            {**data, "roles": {**data["roles"], "decide": "embeddinggemma"}}
        )
    with pytest.raises(ValidationError, match="missing required roles: embed"):
        recipe.Recipe.model_validate({**data, "roles": {"llm": "ornith"}})
    with pytest.raises(ValidationError, match="unknown role"):
        recipe.Recipe.model_validate({**data, "roles": {**data["roles"], "summarize": "laya"}})


def test_legacy_recipes_from_old_runs_still_load():
    legacy = {
        "version": "0.1.0",
        "models": {
            k: {k2: v for k2, v in m.model_dump().items() if k2 != "kind"}
            for k, m in (("llm", RECIPE.llm), ("embed", RECIPE.embed))
        },
        "services": RECIPE.services.model_dump(),
    }
    r = recipe.Recipe.model_validate(legacy)
    assert r.llm.model == RECIPE.llm.model and r.roles == {"llm": "llm", "embed": "embed"}


def test_aiq_config_follows_the_llm_and_its_reasoning_control():
    ornith = aiq_config.render(RECIPE)["llms"]
    assert ornith["planner_llm"]["model_name"] == RECIPE.llm.model
    assert ornith["agent_llm"]["extra_body"] == {
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    gptoss = aiq_config.render(recipe.load(ROOT / "recipes" / "llm-gpt-oss-20b.toml"))["llms"]
    assert gptoss["agent_llm"]["extra_body"] == {"top_k": 20, "reasoning_effort": "high"}
    assert gptoss["planner_llm"]["extra_body"]["reasoning_effort"] == "low"
    assert gptoss["writer_llm"]["temperature"] == 1.0


def test_features_reach_compose_through_env():
    from ttdr.stack import compose_env

    assert compose_env(RECIPE)["AIQ_AUTO_TOOL_CHOICE"] == "1"
    forced = recipe.load(ROOT / "recipes" / "aiq-forced-tool-choice.toml")
    assert compose_env(forced)["AIQ_AUTO_TOOL_CHOICE"] == "0"
    assert compose_env(RECIPE)["TTDR_OCR_BACKEND"] == "paddle"
    assert compose_env(RECIPE)["TTDR_RECIPE"] == "recipe.toml"
    assert (
        compose_env(RECIPE, Path("recipes/rerank-qwen3-vl.toml"))["TTDR_RECIPE"]
        == "recipes/rerank-qwen3-vl.toml"
    )


def test_decide_can_be_the_llm():
    r = recipe.load(Path(__file__).parents[1] / "recipes" / "verify-with-llm.toml")
    assert r.decide.kind == "chat" and r.decide.model == r.llm.model
    assert "laya" not in r.served()


def test_claim_checker_verifies_but_cannot_triage():
    r = recipe.load(ROOT / "recipes" / "verify-minicheck-cpu.toml")
    assert r.decide.kind == "check" and r.decide.bundle is None
    assert "laya" not in r.served()
    data = r.model_dump()
    with pytest.raises(ValidationError, match="triage needs a decide or chat model"):
        recipe.Recipe.model_validate({**data, "features": {**data["features"], "triage": True}})


def test_rerank_overlay_serves_a_reranker_on_chip_3_with_ornith_verifying():
    r = recipe.load(ROOT / "recipes" / "rerank-qwen3-vl.toml")
    assert list(r.served()) == ["ornith", "qwen3vl_embed", "reranker"]
    assert r.roles["decide"] == "ornith"
    reranker = connect(r, httpx.AsyncClient()).rerank
    assert reranker.instruction and reranker.query_template == "{query}"
    assert (
        connect(recipe.load(ROOT / "recipes" / "minimal-stack.toml"), httpx.AsyncClient()).rerank
        is None
    )


def test_corpus_catalog_reaches_mcp_harness_and_aiq():
    import asyncio

    from ttdr import mcp_server

    assert "- unidoc: Energy and construction" in RECIPE.corpus_catalog()
    tools = asyncio.run(mcp_server.build(RECIPE, object()).list_tools())
    search = next(t for t in tools if t.name == "search")
    assert "financebench: Annual and quarterly reports" in search.description
    cfg = aiq_config.render(RECIPE)
    docs = next(s for s in cfg["functions"]["data_sources"]["sources"] if s["id"] == "documents")
    assert "unidoc:" in docs["description"]
    assert cfg["function_groups"]["web"]["server"]["url"].endswith("/mcp/web")


def test_keyed_web_search_needs_its_key(monkeypatch):
    from ttdr.recipe import Services

    s = Services(qdrant="q", redis="r", mcp="m", searxng="http://sx", web_search="brave")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    assert s.web() == "searxng"
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    assert s.web() == "brave"
    assert Services(qdrant="q", redis="r", mcp="m").web() is None


def test_env_update_keeps_keys_added_by_hand(tmp_path):
    from ttdr.stack import write_env

    env = tmp_path / ".env"
    env.write_text("TAVILY_API_KEY=k\nTTDR_RECIPE=old.toml\n")
    write_env({"TTDR_RECIPE": "recipe.toml"}, env)
    assert env.read_text() == "TAVILY_API_KEY=k\nTTDR_RECIPE=recipe.toml\n"


def test_default_recipe_serves_three_bundles_with_tt_and_cohost_needs_tt_model(monkeypatch):
    from ttdr import stack

    default = recipe.load(Path("recipe.toml"))
    assert not any(m.cohosts for m in default.served().values())  # chips 0-1, 2, 3
    assert all(stack.serve_command(m)[0] == "tt" for m in default.served().values())
    have = {"docker", "git", "tt"}
    monkeypatch.setattr(stack.shutil, "which", lambda t: t if t in have else None)
    monkeypatch.setattr(stack.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0}))
    assert stack.missing_tools(default) == []
    cohost = recipe.load(Path("recipes/cohost-ornith-35b.toml"))
    assert stack.missing_tools(cohost) == ["tt-model"]
    assert stack.missing_tools(cohost, {"ornith": True}) == []  # already serving: nothing to run


def test_up_says_when_docker_or_the_model_cache_is_out_of_reach(monkeypatch, tmp_path):
    from ttdr import stack

    monkeypatch.setattr(stack.shutil, "which", lambda t: t)
    monkeypatch.setattr(stack.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 1}))
    cache = tmp_path / "models"
    cache.mkdir(mode=0o555)
    monkeypatch.setattr(stack, "MODEL_CACHE", cache)
    problems = stack.access_problems(RECIPE)
    assert len(problems) == 2 and "docker" in problems[0] and str(cache) in problems[1]
    assert len(stack.access_problems(RECIPE, {"ornith": True})) == 1  # Ornith up: nothing to stage


def test_recipes_resolve_by_name_or_path(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("TTDR_RECIPE", raising=False)
    assert recipe.resolve(None) == Path("recipe.toml")
    assert recipe.resolve("default") == Path("recipe.toml")
    assert recipe.resolve(recipe.DEFAULT_NAME) == Path("recipe.toml")
    assert recipe.resolve("cohost-ornith-122b") == Path("recipes/cohost-ornith-122b.toml")
    assert recipe.resolve("recipes/rerank-qwen3-vl.toml") == Path("recipes/rerank-qwen3-vl.toml")
    assert recipe.load("cohost-ornith-122b").llm.model == "ornith-ai/Ornith-1.5-122B"


def test_catalog_says_what_each_recipe_serves_and_where(monkeypatch):
    monkeypatch.chdir(ROOT)
    recipes = {r["name"]: r for r in recipe.catalog()}
    default, cohost = recipes[recipe.DEFAULT_NAME], recipes["cohost-ornith-35b"]
    assert len(default["bundles"]) == 3
    assert [m["chips"] for m in default["models"]] == ["0-1", "2", "3"]
    assert cohost["bundles"] == ["raahemnabeel/cohost-ornith-35b-qwen3vl"]
    assert {m["cohosted_with"] for m in cohost["models"]} == {None, "ornith"}
    # Overlays don't inherit a description: only described recipes are listed by default.
    assert recipes["rerank-qwen3-vl"]["description"] is None and default["description"]
    for name in ("cohost-ornith-122b", "cohost-qwen38-27b"):
        assert len(recipes[name]["bundles"]) == 1
