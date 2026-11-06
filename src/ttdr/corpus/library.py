"""The user's document collections: each a corpus saved under data/corpus/<name> and indexed in
the background, one upload batch at a time. The web UI's uploads go to the shared "library";
named collections are made with `ttdr corpus upload --collection`."""

from __future__ import annotations

import asyncio
import io
import json
import re
import time
import uuid
import zipfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree

from ttdr.corpus.sources import CORPORA_DIR

if TYPE_CHECKING:
    from ttdr.corpus import Corpus
    from ttdr.engine.ocr import OcrQueue
    from ttdr.recipe import Recipe

LIBRARY = "library"
UNNAMED = "Documents the user uploaded"
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")
SESSION = re.compile(r"s_[0-9a-f_]{32,}")
"""The UI names each chat's collection after the chat; those all mean the library."""
SUFFIXES = (".pdf", ".txt", ".md", ".docx")
MAX_DOCX_XML = 200 * 1024 * 1024
OCR_WAIT_S = 900
OCR_POLL_S = 3
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def doc_name(file_name: str, file_id: str) -> str:
    """A readable, unique document name (it titles citations): the file's stem and a short ID."""
    stem = re.sub(r"[^\w.-]+", "_", Path(file_name).stem).strip("._-")[:60]
    return f"{stem or 'document'}-{file_id[:6]}"


def docx_text(data: bytes) -> str:
    """A Word document's paragraphs, one per line."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if z.getinfo("word/document.xml").file_size > MAX_DOCX_XML:
            raise ValueError("document.xml is too large")
        root = ElementTree.fromstring(z.read("word/document.xml"))
    return "\n".join(
        "".join(t.text or "" for t in p.iter(f"{WORD}t")) for p in root.iter(f"{WORD}p")
    )


@dataclass
class File:
    file_id: str
    file_name: str
    doc: str
    size: int
    uploaded_at: str = field(default_factory=_now)
    status: str = "ingesting"
    """ingesting, success or failed: the UI's file states."""
    chunks: int = 0
    unread_pages: list[int] = field(default_factory=list)
    """Scanned pages search can't find yet: OCR didn't read them in time, or is off."""
    ingested_at: str | None = None
    error: str | None = None

    def info(self, collection: str) -> dict:
        return {
            "file_id": self.file_id,
            "file_name": self.file_name,
            "collection_name": collection,
            "status": self.status,
            "file_size": self.size,
            "chunk_count": self.chunks,
            "uploaded_at": self.uploaded_at,
            "ingested_at": self.ingested_at,
            "error_message": self.error,
            "metadata": {"doc": self.doc, "unread_pages": self.unread_pages},
        }


@dataclass
class Job:
    id: str
    collection: str
    files: list[File]
    submitted_at: str = field(default_factory=_now)
    started_at: str | None = None
    completed_at: str | None = None
    task: asyncio.Task | None = None

    @property
    def status(self) -> str:
        if self.completed_at:
            return "failed" if all(f.status == "failed" for f in self.files) else "completed"
        return "processing" if self.started_at else "pending"

    def info(self) -> dict:
        done = [f for f in self.files if f.status != "ingesting"]
        return {
            "job_id": self.id,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_files": len(self.files),
            "processed_files": len(done),
            "file_details": [
                {
                    "file_id": f.file_id,
                    "file_name": f.file_name,
                    "status": f.status,
                    "progress_percent": 0 if f.status == "ingesting" else 100,
                    "error_message": f.error,
                    "chunks_created": f.chunks,
                }
                for f in self.files
            ],
            "collection_name": self.collection,
            "backend": "qdrant",
            "error_message": None,
            "metadata": {},
        }


