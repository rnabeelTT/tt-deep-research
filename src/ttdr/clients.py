from dataclasses import dataclass, field

import httpx

from ttdr.recipe import Recipe


@dataclass
class OpenAICompat:
    http: httpx.AsyncClient
    base_url: str
    model: str

    async def _post(self, path: str, body: dict) -> dict:
        r = await self.http.post(f"{self.base_url}{path}", json=body)
        r.raise_for_status()
        return r.json()


def reasoning_params(effort: str | None = None, thinking: bool | None = None) -> dict:
    """gpt-oss takes reasoning_effort; Qwen-style chat templates (Ornith) take enable_thinking."""
    params: dict = {}
    if effort is not None:
        params["reasoning_effort"] = effort
    if thinking is not None:
        params["chat_template_kwargs"] = {"enable_thinking": thinking}
    return params


@dataclass
class LLM(OpenAICompat):
    sampling: dict = field(default_factory=dict)
    """Recipe defaults; request params override them."""

    async def chat(self, messages: list[dict], **params) -> dict:
        body = {"model": self.model, "messages": messages, **self.sampling, **params}
        return await self._post("/chat/completions", body)


class Embedder(OpenAICompat):
    async def embed(self, texts: list[str], dimensions: int | None = None) -> list[list[float]]:
        body = {"model": self.model, "input": texts}
        if dimensions:
            body["dimensions"] = dimensions
        return [d["embedding"] for d in (await self._post("/embeddings", body))["data"]]


@dataclass
class Decider:
    """Laya's systemone API: one state, named questions (noul, choice or score)."""

    http: httpx.AsyncClient
    base_url: str

    async def ask(self, state: str, questions: dict[str, dict]) -> dict:
        r = await self.http.post(
            f"{self.base_url}/v1/systemone", json={"state": state, "questions": questions}
        )
        r.raise_for_status()
        return r.json()["answers"]


@dataclass
class Clients:
    llm: LLM
    embed: Embedder
    decide: Decider


def connect(recipe: Recipe, http: httpx.AsyncClient) -> Clients:
    m = recipe.models
    return Clients(
        LLM(http, m.llm.url, m.llm.model, m.llm.sampling),
        Embedder(http, m.embed.url, m.embed.model),
        Decider(http, m.decide.url),
    )
