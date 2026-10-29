import asyncio
import json

from ttdr.engine.ocr import HITS, PENDING, QUEUE, OcrQueue


class FakeRedis:
    def __init__(self):
        self.kv, self.sets, self.zsets, self.lists = {}, {}, {}, {}

    async def get(self, k):
        return self.kv.get(k)

    async def sadd(self, k, v):
        s = self.sets.setdefault(k, set())
        new = v not in s
        s.add(v)
        return int(new)

    async def zincrby(self, k, n, v):
        z = self.zsets.setdefault(k, {})
        z[v] = z.get(v, 0) + n

    async def zrange(self, k, a, b, withscores=False):
        return list(self.zsets.get(k, {}).items())

    async def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)

    async def rpush(self, k, v):
        self.lists.setdefault(k, []).append(v)


def test_requests_dedupe_count_and_carry_no_host_paths():
    r = FakeRedis()
    q = OcrQueue(r)
    asyncio.run(q.request("financebench", "WALMART_2019_10K", 89))
    asyncio.run(q.request("financebench", "WALMART_2019_10K", 89))
    assert [json.loads(j) for j in r.lists[QUEUE]] == [
        {"corpus": "financebench", "doc": "WALMART_2019_10K", "page": 89}
    ]
    assert r.zsets[HITS]["WALMART_2019_10K#p89"] == 2 and r.sets[PENDING] == {
        "WALMART_2019_10K#p89"
    }


def test_backfill_skips_done_pages_and_puts_most_retrieved_first():
    r = FakeRedis()
    r.kv["ocr:text:A#p1"] = "done"
    r.zsets[HITS] = {"B#p2": 5.0}
    n = asyncio.run(OcrQueue(r).backfill([("c", "A", 1), ("c", "C", 3), ("c", "B", 2)]))
    assert n == 2
    assert [json.loads(j)["doc"] for j in r.lists[QUEUE]] == ["B", "C"]
