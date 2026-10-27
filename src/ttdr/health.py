import asyncio
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from ttdr.clients import Clients, connect
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


async def _chat(c: Clients) -> str:
    r = await c.llm.chat(PING, max_tokens=64)
    return f"{r['usage']['completion_tokens']} tokens"


async def _embed(c: Clients) -> str:
    return f"dim {len((await c.embed.embed(['health check']))[0])}"


async def _decide(c: Clients) -> str:
    a = await c.decide.ask(
        "Health check.", {"q": {"type": "noul", "instructions": "Is this a test?"}}
    )
    return f"noul {a['q']['noul']:.2f}"


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
        c = connect(recipe, http)
        return list(
            await asyncio.gather(
                _timed("llm", _chat(c)),
                _timed("embed", _embed(c)),
                _timed("decide", _decide(c)),
                _timed("qdrant", _qdrant(http, s.qdrant)),
                _timed("searxng", _searxng(http, s.searxng)),
                _timed("redis", _redis(s.redis)),
            )
        )
