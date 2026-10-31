import asyncio
import uuid
from dataclasses import dataclass, field

import httpx

from ttdr.corpus import Corpus
from ttdr.corpus.store import Store
from ttdr.recipe import Model, Recipe


@dataclass
class OpenAICompat:
    http: httpx.AsyncClient
    base_url: str
    model: str

    async def _post(self, path: str, body: dict) -> dict:
        r = await self.http.post(f"{self.base_url}{path}", json=body)
        r.raise_for_status()
        return r.json()


def image_params() -> dict:
    """For requests carrying images: a unique cache salt keeps them out of the prefix cache. A
    cache hit inside an image prompt leaves a partial prefill, which tt's Ornith can't run (the
    engine dies); text-only requests keep sharing prefixes."""
    return {"cache_salt": uuid.uuid4().hex}


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

    async def embed_images(self, images_b64: list[str]) -> list[list[float]]:
        body = {"model": self.model, "input": [{"image": b} for b in images_b64]}
        return [d["embedding"] for d in (await self._post("/embeddings", body))["data"]]


@dataclass
class Decider:
    """Decision models' System One API (Laya, Kev): one state, named questions."""

    http: httpx.AsyncClient
    base_url: str
    model: str = ""
    batch: bool = True

    async def ask(self, state: str, questions: dict[str, dict]) -> dict:
        r = await self.http.post(
            f"{self.base_url}/v1/systemone", json={"state": state, "questions": questions}
        )
        r.raise_for_status()
        return r.json()["answers"]

    async def ask_batch(self, states: list[str], questions: dict[str, dict]) -> list[dict]:
        """Many states, one set of questions, shared forward passes (Laya); servers without the
        batch route (Kev, CLM) get one request per state."""
        if self.batch:
            r = await self.http.post(
                f"{self.base_url}/v1/systemone/batch",
                json={"states": states, "questions": questions},
            )
            if r.status_code not in (404, 405):
                r.raise_for_status()
                return [res["answers"] for res in r.json()["results"]]
            self.batch = False
        return list(await asyncio.gather(*(self.ask(s, questions) for s in states)))


@dataclass
class Checker:
    """Claim checkers (MiniCheck): the probability that a passage supports a claim."""

    http: httpx.AsyncClient
    base_url: str
    model: str = ""

    async def support(self, pairs: list[tuple[str, str]]) -> list[float]:
        body = {"pairs": [{"passage": p, "claim": c} for p, c in pairs]}
        r = await self.http.post(f"{self.base_url}/check", json=body)
        r.raise_for_status()
        return r.json()["support"]


@dataclass
class Reranker(OpenAICompat):
    """Cross-encoder rerankers behind the Cohere/Jina-style rerank API. Servers that build the
    model's prompt take an `instruction` (tt-dit-server's Qwen3-VL-Reranker); vLLM serving the
    original Qwen3-Reranker needs the prompt wrapped client-side (templates). A document is text,
    or a dict with "text" and/or "image" (base64 PNG) for multimodal rerankers."""

    query_template: str = "{query}"
    document_template: str = "{document}"
    instruction: str | None = None

    def _doc(self, d: str | dict) -> str | dict:
        if isinstance(d, str):
            return self.document_template.format(document=d)
        return (
            {**d, "text": self.document_template.format(document=d["text"])} if "text" in d else d
        )

    async def scores(self, query: str, docs: list[str | dict]) -> list[float]:
        body = {
            "model": self.model,
            "query": self.query_template.format(query=query),
            "documents": [self._doc(d) for d in docs],
        }
        if self.instruction:
            body["instruction"] = self.instruction
        out = [0.0] * len(docs)
        for r in (await self._post("/rerank", body))["results"]:
            out[r["index"]] = r["relevance_score"]
        return out


CHAT_DECIDE = """Read the state and answer the question with exactly one of the options, nothing else.

Question: {instructions}

Options:
{options}"""


@dataclass
class ChatDecider:
    """A chat model behind the decision models' typed questions, so `decide` can point at the LLM.

    Choice questions get the chosen option's probability 1.0; noul (yes/no) questions get 1.0
    or 0.0. No thinking and a few tokens per answer, with no forced output format.
    """

    llm: LLM

    async def _one(self, state: str, q: dict) -> dict:
        if q["type"] == "noul":
            options = {"yes": "Yes", "no": "No"}
        else:
            options = q["criteria"]
        listing = "\n".join(f"- {k}: {v}" for k, v in options.items())
        r = await self.llm.chat(
            [
                {
                    "role": "system",
                    "content": CHAT_DECIDE.format(instructions=q["instructions"], options=listing),
                },
                {"role": "user", "content": state},
            ],
            max_tokens=16,
            temperature=0,
            **reasoning_params(thinking=False),
        )
        said = (r["choices"][0]["message"]["content"] or "").strip().lower()
        pick = next((k for k in sorted(options, key=len, reverse=True) if k in said), None)
        if q["type"] == "noul":
            return {"noul": 1.0 if pick == "yes" else 0.0}
        probs = {k: float(k == pick) for k in options}
        return {"choice": pick, "probabilities": probs}

    async def ask(self, state: str, questions: dict[str, dict]) -> dict:
        names = list(questions)
        answers = await asyncio.gather(*(self._one(state, questions[n]) for n in names))
        return dict(zip(names, answers, strict=True))

    async def ask_batch(self, states: list[str], questions: dict[str, dict]) -> list[dict]:
        return list(await asyncio.gather(*(self.ask(s, questions) for s in states)))


# kind -> client. A new kind of model is one client class plus one entry here (and a role).
KINDS = {
    "chat": lambda http, m: LLM(http, m.url, m.model, m.sampling),
    "embed": lambda http, m: Embedder(http, m.url, m.model),
    "decide": lambda http, m: Decider(http, m.url, m.model),
    "check": lambda http, m: Checker(http, m.url, m.model),
    "rerank": lambda http, m: Reranker(
        http,
        m.url,
        m.model,
        m.query_template or "{query}",
        m.document_template or "{document}",
        m.instruction,
    ),
}


def client(http: httpx.AsyncClient, m: Model):
    return KINDS[m.kind](http, m)


def _decider(http: httpx.AsyncClient, m: Model):
    return ChatDecider(client(http, m)) if m.kind == "chat" else client(http, m)


@dataclass
class Clients:
    llm: LLM
    embed: Embedder
    decide: Decider | ChatDecider | Checker | None
    corpus: Corpus
    rerank: Reranker | None = None


def connect(recipe: Recipe, http: httpx.AsyncClient) -> Clients:
    embed = client(http, recipe.embed)
    decide = recipe.decide
    return Clients(
        client(http, recipe.llm),
        embed,
        _decider(http, decide) if decide else None,
        Corpus(embed, Store(recipe.services.qdrant)),
        client(http, recipe.rerank) if recipe.rerank else None,
    )