class Collection:
    def __init__(
        self,
        corpus: Corpus,
        name: str = LIBRARY,
        root: Path = CORPORA_DIR,
        ocr: OcrQueue | None = None,
        turn: asyncio.Lock | None = None,
    ):
        self.corpus = corpus
        self.name = name
        self.ocr = ocr
        """With a text-only embedder, scanned pages are OCR'd while indexing and their text
        embedded; otherwise their page images are, and OCR runs when search first finds them."""
        self.root = root / name
        self.manifest = self.root / "files.jsonl"
        self.about = self.root / "collection.json"
        self.files: dict[str, File] = {}
        self.jobs: dict[str, Job] = {}
        self._turn = turn or asyncio.Lock()
        """One batch indexes at a time; the embedder is shared with research."""
        if self.manifest.exists():
            for line in self.manifest.read_text().splitlines():
                f = File(**json.loads(line))
                if f.status == "ingesting":
                    f.status, f.error = "failed", "indexing was interrupted; upload it again"
                self.files[f.file_id] = f

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(asdict(f)) + "\n" for f in self.files.values()))
        tmp.replace(self.manifest)

    @property
    def description(self) -> str:
        return json.loads(self.about.read_text())["description"] if self.about.exists() else ""

    def describe(self, description: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.about.write_text(json.dumps({"description": description}))

    def info(self) -> dict:
        ok = [f for f in self.files.values() if f.status == "success"]
        return {
            "name": self.name,
            "description": self.description or UNNAMED,
            "file_count": len(self.files),
            "chunk_count": sum(f.chunks for f in ok),
            "created_at": min((f.uploaded_at for f in self.files.values()), default=None),
            "updated_at": max((f.uploaded_at for f in self.files.values()), default=None),
            "backend": "qdrant",
            "metadata": {},
        }

    def add(self, uploads: list[tuple[str, bytes]]) -> Job:
        """Save each upload (Word and Markdown as text) and index the batch in the background.
        A file of an unsupported type, or one that can't be read, is marked failed."""
        self.root.mkdir(parents=True, exist_ok=True)
        files = []
        for name, data in uploads:
            fid = uuid.uuid4().hex
            f = File(fid, Path(name).name, doc_name(name, fid), len(data))
            try:
                self._write(f, data)
            except Exception as e:  # noqa: BLE001 - the UI shows it on the file
                f.status, f.error = "failed", str(e) or type(e).__name__
            files.append(f)
            self.files[fid] = f
        self._save()
        job = Job(uuid.uuid4().hex, self.name, files)
        self.jobs[job.id] = job
        job.task = asyncio.create_task(self._index(job))
        return job

    def _write(self, f: File, data: bytes) -> None:
        suffix = Path(f.file_name).suffix.lower()
        if suffix not in SUFFIXES:
            raise ValueError(
                f"unsupported file type {suffix or '(none)'}; use {', '.join(SUFFIXES)}"
            )
        if suffix == ".pdf":
            (self.root / f"{f.doc}.pdf").write_bytes(data)
            return
        text = docx_text(data) if suffix == ".docx" else data.decode("utf-8", errors="replace")
        if not text.strip():
            raise ValueError("no text in this file")
        (self.root / f"{f.doc}.txt").write_text(text)

    def _path(self, f: File) -> Path:
        return next(p for s in (".pdf", ".txt") if (p := self.root / f"{f.doc}{s}").exists())

    async def _index(self, job: Job) -> None:
        async with self._turn:
            job.started_at = _now()
            with ProcessPoolExecutor(1) as pool:
                for f in job.files:
                    if f.status != "ingesting":
                        continue
                    try:
                        f.chunks, scanned = await self.corpus.index_document(
                            self._path(f), self.name, pool
                        )
                        if scanned and not self.corpus.images:
                            await self._read_scans(f, scanned)
                        f.status, f.ingested_at = "success", _now()
                    except Exception as e:  # noqa: BLE001 - the UI shows it on the file
                        f.status, f.error = "failed", f"{type(e).__name__}: {e}"
                        await self._remove(f)
                    self._save()
            job.completed_at = _now()

    async def _read_scans(self, f: File, pages: list[int]) -> None:
        """Queue the pages for the OCR worker and index each page's text as it lands."""
        todo = set(pages)
        if self.ocr:
            await self.ocr.backfill([(self.name, f.doc, p) for p in pages])
            deadline = time.monotonic() + OCR_WAIT_S
            while todo and time.monotonic() < deadline:
                for p in sorted(todo):
                    if (text := await self.ocr.text(f.doc, p)) is not None:
                        todo.discard(p)
                        f.chunks += await self.corpus.index_ocr_page(f.doc, p, text, self.name)
                if todo:
                    await asyncio.sleep(OCR_POLL_S)
        f.unread_pages = sorted(todo)

    async def _remove(self, f: File) -> None:
        await self.corpus.delete_document(f.doc)
        if self.ocr:
            await self.ocr.forget(f.doc)
        for s in (".pdf", ".txt"):
            (self.root / f"{f.doc}{s}").unlink(missing_ok=True)

    async def delete(self, file_ids: list[str]) -> int:
        """Remove files from the index and disk; files still indexing are left alone."""
        gone = [f for i in file_ids if (f := self.files.get(i)) and f.status != "ingesting"]
        for f in gone:
            await self._remove(f)
            del self.files[f.file_id]
        self._save()
        return len(gone)


def described(root: Path = CORPORA_DIR) -> dict[str, str]:
    """The user's collections and their descriptions, from disk."""
    return {LIBRARY: UNNAMED} | {
        p.parent.name: json.loads(p.read_text())["description"] or UNNAMED
        for p in root.glob("*/collection.json")
    }


async def catalog(recipe: Recipe, selected: tuple[str, ...] = ()) -> str:
    """The collections that hold documents (of SELECTED, if any), described for agents; a fresh
    install with nothing indexed lists none."""
    from ttdr.corpus.store import Store

    try:
        present = await Store(recipe.services.qdrant, recipe.embed.collection).corpora()
    except Exception:  # noqa: BLE001 - Qdrant down: describe what the recipe names
        return recipe.corpus_catalog()
    names = [n for n in (selected or sorted(present)) if n in present]
    return recipe.corpus_catalog(names, described())


class Library:
    """Every collection of the user's: the library, and named ones (a folder with a
    collection.json). Benchmark corpora have neither and are never touched."""

    def __init__(self, corpus: Corpus, root: Path = CORPORA_DIR, ocr: OcrQueue | None = None):
        self.corpus, self.root, self.ocr = corpus, root, ocr
        self._turn = asyncio.Lock()
        names = {LIBRARY} | {p.parent.name for p in root.glob("*/collection.json")}
        self.collections = {n: self._open(n) for n in sorted(names)}

    def _open(self, name: str) -> Collection:
        return Collection(self.corpus, name, self.root, self.ocr, self._turn)

    def get(self, name: str) -> Collection | None:
        """A chat's own collection is the library; other names must exist."""
        return self.collections[LIBRARY] if SESSION.fullmatch(name) else self.collections.get(name)

    def create(self, name: str, description: str | None) -> Collection:
        """A chat's collection is the library; a new named collection needs a free name."""
        if found := self.get(name):
            return found
        if not NAME.fullmatch(name):
            raise ValueError("a collection name is lowercase letters, digits, - and _")
        if (self.root / name).exists():
            raise ValueError(f"{name!r} is already a document set that isn't a collection")
        c = self._open(name)
        c.describe(description or "")
        self.collections[name] = c
        return c

    def job(self, job_id: str) -> Job | None:
        return next((c.jobs[job_id] for c in self.collections.values() if job_id in c.jobs), None)
