"""Chunks and their vectors in Qdrant: one collection per embedder, filtered by corpus."""

import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from ttdr.corpus.chunk import Chunk

COLLECTION = "chunks"


@dataclass
class Hit:
    id: str
    doc: str
    page: int
    text: str
    flags: list[str]
    score: float
    corpus: str = ""


def _hit(payload: dict, score: float) -> Hit:
    keys = ("eid", "doc", "page", "text", "flags")
    return Hit(*(payload[k] for k in keys), score, payload["corpus"])


def _point_id(evidence_id: str) -> str:
    """Keyed by evidence ID alone, so a document name must be unique across corpora."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, evidence_id))


class Store:
    def __init__(self, url: str, collection: str = COLLECTION):
        self.client = AsyncQdrantClient(url=url)
        self.collection = collection

    async def ensure(self, dim: int) -> None:
        if not await self.client.collection_exists(self.collection):
            await self.client.create_collection(
                self.collection,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            )
            for key in ("corpus", "doc"):
                await self.client.create_payload_index(
                    self.collection, key, models.PayloadSchemaType.KEYWORD
                )
            await self.client.create_payload_index(
                self.collection, "page", models.PayloadSchemaType.INTEGER
            )
        await self.text_index()

    async def text_index(self) -> None:
        """Full-text index on chunk text, for in-document term search (idempotent)."""
        info = await self.client.get_collection(self.collection)
        if "text" not in (info.payload_schema or {}):
            params = models.TextIndexParams(
                type="text", tokenizer=models.TokenizerType.WORD, lowercase=True
            )
            await self.client.create_payload_index(self.collection, "text", params)

    async def upsert(self, corpus: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        await self.client.upsert(
            self.collection,
            [
                models.PointStruct(
                    id=_point_id(c.id),
                    vector=v,
                    payload={
                        "corpus": corpus,
                        "eid": c.id,
                        "doc": c.doc,
                        "page": c.page,
                        "text": c.text,
                        "flags": c.flags,
                    },
                )
                for c, v in zip(chunks, vectors, strict=True)
            ],
        )

    async def search(
        self, vector: list[float], k: int = 10, corpus: str | None = None, doc: str | None = None
    ) -> list[Hit]:
        must = [
            models.FieldCondition(key=key, match=models.MatchValue(value=val))
            for key, val in (("corpus", corpus), ("doc", doc))
            if val
        ]
        r = await self.client.query_points(
            self.collection,
            query=vector,
            limit=k,
            query_filter=models.Filter(must=must) if must else None,
        )
        return [_hit(p.payload, p.score) for p in r.points]

    async def page(self, doc: str, page: int) -> list[Hit]:
        """Every chunk of one page, in order."""
        return await self.pages(doc, page, page)

    async def pages(self, doc: str, first: int, last: int) -> list[Hit]:
        """Every chunk of pages first..last of one document, in reading order."""
        page_range = models.Range(gte=first, lte=last)
        return await self._scroll(
            [
                models.FieldCondition(key="doc", match=models.MatchValue(value=doc)),
                models.FieldCondition(key="page", range=page_range),
            ]
        )

    async def find(self, doc: str, terms: str, limit: int = 50) -> list[Hit]:
        """Chunks of one document containing every word of terms (Qdrant full-text index)."""
        return await self._scroll(
            [
                models.FieldCondition(key="doc", match=models.MatchValue(value=doc)),
                models.FieldCondition(key="text", match=models.MatchText(text=terms)),
            ],
            limit,
        )

    async def chunks(self, doc: str) -> list[Hit]:
        """Every chunk of one document, in reading order."""
        match = models.MatchValue(value=doc)
        return await self._scroll([models.FieldCondition(key="doc", match=match)], 100_000)

    async def _scroll(self, must: list, limit: int = 200) -> list[Hit]:
        points, _ = await self.client.scroll(
            self.collection, scroll_filter=models.Filter(must=must), limit=limit
        )
        hits = [_hit(p.payload, 1.0) for p in points]
        return sorted(hits, key=lambda h: (h.page, int(h.id.rsplit(".", 1)[1])))

    async def docs(self, corpus: str | None = None) -> set[str]:
        """Document names in a corpus, or in every corpus."""
        flt = None
        if corpus:
            match = models.MatchValue(value=corpus)
            flt = models.Filter(must=[models.FieldCondition(key="corpus", match=match)])
        r = await self.client.facet(
            self.collection,
            "doc",
            facet_filter=flt,
            limit=1_000_000,
        )
        return {h.value for h in r.hits}


BM25 = "qdrant/bm25"
KEYWORDS = "bm25"


class Keywords:
    """BM25 over every chunk's text, in a sparse collection beside a Store's; Qdrant tokenizes,
    stems and weighs terms itself (IDF over the collection)."""

    def __init__(self, url: str, collection: str):
        self.client = AsyncQdrantClient(url=url, cloud_inference=True)
        self.collection = collection
        self._exists = False

    @staticmethod
    def _doc(text: str) -> models.Document:
        return models.Document(text=text, model=BM25, options={"language": "english"})

    async def ensure(self) -> None:
        if await self.client.collection_exists(self.collection):
            return
        await self.client.create_collection(
            self.collection,
            vectors_config={},
            sparse_vectors_config={
                KEYWORDS: models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        for key in ("corpus", "doc"):
            await self.client.create_payload_index(
                self.collection, key, models.PayloadSchemaType.KEYWORD
            )

    async def upsert(self, corpus: str, chunks: list[Chunk]) -> None:
        await self.client.upsert(
            self.collection,
            [
                models.PointStruct(
                    id=_point_id(c.id),
                    vector={KEYWORDS: self._doc(c.text)},
                    payload={
                        "corpus": corpus,
                        "eid": c.id,
                        "doc": c.doc,
                        "page": c.page,
                        "text": c.text,
                        "flags": c.flags,
                    },
                )
                for c in chunks
            ],
            wait=False,
        )

    async def search(self, query: str, k: int = 10, corpus: str | None = None) -> list[Hit]:
        """Nothing until the index is built, so corpora indexed before BM25 still search."""
        self._exists = self._exists or await self.client.collection_exists(self.collection)
        if not self._exists:
            return []
        flt = None
        if corpus:
            match = models.MatchValue(value=corpus)
            flt = models.Filter(must=[models.FieldCondition(key="corpus", match=match)])
        r = await self.client.query_points(
            self.collection, query=self._doc(query), using=KEYWORDS, limit=k, query_filter=flt
        )
        return [_hit(p.payload, p.score) for p in r.points]

    async def docs(self, corpus: str) -> set[str]:
        match = models.MatchValue(value=corpus)
        flt = models.Filter(must=[models.FieldCondition(key="corpus", match=match)])
        r = await self.client.facet(self.collection, "doc", facet_filter=flt, limit=1_000_000)
        return {h.value for h in r.hits}
