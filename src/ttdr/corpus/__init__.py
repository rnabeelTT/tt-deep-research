"""Private documents: extract, chunk, embed, store and retrieve."""

from __future__ import annotations

import asyncio
import base64
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from ttdr.corpus import chunk, pdf
from ttdr.corpus.sources import CORPORA_DIR
from ttdr.corpus.store import Hit, Store

if TYPE_CHECKING:
    from ttdr.clients import Embedder

BATCH = 32
IMAGE_BATCH = 4
PAGE_SCALE = 1.0
PAGE_CACHE = 512


class Corpus:
    def __init__(self, embedder: Embedder, store: Store, root: Path = CORPORA_DIR):
        self.embedder = embedder
        self.store = store
        self.root = root
        self._pages: dict[tuple[str, str, int, float], str] = {}
        self._render = asyncio.Lock()

    async def page_images(self, hits: list[Hit], scale: float = PAGE_SCALE) -> list[str | None]:
        """Each hit's page as a base64 PNG (None when its PDF isn't on disk). pdfium isn't
        thread-safe, so pages render one document at a time; recent pages are cached."""
        want: dict[tuple[str, str], set[int]] = {}
        for h in hits:
            if (h.corpus, h.doc, h.page, scale) not in self._pages:
                want.setdefault((h.corpus, h.doc), set()).add(h.page)
        for (corpus, doc), pages in want.items():
            path = self.root / corpus / f"{doc}.pdf"
            if not path.exists():
                continue
            async with self._render:
                pngs = await asyncio.to_thread(pdf.render_png, path, sorted(pages), scale)
            for page, png in zip(sorted(pages), pngs, strict=True):
                self._pages[(corpus, doc, page, scale)] = base64.b64encode(png).decode()
        while len(self._pages) > PAGE_CACHE:
            self._pages.pop(next(iter(self._pages)))
        return [self._pages.get((h.corpus, h.doc, h.page, scale)) for h in hits]

    async def search(
        self, query: str, k: int = 10, corpus: str | None = None, doc: str | None = None
    ) -> list[Hit]:
        [vec] = await self.embedder.queries([query])
        return await self.store.search(vec, k, corpus, doc)

    async def index_pdf(
        self, path: Path, corpus: str, pool: ProcessPoolExecutor | None = None
    ) -> list[pdf.Page]:
        """pdfium isn't thread-safe, so extraction runs in a process pool."""
        pages = await asyncio.get_running_loop().run_in_executor(pool, pdf.read, path)
        chunks = [c for p in pages if "image_only" not in p.flags for c in chunk.chunks(p)]
        for i in range(0, len(chunks), BATCH):
            batch = chunks[i : i + BATCH]
            vecs = await self.embedder.documents([(c.doc, c.text) for c in batch])
            await self.store.upsert(corpus, batch, vecs)
        await self.index_images(path, [p for p in pages if "image_only" in p.flags], corpus, pool)
        return pages

    async def index_images(
        self,
        path: Path,
        pages: list[pdf.Page],
        corpus: str,
        pool: ProcessPoolExecutor | None = None,
    ) -> None:
        """Image-only pages are embedded as page images, so search can find them before OCR."""
        if not pages:
            return
        pngs = await asyncio.get_running_loop().run_in_executor(
            pool, pdf.render_png, path, [p.page for p in pages], 1.0
        )
        for i in range(0, len(pages), IMAGE_BATCH):
            batch = pages[i : i + IMAGE_BATCH]
            vecs = await self.embedder.embed_images(
                [base64.b64encode(b).decode() for b in pngs[i : i + IMAGE_BATCH]]
            )
            await self.store.upsert(
                corpus, [chunk.Chunk(p.doc, p.page, 0, "", p.flags) for p in batch], vecs
            )

    async def index_dir(self, root: Path, corpus: str, concurrency: int = 4) -> dict:
        """Index every PDF under root; page flags go to root/pages.jsonl for the OCR stage."""
        await self.store.ensure(len((await self.embedder.queries(["size"]))[0]))
        done = await self.store.docs(corpus)
        todo = [p for p in sorted(root.glob("*.pdf")) if p.stem not in done]
        if clash := {p.stem for p in todo} & (await self.store.docs() - done):
            raise ValueError(
                f"already indexed in another corpus, and would overwrite it: {sorted(clash)}"
            )
        sem = asyncio.Semaphore(concurrency)
        manifest = root / "pages.jsonl"

        async def one(path: Path) -> None:
            async with sem:
                pages = await self.index_pdf(path, corpus, pool)
            with manifest.open("a") as f:
                for p in pages:
                    f.write(json.dumps({**asdict(p), "text": None, "chars": len(p.text)}) + "\n")

        with ProcessPoolExecutor(concurrency) as pool:
            await asyncio.gather(*(one(p) for p in todo))
        return {"indexed": len(todo), "skipped": len(done)}
