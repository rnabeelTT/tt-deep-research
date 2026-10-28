"""Load test: N research jobs at a given concurrency against AI-Q's job API.

Reports per hour, time per report, and the LLM's prefill/decode split from vLLM's metrics.
"""

import asyncio
import re
import time

import httpx

METRICS = (
    "prompt_tokens_total",
    "generation_tokens_total",
    "prefix_cache_hits_total",
    "prefix_cache_queries_total",
    "request_prefill_time_seconds_sum",
    "request_decode_time_seconds_sum",
)


async def metrics(http: httpx.AsyncClient, llm_url: str) -> dict[str, float]:
    text = (await http.get(llm_url.removesuffix("/v1") + "/metrics")).text
    out = {}
    for name in METRICS:
        m = re.search(rf"^vllm:{name}\{{[^}}]*\}} ([0-9.e+]+)$", text, re.MULTILINE)
        out[name] = float(m[1]) if m else 0.0
    return out


async def job(http: httpx.AsyncClient, api: str, agent: str, question: str) -> tuple[str, float]:
    start = time.perf_counter()
    r = await http.post(
        f"{api}/v1/jobs/async/submit",
        json={"agent_type": agent, "input": question, "data_sources": ["ttdr"]},
    )
    r.raise_for_status()
    jid = r.json()["job_id"]
    status = "submitted"
    while status in ("submitted", "running"):
        await asyncio.sleep(2)
        status = (await http.get(f"{api}/v1/jobs/async/job/{jid}")).json()["status"]
    return status, time.perf_counter() - start


async def run(api: str, llm_url: str, agent: str, questions: list[str], concurrency: int) -> dict:
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=60) as http:
        before = await metrics(http, llm_url)
        start = time.perf_counter()

        async def one(q):
            async with sem:
                return await job(http, api, agent, q)

        results = await asyncio.gather(*(one(q) for q in questions))
        wall = time.perf_counter() - start
        after = await metrics(http, llm_url)
    d = {k: after[k] - before[k] for k in METRICS}
    times = sorted(t for s, t in results if s == "success")
    prefill, decode = d["request_prefill_time_seconds_sum"], d["request_decode_time_seconds_sum"]
    return {
        "concurrency": concurrency,
        "jobs": len(results),
        "ok": len(times),
        "wall_s": round(wall),
        "reports_per_hour": round(3600 * len(times) / wall, 1),
        "p50_s": round(times[len(times) // 2]) if times else None,
        "p95_s": round(times[min(len(times) - 1, int(0.95 * len(times)))]) if times else None,
        "prompt_tokens": int(d["prompt_tokens_total"]),
        "output_tokens": int(d["generation_tokens_total"]),
        "prefix_hit": round(
            d["prefix_cache_hits_total"] / (d["prefix_cache_queries_total"] or 1), 2
        ),
        "prefill_share": round(prefill / ((prefill + decode) or 1), 2),
    }
