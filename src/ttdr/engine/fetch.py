"""Fetch a URL, extract readable text (HTML or PDF), chunk it, and rank chunks against a query."""

import asyncio
import threading

import httpx
import pypdfium2 as pdfium
import trafilatura

from ttdr.clients import Embedder
from ttdr.corpus.chunk import split
from ttdr.engine.evidence import Evidence, web_id
from ttdr.engine.web import cosine

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
TIMEOUT_S = 20
MAX_BYTES = 20_000_000
QUERY_PROMPT = "task: question answering | query: {q}"
DOC_PROMPT = "title: {title} | text: {text}"
EMBED_BATCH = 64


_PDFIUM = threading.Lock()  # pdfium isn't thread-safe; concurrent parses segfault
_LXML = threading.Lock()  # nor are trafilatura's shared lxml parsers (heap corruption under load)


def _pdf_text(data: bytes) -> str:
    """Pages and text pages are closed inside the lock, not left to the GC on another thread."""
    with _PDFIUM:
        doc = pdfium.PdfDocument(data)
        texts = []
        try:
            for i in range(len(doc)):
                page = doc[i]
                textpage = page.get_textpage()
                texts.append(textpage.get_text_bounded())
                textpage.close()
                page.close()
        finally:
            doc.close()
        return "\n\n".join(texts)


async def extract(http: httpx.AsyncClient, url: str) -> tuple[str, str]:
    """(title, text) of a page; raises on HTTP errors."""
    r = await http.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, follow_redirects=True
    )
    r.raise_for_status()
    data = r.content[:MAX_BYTES]
    if "pdf" in r.headers.get("content-type", "") or data[:5] == b"%PDF-":
        return url.rsplit("/", 1)[-1], await asyncio.to_thread(_pdf_text, data)
    return await asyncio.to_thread(_html_text, r.text, url)


def _html_text(html: str, url: str) -> tuple[str, str]:
    with _LXML:
        text = trafilatura.extract(
            html, include_tables=True, include_links=False, favor_recall=True
        )
        meta = trafilatura.extract_metadata(html)
    return (meta.title if meta and meta.title else url), text or ""


async def chunks(
    embedder: Embedder, url: str, title: str, text: str, query: str | None, k: int
) -> list[Evidence]:
    parts = split(text)
    items = [Evidence(web_id(url, n), "web", title, url, t) for n, t in enumerate(parts)]
    if not query or len(items) <= k:
        return items[:k]
    [qv] = await embedder.embed([QUERY_PROMPT.format(q=query)])
    vecs = []
    for i in range(0, len(items), EMBED_BATCH):
        batch = items[i : i + EMBED_BATCH]
        vecs += await embedder.embed([DOC_PROMPT.format(title=title, text=e.text) for e in batch])
    for e, v in zip(items, vecs, strict=True):
        e.score = round(cosine(qv, v), 3)
    return sorted(items, key=lambda e: e.score, reverse=True)[:k]
