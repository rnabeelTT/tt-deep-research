"""The research engine behind MCP: search (web + corpus), fetch, and an evidence store."""

import asyncio
import json
import re
from dataclasses import asdict, replace

import httpx
from redis.asyncio import Redis

from ttdr.clients import Clients, connect, reasoning_params
from ttdr.corpus.store import Hit
from ttdr.engine import fetch, web
from ttdr.engine.evidence import Evidence, EvidenceStore
from ttdr.engine.ocr import NEEDS_OCR, OCR_OFF_NOTE, PENDING_NOTE, OcrQueue
from ttdr.engine.rerank import Rerank
from ttdr.engine.triage import OVERFETCH, Triage
from ttdr.engine.verify import window_chars
from ttdr.recipe import Recipe

SOURCES = ("web", "corpus", "all")
PAGE_ID = re.compile(r"^(.+)#p(\d+)\.\d+$")
READ_SCALE = 1.5
FIND_K = 10
OPEN_MAX = 2
KEYWORD_K = 30
"""BM25 hits added to the embeddings' candidates (features.keywords), before reranking."""
READ_PAGE = """This is page {page} of the document {doc}. Question: {question}

Report everything on this page that bears on the question: exact values from tables, charts and
figures with their labels, units and periods, and what diagrams show. Quote numbers as printed.
If the page has nothing relevant, say "Nothing relevant on this page." Do not answer from
outside the page."""


PAGE_LINK = re.compile(r"(?:corpus://([^#/\s]+)#page=|/pages/([^/\s]+)/)(\d+)$")


class Offline(Exception):
    """The recipe is offline (features.offline): the web is out of reach."""


