"""Chunks and their vectors in Qdrant: one collection, filtered by corpus."""

import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from ttdr.corpus.chunk import Chunk

COLLECTION = "chunks"
DIM = 768


@dataclass
class Hit:
    id: str
    doc: str
    page: int
    text: str
    flags: list[str]
    score: float
    corpus: str = ""


def _point_id(evidence_id: str) -> str:
    """Keyed by evidence ID alone, so a document name must be unique across corpora."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, evidence_id))


class Store:
    def __init__(self, url: str):
        self.client = AsyncQdrantClient(url=url)

    async def ensure(self) -> None:
        if not await self.client.collection_exists(COLLECTION):
            await self.client.create_collection(
                COLLECTION,
                vectors_config=models.VectorParams(size=DIM, distance=models.Distance.COSINE),
            )
            for key in ("corpus", "doc"):
                await self.client.create_payload_index(
                    COLLECTION, key, models.PayloadSchemaType.KEYWORD
                )
            await self.client.create_payload_index(
                COLLECTION, "page", models.PayloadSchemaType.INTEGER
            )

    async def upsert(self, corpus: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        await self.client.upsert(
            COLLECTION,
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
            COLLECTION,
            query=vector,
            limit=k,
            query_filter=models.Filter(must=must) if must else None,
        )
        return [
            Hit(
                p.payload["eid"],
                p.payload["doc"],
                p.payload["page"],
                p.payload["text"],
                p.payload["flags"],
                p.score,
                p.payload["corpus"],
            )
            for p in r.points
        ]

    async def page(self, doc: str, page: int) -> list[Hit]:
        """Every chunk of one page, in order."""
        points, _ = await self.client.scroll(
            COLLECTION,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(key="doc", match=models.MatchValue(value=doc)),
                    models.FieldCondition(key="page", match=models.MatchValue(value=page)),
                ]
            ),
            limit=100,
        )
        hits = [
            Hit(p.payload["eid"], doc, page, p.payload["text"], p.payload["flags"], 1.0)
            for p in points
        ]
        return sorted(hits, key=lambda h: int(h.id.rsplit(".", 1)[1]))

    async def docs(self, corpus: str | None = None) -> set[str]:
        """Document names in a corpus, or in every corpus."""
        must = [models.FieldCondition(key="corpus", match=models.MatchValue(value=corpus))]
        r = await self.client.facet(
            COLLECTION,
            "doc",
            facet_filter=models.Filter(must=must) if corpus else None,
            limit=10_000,
        )
        return {h.value for h in r.hits}
