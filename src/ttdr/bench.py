"""Component benchmarks for the LLM: decode throughput and tool-call reliability."""

import asyncio
import json
import time
from dataclasses import dataclass

import httpx

from ttdr.clients import LLM
from ttdr.recipe import Model

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web. Returns titles, URLs and snippets.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": "Fetch the text of a web page or PDF by URL.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
]

# (prompt, expected tool or None, substring expected in the arguments)
TOOL_CASES = [
    ("What did Tenstorrent announce this week?", "web_search", "tenstorrent"),
    ("Find recent FDA approvals for GLP-1 drugs.", "web_search", "fda"),
    ("Who won the 2026 Formula 1 constructors' championship?", "web_search", "formula"),
    ("Search for court opinions on fair use in AI training data.", "web_search", "fair use"),
    ("What is Apple's latest quarterly revenue?", "web_search", "apple"),
    (
        "Read https://www.sec.gov/Archives/edgar/data/320193/aapl-10k.htm and list the risks.",
        "fetch_page",
        "sec.gov",
    ),
    ("Summarize this paper: https://arxiv.org/abs/1706.03762", "fetch_page", "arxiv.org"),
    (
        "Open https://example.com/report.pdf and give me the key numbers.",
        "fetch_page",
        "report.pdf",
    ),
    ("What is 17 times 23?", None, ""),
    ("Rewrite this sentence more formally: we gotta ship it by friday.", None, ""),
    ("Translate 'good morning' into French.", None, ""),
    ("Write a haiku about rivers.", None, ""),
]


@dataclass
class ToolResult:
    prompt: str
    expected: str | None
    called: str | None
    ok: bool
    detail: str


def _check(expected: str | None, needle: str, message: dict) -> tuple[str | None, bool, str]:
    calls = message.get("tool_calls") or []
    if not calls:
        return None, expected is None, "no call"
    fn = calls[0]["function"]
    try:
        args = json.loads(fn["arguments"])
    except (json.JSONDecodeError, TypeError):
        return fn["name"], False, f"bad JSON args: {fn['arguments']!r}"[:120]
    ok = fn["name"] == expected and needle in json.dumps(args).lower()
    return fn["name"], ok, json.dumps(args)[:120]


async def tools(llm: LLM, params: dict, concurrency: int = 8) -> list[ToolResult]:
    sem = asyncio.Semaphore(concurrency)

    async def one(prompt: str, expected: str | None, needle: str) -> ToolResult:
        async with sem:
            r = await llm.chat(
                [{"role": "user", "content": prompt}],
                tools=TOOLS,
                tool_choice="auto",
                max_tokens=2048,
                **params,
            )
        called, ok, detail = _check(expected, needle, r["choices"][0]["message"])
        return ToolResult(prompt, expected, called, ok, detail)

    return list(await asyncio.gather(*(one(*c) for c in TOOL_CASES)))


async def decode(llm: LLM, concurrencies: list[int], tokens: int = 256) -> list[dict]:
    """Fixed-length generations (ignore_eos) to measure decode tok/s per stream and in total."""
    body = {
        "messages": [{"role": "user", "content": "Write a long essay about rivers."}],
        "max_tokens": tokens,
        "ignore_eos": True,
    }
    await llm.chat(**{**body, "max_tokens": 8})
    rows = []
    for n in concurrencies:
        start = time.perf_counter()
        rs = await asyncio.gather(*(llm.chat(**body) for _ in range(n)))
        wall = time.perf_counter() - start
        total = sum(r["usage"]["completion_tokens"] for r in rs) / wall
        rows.append({"concurrency": n, "total_tok_s": total, "per_stream_tok_s": total / n})
    return rows


async def with_llm(m: Model, fn, *args):
    async with httpx.AsyncClient(timeout=1800) as http:
        return await fn(LLM(http, m.url, m.model, m.sampling), *args)
