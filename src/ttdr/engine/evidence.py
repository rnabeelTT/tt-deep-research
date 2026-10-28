"""Evidence store: every passage a tool returns gets a stable ID that verify and the writer can cite."""

import hashlib
import json
from dataclasses import asdict, dataclass

from redis.asyncio import Redis

TTL_S = 7 * 24 * 3600


@dataclass
class Evidence:
    id: str
    source: str  # "web" or a corpus name
    title: str
    url: str
    text: str
    score: float = 0.0
    flags: tuple[str, ...] = ()


def web_id(url: str, n: int | None = None) -> str:
    """W<hash> for a search snippet, W<hash>.<n> for passage n of the fetched page."""
    base = f"W{hashlib.sha1(url.encode()).hexdigest()[:8]}"
    return base if n is None else f"{base}.{n}"


class EvidenceStore:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def put(self, items: list[Evidence]) -> None:
        if items:
            async with self.redis.pipeline() as p:
                for e in items:
                    p.set(f"ev:{e.id}", json.dumps(asdict(e)), ex=TTL_S)
                await p.execute()

    async def get(self, eid: str) -> Evidence | None:
        raw = await self.redis.get(f"ev:{eid}")
        if not raw:
            return None
        d = json.loads(raw)
        return Evidence(**{**d, "flags": tuple(d.get("flags", ()))})

    async def cached(self, key: str) -> str | None:
        return await self.redis.get(f"cache:{key}")

    async def cache(self, key: str, value: str, ttl_s: int = 24 * 3600) -> None:
        await self.redis.set(f"cache:{key}", value, ex=ttl_s)