class Engine:
    def __init__(self, recipe: Recipe, http: httpx.AsyncClient, redis: Redis):
        self.recipe = recipe
        self.http = http
        self.clients: Clients = connect(recipe, http)
        self.evidence = EvidenceStore(redis)
        self.ocr = OcrQueue(redis)
        decide = recipe.decide
        self.triage = (
            Triage(self.clients.decide, window_chars(decide.max_state_tokens))
            if recipe.features.triage and decide
            else None
        )
        self.rerank = Rerank(self.clients.rerank) if self.clients.rerank else None
        self.page_rerank = (
            Rerank(self.clients.rerank, mode="both", pages=self._page_images)
            if self.rerank and self.clients.corpus.pages
            else None
        )

    @classmethod
    def open(cls, recipe: Recipe) -> "Engine":
        return cls(
            recipe,
            httpx.AsyncClient(timeout=60),
            Redis.from_url(recipe.services.redis, decode_responses=True),
        )

    async def _web(self, query: str, k: int) -> list[Evidence]:
        services = self.recipe.services
        if not (backend := services.web()):
            return []
        source = services.searxng if backend == "searxng" else backend
        key = f"web:{source}:{k}:{' '.join(query.lower().split())}"
        if hit := await self.evidence.cached(key):
            return [Evidence(**{**d, "flags": tuple(d["flags"])}) for d in json.loads(hit)]
        items = await web.search(self.http, backend, services.searxng, self.clients.embed, query, k)
        if items:
            await self.evidence.cache(key, json.dumps([asdict(e) for e in items]))
        return items

    async def _corpus(self, query: str, k: int, corpus: str | None) -> list[Evidence]:
        c = self.clients.corpus
        if self.page_rerank:
            hits = await c.fused(query, k, corpus)
        else:
            extra = KEYWORD_K if self.recipe.features.keywords else 0
            hits = await c.search(query, k, corpus, keywords=extra)
        return list(await asyncio.gather(*(self._corpus_evidence(h) for h in hits)))

    async def _page_images(self, items: list[Evidence]) -> list[str | None]:
        hits = [
            Hit(e.id, m[1], int(m[2]), e.text, [], 0.0, e.source)
            for e in items
            if (m := PAGE_ID.match(e.id))
        ]
        return await self.clients.corpus.page_images(hits)

    async def _corpus_evidence(self, h) -> Evidence:
        """Scanned pages use cached OCR text; on a miss they're queued and marked unverified."""
        text, flags = h.text, tuple(h.flags)
        if NEEDS_OCR & set(h.flags):
            if ocr := await self.ocr.text(h.doc, h.page):
                text, flags = ocr, (*flags, "ocr")
            elif not self.recipe.features.ocr:
                text, flags = text or OCR_OFF_NOTE, (*flags, "unverified")
            else:
                await self.ocr.request(h.corpus, h.doc, h.page)
                text, flags = text or PENDING_NOTE, (*flags, "unverified")
        url = f"corpus://{h.doc}#page={h.page + 1}"
        return Evidence(h.id, h.corpus, h.doc, url, text, round(h.score, 3), flags)

    async def search(
        self, query: str, sources: str = "all", k: int = 8, corpus: str | None = None
    ) -> list[Evidence]:
        if self.rerank:
            n = max(k, self.rerank.pool)
        elif self.triage:
            n = k * OVERFETCH
        else:
            n = k
        tasks, rankers = [], []
        if sources in ("web", "all"):
            tasks.append(self._web(query, n))
            rankers.append(self.rerank or self.triage)
        if sources in ("corpus", "all"):
            tasks.append(self._corpus(query, n, corpus))
            rankers.append(self.page_rerank or self.rerank or self.triage)
        batches = await asyncio.gather(*tasks)
        batches = [
            (await r.rerank(query, b))[:k] if r else b
            for r, b in zip(rankers, batches, strict=True)
        ]
        items = [e for batch in batches for e in batch]
        await self.evidence.put(items)
        if self.recipe.features.read_pages:
            items = await self._with_readings(query, items)
        return items

    async def read_page(self, evidence_id: str, question: str) -> str | None:
        """The LLM (multimodal) reads the page behind a corpus passage for one question: figures,
        charts and table cells the text layer doesn't carry. None for web evidence."""
        m = PAGE_ID.match(evidence_id)
        found = await self.get([evidence_id]) if m else []
        if not found or found[0].source == "web":
            return None
        e = found[0]
        hit = Hit(evidence_id, m[1], int(m[2]), e.text, list(e.flags), 0.0, e.source)
        [png] = await self.clients.corpus.page_images([hit], READ_SCALE)
        if not png:
            return None
        prompt = READ_PAGE.format(page=hit.page + 1, doc=hit.doc, question=question)
        r = await self.clients.llm.chat(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            max_tokens=1024,
            temperature=0,
            **reasoning_params(thinking=False),
        )
        return (r["choices"][0]["message"]["content"] or "").strip()

    async def _with_readings(self, query: str, items: list[Evidence]) -> list[Evidence]:
        """Attach the LLM's reading of the top corpus pages (features.read_pages) to the first
        passage from each page."""
        first: dict[tuple[str, str], int] = {}
        for i, e in enumerate(items):
            if e.source != "web" and PAGE_ID.match(e.id):
                first.setdefault((e.title, PAGE_ID.match(e.id)[2]), i)
        picked = list(first.values())[: self.recipe.features.read_pages]
        readings = await asyncio.gather(*(self.read_page(items[i].id, query) for i in picked))
        items = list(items)
        for i, reading in zip(picked, readings, strict=True):
            if reading:
                text = f"{items[i].text}\n\n[Page reading: {reading}]"
                items[i] = replace(items[i], text=text, flags=(*items[i].flags, "page_read"))
        return items

    async def find(self, doc: str, terms: str, k: int = FIND_K) -> tuple[list[Evidence], int]:
        """Passages of one corpus document containing every word of terms, in page order, and
        how many matched in all."""
        hits = await self.clients.corpus.find(doc, terms)
        items = list(await asyncio.gather(*(self._corpus_evidence(h) for h in hits[:k])))
        await self.evidence.put(items)
        return items, len(hits)

    async def open_pages(self, evidence_id: str, before: int = 0, after: int = 1) -> list[Evidence]:
        """Every passage of a corpus passage's page and its neighbours, in reading order."""
        m = PAGE_ID.match(evidence_id)
        if not m:
            return []
        page = int(m[2])
        first, last = max(0, page - min(before, OPEN_MAX)), page + min(after, OPEN_MAX)
        hits = await self.clients.corpus.page_range(m[1], first, last)
        items = list(await asyncio.gather(*(self._corpus_evidence(h) for h in hits)))
        await self.evidence.put(items)
        return items

    async def fetch(self, url: str, query: str | None = None, k: int = 5) -> list[Evidence]:
        """A corpus page link (corpus://DOC#page=N, or the page-image link the MCP server shows)
        returns that page's passages; anything else is fetched from the web."""
        m = PAGE_LINK.search(url)
        if m and (items := await self.open_pages(f"{m[1] or m[2]}#p{int(m[3]) - 1}.0", 0, 0)):
            return items
        if self.recipe.features.offline:
            raise Offline("only document links can be opened")
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
