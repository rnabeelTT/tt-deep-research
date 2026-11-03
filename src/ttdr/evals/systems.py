import asyncio
import re
import time
from dataclasses import dataclass
from typing import Protocol

import httpx
from mcp import Client

from ttdr import recipe
from ttdr.clients import Clients, reasoning_params
from ttdr.corpus.store import Hit
from ttdr.engine.rerank import Rerank
from ttdr.evals.schema import Answer, Item


class System(Protocol):
    async def answer(self, item: Item, clients: Clients) -> Answer: ...


@dataclass
class B0:
    """Closed book: the LLM alone, no retrieval. The floor every later stage must beat."""

    reasoning_effort: str | None = "medium"
    thinking: bool | None = None
    max_tokens: int = 4096
    prompt: str = (
        "Answer the question concisely and directly. "
        'If you are not confident in the answer, say "I don\'t know".'
    )

    async def answer(self, item: Item, clients: Clients) -> Answer:
        return await self._ask(clients, item, item.question, time.perf_counter())

    async def _ask(
        self, clients: Clients, item: Item, user: str | list[dict], start: float, **extra
    ) -> Answer:
        r = await clients.llm.chat(
            [{"role": "system", "content": self.prompt}, {"role": "user", "content": user}],
            max_tokens=self.max_tokens,
            **reasoning_params(self.reasoning_effort, self.thinking),
        )
        usage, choice = r["usage"], r["choices"][0]
        return Answer(
            id=item.id,
            text=choice["message"]["content"] or "",
            finish_reason=choice.get("finish_reason"),
            latency_s=time.perf_counter() - start,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            reasoning_tokens=(usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens", 0
            ),
            **extra,
        )


CITE = re.compile(r"\[([^\[\]\s]+#p\d+\.\d+)\]")


@dataclass
class B1(B0):
    """Naive RAG: top-k chunks from the suite's corpus, answer from them only, cite evidence IDs."""

    k: int = 10
    corpus: str = "financebench"
    known_doc: bool = False
    rerank: bool = False
    """Rerank a wider pool with the recipe's rerank model before keeping the top k."""
    rerank_mode: str = "text"
    """What the reranker sees: the chunk text, its rendered page, or both."""
    pages: int = 0
    """Also show the LLM the rendered pages of the top hits (up to this many distinct pages)."""
    page_scale: float = 1.5
    prompt: str = (
        "Answer the question using only the passages provided. Cite the passages you used by "
        "their ID in square brackets, e.g. [DOC#p12.0]. Be concise and give exact figures. "
        'If the passages do not contain the answer, say "I don\'t know".'
    )

    async def answer(self, item: Item, clients: Clients) -> Answer:
        start = time.perf_counter()
        doc = item.tags.get("doc") if self.known_doc else None
        corpus = clients.corpus
        if self.rerank and clients.rerank:
            ranker = Rerank(clients.rerank, mode=self.rerank_mode, pages=corpus.page_images)
            hits = await corpus.search(item.question, ranker.pool, self.corpus, doc)
            hits = (await ranker.rerank(item.question, hits))[: self.k]
        else:
            hits = await corpus.search(item.question, self.k, self.corpus, doc)
        passages = "\n\n".join(f"[{h.id}]\n{h.text}" for h in hits)
        user: str | list[dict] = f"Passages:\n\n{passages}\n\nQuestion: {item.question}"
        if self.pages:
            first: dict[tuple[str, int], Hit] = {}
            for h in hits:
                first.setdefault((h.doc, h.page), h)
            top = list(first.values())[: self.pages]
            pngs = await corpus.page_images(top, self.page_scale)
            user = [
                part
                for h, png in zip(top, pngs, strict=True)
                if png
                for part in (
                    {"type": "text", "text": f"Page image of [{h.id}]:"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}},
                )
            ] + [{"type": "text", "text": user}]
        a = await self._ask(clients, item, user, start, retrieved=[h.id for h in hits])
        a.cited = sorted(set(CITE.findall(a.text)))
        return a


