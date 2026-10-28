"""The research engine behind MCP: search (web + corpus), fetch, and an evidence store."""

import asyncio
import json
from dataclasses import asdict

import httpx
from redis.asyncio import Redis

from ttdr.clients import Clients, connect
from ttdr.engine import fetch, web
from ttdr.engine.evidence import Evidence, EvidenceStore
from ttdr.recipe import Recipe

SOURCES = ("web", "corpus", "all")


class Engine:
    def __init__(self, recipe: Recipe, http: httpx.AsyncClient, redis: Redis):
        self.recipe = recipe
        self.http = http
        self.clients: Clients = connect(recipe, http)
        self.evidence = EvidenceStore(redis)

    @classmethod
    def open(cls, recipe: Recipe) -> "Engine":
        return cls(
            recipe,
            httpx.AsyncClient(timeout=60),
            Redis.from_url(recipe.services.redis, decode_responses=True),
        )

    async def _web(self, query: str, k: int) -> list[Evidence]:
        key = f"web:{k}:{' '.join(query.lower().split())}"
        if hit := await self.evidence.cached(key):
            return [Evidence(**{**d, "flags": tuple(d["flags"])}) for d in json.loads(hit)]
        items = await web.search(
            self.http, self.recipe.services.searxng, self.clients.embed, query, k
        )
        if items:
            await self.evidence.cache(key, json.dumps([asdict(e) for e in items]))
        return items

    async def _corpus(self, query: str, k: int, corpus: str | None) -> list[Evidence]:
        hits = await self.clients.corpus.search(query, k, corpus)
        return [
            Evidence(
                h.id,
                "corpus",
                h.doc,
                f"corpus://{h.doc}#page={h.page + 1}",
                h.text,
                round(h.score, 3),
                tuple(h.flags),
            )
            for h in hits
        ]

    async def search(
        self, query: str, sources: str = "all", k: int = 8, corpus: str | None = None
    ) -> list[Evidence]:
        tasks = []
        if sources in ("web", "all"):
            tasks.append(self._web(query, k))
        if sources in ("corpus", "all"):
            tasks.append(self._corpus(query, k, corpus))
        items = [e for batch in await asyncio.gather(*tasks) for e in batch]
        await self.evidence.put(items)
        return items

    async def fetch(self, url: str, query: str | None = None, k: int = 5) -> list[Evidence]:
        key = f"page:{url}"
        if hit := await self.evidence.cached(key):
            title, text = json.loads(hit)
        else:
            title, text = await fetch.extract(self.http, url)
            await self.evidence.cache(key, json.dumps([title, text]))
        items = await fetch.chunks(self.clients.embed, url, title, text, query, k)
        await self.evidence.put(items)
        return items

    async def get(self, ids: list[str]) -> list[Evidence]:
        found = await asyncio.gather(*(self.evidence.get(i) for i in ids))
        return [e for e in found if e]
