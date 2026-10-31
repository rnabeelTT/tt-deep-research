"""Pinned document sources for eval corpora."""

import asyncio
import json
import tarfile
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


UNIDOC = ("Salesforce/UniDoc-Bench", "2e22c592438d1e52c39bd769aab2901d596af746")
UNIDOC_DOMAINS = ("energy", "construction")


def _extract_pdfs(archive: Path, out: Path) -> int:
    """Regular *.pdf members only, flattened to their file name (the archive is untrusted)."""
    n = 0
    with tarfile.open(archive) as tar:
        for m in tar:
            name = Path(m.name).name
            if not (m.isfile() and name.endswith(".pdf")) or name.startswith("."):
                continue
            if not (out / name).exists():
                (out / name).write_bytes(tar.extractfile(m).read())
            n += 1
    return n


async def fetch_unidoc() -> Path:
    """UniDoc-Bench's PDFs for our domains, as one corpus (~1,490 documents; the domains share
    a few identical files)."""
    from huggingface_hub import hf_hub_download

    out = CORPORA_DIR / "unidoc"
    out.mkdir(parents=True, exist_ok=True)
    for domain in UNIDOC_DOMAINS:
        archive = await asyncio.to_thread(
            hf_hub_download,
            UNIDOC[0],
            f"{domain}_pdfs.tar.gz",
            repo_type="dataset",
            revision=UNIDOC[1],
        )
        await asyncio.to_thread(_extract_pdfs, Path(archive), out)
    return out


FETCHERS = {"financebench": fetch_financebench, "unidoc": fetch_unidoc}
