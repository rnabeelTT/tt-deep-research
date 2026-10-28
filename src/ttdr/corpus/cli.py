import asyncio
import json
import time
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from ttdr import recipe
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
def index(name: str, concurrency: int = 4, recipe_path: RecipeOpt = None) -> None:
    """Extract, chunk and embed every PDF in data/corpus/<name> into Qdrant (resumable)."""
    start = time.perf_counter()
    stats = _with_corpus(
        recipe_path, lambda c: c.index_dir(sources.CORPORA_DIR / name, name, concurrency)
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
    recipe_path: RecipeOpt = None,
) -> None:
    """Share of questions with a gold evidence page in the top k: whole corpus vs the known doc."""
    ks = sorted(ks or [1, 5, 10, 20])
    items = [json.loads(x) for x in Path(f"data/suites/{suite}.jsonl").read_text().splitlines()]

    async def go(c):
        sem = asyncio.Semaphore(16)

        async def one(item, doc):
            async with sem:
                hits = await c.search(item["question"], ks[-1], suite, doc)
            gold = {(e["doc"], e["page"]) for e in item["evidence"]}
            return next((r for r, h in enumerate(hits) if (h.doc, h.page) in gold), None)

        shared = await asyncio.gather(*(one(i, None) for i in items))
        single = await asyncio.gather(*(one(i, i["tags"]["doc"]) for i in items))
        return shared, single

    shared, single = _with_corpus(recipe_path, go)
    table = Table("setting", *(f"recall@{k}" for k in ks))
    for name, ranks in (("whole corpus", shared), ("known doc", single)):
        table.add_row(
            name, *(f"{sum(r is not None and r < k for r in ranks) / len(ranks):.1%}" for k in ks)
        )
    console.print(table)