@dataclass
class B1MCP(B1):
    """B1 with retrieval through the engine's MCP `search` tool, to check the tool path."""

    mcp_url: str = "http://localhost:9901/mcp"

    async def answer(self, item: Item, clients: Clients) -> Answer:
        start = time.perf_counter()
        async with Client(self.mcp_url) as mcp:
            r = await mcp.call_tool(
                "search",
                {
                    "query": item.question,
                    "sources": "corpus",
                    "max_results": self.k,
                    "corpus": self.corpus,
                },
            )
        passages = "".join(c.text for c in r.content if c.type == "text")
        a = await self._ask(
            clients,
            item,
            f"Passages:\n\n{passages}\n\nQuestion: {item.question}",
            start,
            retrieved=re.findall(r"^\[([^\]]+)\]", passages, re.MULTILINE),
        )
        a.cited = sorted(set(CITE.findall(a.text)))
        return a


CORPUS_URL = re.compile(r"corpus://([^#\s)\]]+)#page=(\d+)")


def cited_ids(text: str) -> list[str]:
    """Evidence IDs a report cites, inline or as corpus:// URLs (page numbers are 1-based there)."""
    ids = set(CITE.findall(text))
    ids |= {f"{doc}#p{int(page) - 1}.0" for doc, page in CORPUS_URL.findall(text)}
    return sorted(ids)


async def _get(http, url: str, tries: int = 5) -> dict:
    """GET with retries: long polls through a shared client can hit closed keep-alive sockets."""
    for attempt in range(tries):
        try:
            r = await http.get(url)
            r.raise_for_status()
            return r.json()
        except httpx.TransportError:
            if attempt == tries - 1:
                raise
            await asyncio.sleep(1 + attempt)
    raise AssertionError


@dataclass
class AIQ:
    """A question submitted as an AI-Q job (shallow_researcher or deep_researcher) over its API."""

    agent: str = "shallow_researcher"
    api: str = "http://127.0.0.1:8100"
    poll_s: float = 5.0
    timeout_s: float = 3600.0

    async def answer(self, item: Item, clients: Clients) -> Answer:
        start = time.perf_counter()
        http = clients.llm.http
        r = await http.post(
            f"{self.api}/v1/jobs/async/submit",
            json={"agent_type": self.agent, "input": item.question, "data_sources": ["ttdr"]},
        )
        r.raise_for_status()
        job = r.json()["job_id"]
        status = "submitted"
        while status in ("submitted", "running"):
            if time.perf_counter() - start > self.timeout_s:
                await http.post(f"{self.api}/v1/jobs/async/job/{job}/cancel")
                raise TimeoutError(f"job {job} still {status}")
            await asyncio.sleep(self.poll_s)
            status = (await _get(http, f"{self.api}/v1/jobs/async/job/{job}"))["status"]
        report = await _get(http, f"{self.api}/v1/jobs/async/job/{job}/report")
        text = report.get("report") or ""
        if status != "success":
            raise RuntimeError(f"job {job} ended {status}")
        return Answer(
            id=item.id, text=text, latency_s=time.perf_counter() - start, cited=cited_ids(text)
        )


@dataclass
class DeepAgents:
    """LangChain Deep Agents (ttdr.harness) over the engine's MCP tools."""

    max_subquestions: int = 3
    max_tool_calls: int = 8
    thinking: bool = False
    doc_tools: bool = True
    context_tokens: int | None = None
    sections: bool = True

    async def answer(self, item: Item, clients: Clients) -> Answer:
        from ttdr.harness import DeepAgentsHarness

        start = time.perf_counter()
        h = DeepAgentsHarness(
            recipe.load(),
            self.max_subquestions,
            self.max_tool_calls,
            thinking=self.thinking,
            doc_tools=self.doc_tools,
            context_tokens=self.context_tokens,
            sections=self.sections,
        )
        r = await h.run(item.question)
        return Answer(
            id=item.id,
            text=r.report,
            latency_s=time.perf_counter() - start,
            prompt_tokens=r.stats["input tokens"],
            completion_tokens=r.stats["output tokens"],
            retrieved=r.seen,
            cited=sorted(set(CITE.findall(r.report))),
            stats=dict(r.stats),
        )


SYSTEMS: dict[str, type] = {
    "b0": B0,
    "b1": B1,
    "b1mcp": B1MCP,
    "aiq": AIQ,
    "deepagents": DeepAgents,
}
