from dataclasses import dataclass

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


class LLM(OpenAICompat):
    async def chat(self, messages: list[dict], **params) -> dict:
        return await self._post(
            "/chat/completions", {"model": self.model, "messages": messages, **params}
        )


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
        LLM(http, m.llm.url, m.llm.model),
        Embedder(http, m.embed.url, m.embed.model),
        Decider(http, m.decide.url),
    )
