import asyncio
import json
import time
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from ttdr import jsonl, recipe
from ttdr.clients import connect
from ttdr.corpus import sources

app = typer.Typer(no_args_is_help=True, help="Fetch, index and search document corpora.")
console = Console()
RecipeOpt = Annotated[Path | None, typer.Option("--recipe", help="Recipe file")]


def _with_corpus(recipe_path: Path | None, fn):
    async def go():
        async with httpx.AsyncClient(timeout=300) as http:
            return await fn(connect(recipe.load(recipe_path), http).corpus)

    return asyncio.run(go())


@app.command()
def fetch(name: str) -> None:
    """Download a pinned corpus into data/corpus/<name>."""
    console.print(asyncio.run(sources.FETCHERS[name]()))


@app.command()
def index(
    name: str,
    concurrency: int = 4,
    keywords: Annotated[bool, typer.Option(help="Also build the BM25 keyword index")] = True,
    recipe_path: RecipeOpt = None,
) -> None:
    """Extract, chunk and embed every document in data/corpus/<name> into Qdrant (resumable)."""
    start = time.perf_counter()
    stats = _with_corpus(
        recipe_path,
        lambda c: c.index_dir(sources.CORPORA_DIR / name, name, concurrency, keywords),
    )
    console.print({**stats, "seconds": round(time.perf_counter() - start)})


@app.command()
def upload(
    folder: Path,
    url: Annotated[str, typer.Option(help="The app's API (ttdr ui-server)")] = (
        "http://localhost:8200"
    ),
    batch: int = 20,
) -> None:
    """Add every PDF, Word, Markdown and text file under FOLDER to the web UI's document
    library, through the running app, as the UI's uploads do."""
    from ttdr.corpus.library import SUFFIXES

    paths = sorted(p for p in folder.rglob("*") if p.suffix.lower() in SUFFIXES)
    console.print(f"{len(paths)} files")
    failed = 0
    with httpx.Client(base_url=url, timeout=600) as http:
        for i in range(0, len(paths), batch):
            part = paths[i : i + batch]
            files = [("files", (p.name, p.read_bytes())) for p in part]
            r = http.post("/v1/collections/library/documents", files=files)
            r.raise_for_status()
            while (s := http.get(f"/v1/documents/{r.json()['job_id']}/status").json())[
                "status"
            ] in ("pending", "processing"):
                time.sleep(3)
            for f in s["file_details"]:
                if f["status"] == "failed":
                    failed += 1
                    console.print(f"[red]failed[/] {f['file_name']}: {f['error_message']}")
            console.print(f"{min(i + batch, len(paths))}/{len(paths)} indexed")
    raise typer.Exit(1 if failed else 0)


@app.command()
def keywords(name: str, concurrency: int = 8, recipe_path: RecipeOpt = None) -> None:
    """Add every chunk of an indexed corpus to the BM25 collection (resumable)."""
    start = time.perf_counter()
    stats = _with_corpus(
        recipe_path,
        lambda c: c.index_keywords(name, concurrency),
    )
    console.print({**stats, "seconds": round(time.perf_counter() - start)})


@app.command()
def search(
    query: str,
    k: int = 5,
    corpus: str | None = None,
    doc: str | None = None,
    recipe_path: RecipeOpt = None,
) -> None:
    """Top-k passages for a query."""
    for h in _with_corpus(recipe_path, lambda c: c.search(query, k, corpus, doc)):
        console.print(f"[bold]{h.id}[/] {h.score:.3f} {h.flags}\n{h.text[:300]}\n")


