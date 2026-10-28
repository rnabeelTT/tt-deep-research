"""Lazy OCR: image-only pages are found by their page image, OCR'd on first retrieval.

The engine enqueues a page the first time search returns it and serves the OCR text once a
worker (workers/ocr) has cached it. Backfill enqueues every flagged page, most-retrieved first.
"""

import json

from redis.asyncio import Redis

QUEUE = "ocr:queue"
PENDING = "ocr:pending"
HITS = "ocr:hits"
TEXT = "ocr:text:{page}"
NEEDS_OCR = {"image_only", "bad_text"}
PENDING_NOTE = "[Scanned page: OCR pending. Ranked by its page image; text not yet verified.]"


def page_key(doc: str, page: int) -> str:
    return f"{doc}#p{page}"


class OcrQueue:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def text(self, doc: str, page: int) -> str | None:
        return await self.redis.get(TEXT.format(page=page_key(doc, page)))

    async def request(self, doc: str, page: int, pdf: str) -> None:
        """Count a retrieval and queue the page once."""
        key = page_key(doc, page)
        await self.redis.zincrby(HITS, 1, key)
        if await self.redis.sadd(PENDING, key):
            await self.redis.lpush(QUEUE, json.dumps({"doc": doc, "page": page, "pdf": pdf}))

    async def backfill(self, pages: list[tuple[str, int, str]]) -> int:
        """Queue every flagged page not yet done, most-retrieved first."""
        scores = {k: s for k, s in await self.redis.zrange(HITS, 0, -1, withscores=True)}
        todo = sorted(pages, key=lambda p: -scores.get(page_key(p[0], p[1]), 0))
        n = 0
        for doc, page, pdf in todo:
            if await self.text(doc, page) is None and await self.redis.sadd(
                PENDING, page_key(doc, page)
            ):
                await self.redis.rpush(QUEUE, json.dumps({"doc": doc, "page": page, "pdf": pdf}))
                n += 1
        return n
