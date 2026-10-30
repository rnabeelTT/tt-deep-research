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
    r = recipe.load(ROOT / "recipes" / "minimal.toml")
    assert r.decide is None and r.services.searxng is None
    assert list(r.served()) == ["ornith", "embeddinggemma"]
    assert app_services(r) == ["engine", "aiq"]
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
    gptoss = aiq_config.render(recipe.load(ROOT / "recipes" / "gpt-oss.toml"))["llms"]
    assert gptoss["agent_llm"]["extra_body"] == {"top_k": 20, "reasoning_effort": "high"}
    assert gptoss["planner_llm"]["extra_body"]["reasoning_effort"] == "low"
    assert gptoss["writer_llm"]["temperature"] == 1.0


def test_features_reach_compose_through_env():
    from ttdr.stack import compose_env

    assert compose_env(RECIPE)["AIQ_AUTO_TOOL_CHOICE"] == "1"
    forced = recipe.load(ROOT / "recipes" / "forced-tool-choice.toml")
    assert compose_env(forced)["AIQ_AUTO_TOOL_CHOICE"] == "0"
    assert compose_env(RECIPE)["TTDR_OCR_BACKEND"] == "paddle"


def test_decide_can_be_the_llm():
    r = recipe.load(Path(__file__).parents[1] / "recipes" / "llm-verify.toml")
    assert r.decide.kind == "chat" and r.decide.model == r.llm.model
    assert "laya" not in r.served()


def test_claim_checker_verifies_but_cannot_triage():
    r = recipe.load(ROOT / "recipes" / "minicheck.toml")
    assert r.decide.kind == "check" and r.decide.bundle is None
    assert "laya" not in r.served()
    data = r.model_dump()
    with pytest.raises(ValidationError, match="triage needs a decide or chat model"):
        recipe.Recipe.model_validate({**data, "features": {**data["features"], "triage": True}})


def test_rerank_overlay_adds_a_reranker_without_a_bundle():
    r = recipe.load(ROOT / "recipes" / "rerank.toml")
    assert r.rerank.kind == "rerank" and r.rerank.bundle is None
    reranker = connect(r, httpx.AsyncClient()).rerank
    assert "<Query>: q\n" in reranker.query_template.format(query="q")
    assert reranker.document_template.startswith("<Document>: {document}")
    assert connect(RECIPE, httpx.AsyncClient()).rerank is None
