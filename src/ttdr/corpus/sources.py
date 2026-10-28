"""Pinned document sources for eval corpora."""

import asyncio
import json
from pathlib import Path

import httpx

CORPORA_DIR = Path("data/corpus")
FINANCEBENCH_PDFS = (
    "https://raw.githubusercontent.com/patronus-ai/financebench/"
    "cc39aeb4afdf33909ee1412188bf89035950c2eb/pdfs/{doc}.pdf"
)


def financebench_docs(suite: Path = Path("data/suites/financebench.jsonl")) -> list[str]:
    return sorted({json.loads(line)["tags"]["doc"] for line in suite.open()})


async def fetch_financebench(concurrency: int = 8) -> Path:
    out = CORPORA_DIR / "financebench"
    out.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)

    async def one(http: httpx.AsyncClient, doc: str) -> None:
        path = out / f"{doc}.pdf"
        if path.exists():
            return
        async with sem:
            r = await http.get(FINANCEBENCH_PDFS.format(doc=doc))
            r.raise_for_status()
        path.write_bytes(r.content)

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as http:
        await asyncio.gather(*(one(http, d) for d in financebench_docs()))
    return out


FETCHERS = {"financebench": fetch_financebench}
