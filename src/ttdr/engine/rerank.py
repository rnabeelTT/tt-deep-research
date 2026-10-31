"""Rerank: embedding search fetches a wider pool, a cross-encoder reorders it, the top k stay.

Qwen3-Reranker-0.6B on the CPU lifted FinanceBench recall@10 from 76% to 84% over a pool of 30;
reranker order alone matched a blend with the embedding score at @10 and beat it at @5 (docs/LOG.md).
A multimodal reranker can also see each candidate's page: "text" sends the chunk, "page" the
rendered page, "both" the two together.
"""

from collections.abc import Awaitable, Callable
from typing import Literal

from ttdr.clients import Reranker

POOL = 30
Mode = Literal["text", "page", "both"]


class Rerank:
    def __init__(
        self,
        reranker: Reranker,
        pool: int = POOL,
        mode: Mode = "text",
        pages: Callable[[list], Awaitable[list[str | None]]] | None = None,
    ):
        self.reranker = reranker
        self.pool = pool
        self.mode = mode if pages else "text"
        self.pages = pages

    async def _docs(self, items: list) -> list[str | dict]:
        if self.mode == "text":
            return [i.text for i in items]
        pngs = await self.pages(items)
        return [
            i.text
            if png is None
            else {"image": png}
            if self.mode == "page"
            else {"text": i.text, "image": png}
            for i, png in zip(items, pngs, strict=True)
        ]

    async def rerank(self, query: str, items: list) -> list:
        """Items (with .text) reordered by relevance. Text-only, an unOCR'd scan is just its
        placeholder note; with pages the reranker reads the scan itself."""
        if len(items) < 2:
            return items
        scores = await self.reranker.scores(query, await self._docs(items))
        order = sorted(range(len(items)), key=lambda i: (-scores[i], i))
        return [items[i] for i in order]