@app.command()
def recall(
    suite: str = "financebench",
    ks: Annotated[list[int] | None, typer.Option("--k")] = None,
    triage: Annotated[
        bool, typer.Option(help="Rerank 3x candidates with the decision model")
    ] = False,
    rerank: Annotated[
        bool, typer.Option(help="Rerank a wider pool with the recipe's rerank model")
    ] = False,
    pool: Annotated[int | None, typer.Option(help="Rerank pool size (default 30)")] = None,
    mode: Annotated[
        str, typer.Option(help="What the reranker sees: text, page (image) or both")
    ] = "text",
    known_doc: Annotated[bool, typer.Option(help="Also search within each known doc")] = True,
    keywords: Annotated[int, typer.Option(help="Add this many BM25 hits per search")] = 0,
    recipe_path: RecipeOpt = None,
) -> None:
    """Share of questions with a gold evidence page in the top k: whole corpus vs the known doc."""
    ks = sorted(ks or [1, 5, 10, 20])
    items = [json.loads(x) for x in jsonl.lines(Path(f"data/suites/{suite}.jsonl"))]

    r = recipe.load(recipe_path)

    async def go(c):
        sem = asyncio.Semaphore(16)
        ranker, n = None, ks[-1]
        if rerank:
            from ttdr.engine.rerank import POOL, Rerank

            if not r.rerank:
                raise typer.BadParameter("the recipe has no rerank role")
            ranker = Rerank(connect(r, c.embedder.http).rerank, pool or POOL, mode, c.page_images)
            n = max(n, ranker.pool)
        elif triage:
            from ttdr.engine.triage import OVERFETCH, Triage
            from ttdr.engine.verify import window_chars

            if not r.decide:
                raise typer.BadParameter("the recipe has no decide role")
            decider = connect(r, c.embedder.http).decide
            ranker = Triage(decider, window_chars(r.decide.max_state_tokens))
            n = n * OVERFETCH

        async def one(item, doc):
            async with sem:
                hits = await c.search(item["question"], n, suite, doc, keywords)
                if ranker:
                    hits = await ranker.rerank(item["question"], hits)
            hits = hits[: ks[-1]]
            gold = {(e["doc"], e["page"]) for e in item["evidence"]}
            return next((r for r, h in enumerate(hits) if (h.doc, h.page) in gold), None)

        shared = await asyncio.gather(*(one(i, None) for i in items))
        if not known_doc:
            return shared, None
        single = await asyncio.gather(*(one(i, i["tags"]["doc"]) for i in items))
        return shared, single

    shared, single = _with_corpus(recipe_path, go)
    table = Table("setting", *(f"recall@{k}" for k in ks))
    for name, ranks in (("whole corpus", shared), ("known doc", single)):
        if ranks is None:
            continue
        table.add_row(
            name, *(f"{sum(r is not None and r < k for r in ranks) / len(ranks):.1%}" for k in ks)
        )
    console.print(table)


def _ocr_queue(recipe_path: Path | None):
    from redis.asyncio import Redis

    from ttdr.engine.ocr import OcrQueue

    return OcrQueue(Redis.from_url(recipe.load(recipe_path).services.redis, decode_responses=True))


@app.command("ocr-backfill")
def ocr_backfill(name: str, recipe_path: RecipeOpt = None) -> None:
    """Queue every flagged page of a corpus for OCR, most-retrieved first."""
    from ttdr.engine.ocr import NEEDS_OCR

    root = sources.CORPORA_DIR / name
    pages = [
        (name, p["doc"], p["page"])
        for p in map(json.loads, jsonl.lines(root / "pages.jsonl"))
        if NEEDS_OCR & set(p["flags"])
    ]
    console.print(f"queued {asyncio.run(_ocr_queue(recipe_path).backfill(pages))} of {len(pages)}")


@app.command("ocr-status")
def ocr_status(recipe_path: RecipeOpt = None) -> None:
    """Pages queued, in flight and done."""
    from ttdr.engine.ocr import PENDING, QUEUE

    async def go():
        r = _ocr_queue(recipe_path).redis
        done = len([k async for k in r.scan_iter("ocr:text:*")])
        return await r.llen(QUEUE), await r.scard(PENDING), done

    queued, pending, done = asyncio.run(go())
    console.print(f"queued {queued} · pending {pending} · done {done}")
