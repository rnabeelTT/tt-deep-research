"""Web search over SearXNG, reranked by embedding similarity.

Ported from Vane (MIT, ItzCrazyKns/Vane@348feca, baseSearch.ts): embed the query and each
snippet, drop weak matches and near-duplicates, keep the top 20. Vane's cutoffs (0.5, 0.75)
don't fit embeddinggemma-2, whose similarities are compressed: relevant snippets score
0.7-0.85, junk ~0.5, and distinct pages on one topic ~0.87 to each other.
"""

import math

import httpx

from ttdr.clients import Embedder
from ttdr.engine.evidence import Evidence, web_id

MIN_SIMILARITY = 0.6
MAX_BELOW_TOP = 0.2
NEAR_DUPLICATE = 0.97
TOP_N = 20
TIMEOUT_S = 10


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.hypot(*a), math.hypot(*b)
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


async def searxng(http: httpx.AsyncClient, base: str, query: str, **params) -> list[dict]:
    r = await http.get(
        f"{base}/search", params={"q": query, "format": "json", **params}, timeout=TIMEOUT_S
    )
    r.raise_for_status()
    seen, out = set(), []
    for res in r.json()["results"]:
        if res.get("url") and res["url"] not in seen:
            seen.add(res["url"])
            out.append(res)
    return out


async def rerank(
    embedder: Embedder, query: str, results: list[dict], top_n: int = TOP_N
) -> list[tuple[dict, float]]:
    if not results:
        return []
    [qv] = await embedder.queries([query], task="search result")
    vecs = await embedder.documents(
        [(r.get("title") or "none", r.get("content") or r.get("title")) for r in results]
    )
    scored = sorted(
        ((r, cosine(qv, v), v) for r, v in zip(results, vecs, strict=True)),
        key=lambda t: t[1],
        reverse=True,
    )
    floor = max(MIN_SIMILARITY, scored[0][1] - MAX_BELOW_TOP)
    kept: list[tuple[dict, float, list[float]]] = []
    for r, s, v in scored:
        if s < floor:
            break
        if all(cosine(v, kv) <= NEAR_DUPLICATE for _, _, kv in kept):
            kept.append((r, s, v))
    return [(r, s) for r, s, _ in kept[:top_n]]


async def search(
    http: httpx.AsyncClient, base: str, embedder: Embedder, query: str, k: int = 8, **params
) -> list[Evidence]:
    ranked = await rerank(embedder, query, await searxng(http, base, query, **params))
    return [
        Evidence(
            id=web_id(r["url"]),
            source="web",
            title=(r.get("title") or "").strip(),
            url=r["url"],
            text=(r.get("content") or "").strip(),
            score=round(s, 3),
        )
        for r, s in ranked[:k]
    ]
