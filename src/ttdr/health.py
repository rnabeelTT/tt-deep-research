import asyncio
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from ttdr.clients import client
from ttdr.recipe import Recipe

PING = [{"role": "user", "content": "Reply with OK."}]


@dataclass
class Check:
    name: str
    ok: bool
    ms: float
    detail: str


async def _timed(name: str, probe: Awaitable[str]) -> Check:
    start = time.perf_counter()
    try:
        detail, ok = await probe, True
    except Exception as e:  # noqa: BLE001 - any probe failure is a "down" result
        detail, ok = f"{type(e).__name__}: {e}"[:120], False
    return Check(name, ok, (time.perf_counter() - start) * 1000, detail)


async def _chat(llm) -> str:
    r = await llm.chat(PING, max_tokens=64)
    return f"{r['usage']['completion_tokens']} tokens"


async def _embed(embedder) -> str:
    return f"dim {len((await embedder.embed(['health check']))[0])}"


async def _decide(decider) -> str:
    a = await decider.ask(
        "Health check.", {"q": {"type": "noul", "instructions": "Is this a test?"}}
    )
    return f"noul {a['q']['noul']:.2f}"


async def _check(checker) -> str:
    [p] = await checker.support([("The sky is blue.", "The sky is blue.")])
    return f"support {p:.2f}"


async def _rerank(reranker) -> str:
    s = await reranker.scores("What colour is the sky?", ["The sky is blue.", "Grass is green."])
    return f"scores {s[0]:.2f} > {s[1]:.2f}" if s[0] > s[1] else f"misordered {s}"


PROBES = {"chat": _chat, "embed": _embed, "decide": _decide, "check": _check, "rerank": _rerank}


async def _qdrant(http: httpx.AsyncClient, url: str) -> str:
    r = await http.get(f"{url}/readyz")
    r.raise_for_status()
    return r.text.strip()


async def _searxng(http: httpx.AsyncClient, url: str) -> str:
    r = await http.get(f"{url}/search", params={"q": "tenstorrent", "format": "json"})
    r.raise_for_status()
    n = len(r.json()["results"])
    if not n:
        raise RuntimeError("no results")
    return f"{n} results"


async def _redis(url: str) -> str:
    u = urlparse(url)
    reader, writer = await asyncio.open_connection(u.hostname, u.port or 6379)
    try:
        writer.write(b"PING\r\n")
        await writer.drain()
        reply = (await reader.readline()).strip().decode()
    finally:
        writer.close()
    if reply != "+PONG":
        raise RuntimeError(reply)
    return "PONG"


async def run(recipe: Recipe, timeout: float = 60) -> list[Check]:
    s = recipe.services
    async with httpx.AsyncClient(timeout=timeout) as http:
        roles = {}
        for role, name in recipe.roles.items():
            roles.setdefault(name, []).append(role)
        probes = [
            _timed(f"{name} ({', '.join(rs)})", PROBES[m.kind](client(http, m)))
            for name, m in recipe.served().items()
            for rs in [roles[name]]
        ]
        probes += [_timed("qdrant", _qdrant(http, s.qdrant)), _timed("redis", _redis(s.redis))]
        if s.searxng:
            probes.append(_timed("searxng", _searxng(http, s.searxng)))
        return list(await asyncio.gather(*probes))
