"""Rerank: embedding search fetches a wider pool, a cross-encoder reorders it, the top k stay.

Qwen3-Reranker-0.6B on the CPU lifted FinanceBench recall@10 from 76% to 84% over a pool of 30;
reranker order alone matched a blend with the embedding score at @10 and beat it at @5 (docs/LOG.md).
"""

from ttdr.clients import Reranker

POOL = 30


class Rerank:
    def __init__(self, reranker: Reranker, pool: int = POOL):
        self.reranker = reranker
        self.pool = pool

    async def rerank(self, query: str, items: list) -> list:
        """Items (with .text) reordered by relevance. A text reranker sees an unOCR'd scan as its
        placeholder note, so eager OCR at ingestion matters once this is on."""
        if len(items) < 2:
            return items
        scores = await self.reranker.scores(query, [i.text for i in items])
        order = sorted(range(len(items)), key=lambda i: (-scores[i], i))
        return [items[i] for i in order]
