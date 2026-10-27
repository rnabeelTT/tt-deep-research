import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ttdr import bench, recipe
from ttdr.clients import reasoning_params

app = typer.Typer(no_args_is_help=True, help="Component benchmarks for the recipe's LLM.")
console = Console()

RecipeOpt = Annotated[Path | None, typer.Option("--recipe", help="Recipe file")]


@app.command()
def decode(
    concurrency: Annotated[list[int] | None, typer.Option("-c", help="Repeatable")] = None,
    tokens: int = 256,
    recipe_path: RecipeOpt = None,
) -> None:
    """Decode tok/s per stream and in total, at each concurrency."""
    m = recipe.load(recipe_path).models.llm
    rows = asyncio.run(bench.with_llm(m, bench.decode, concurrency or [1, 8, 16, 32], tokens))
    table = Table(
        "concurrency", "per stream tok/s", "total tok/s", title=f"{m.bundle} {m.sampling}"
    )
    for r in rows:
        table.add_row(
            str(r["concurrency"]), f"{r['per_stream_tok_s']:.1f}", f"{r['total_tok_s']:.1f}"
        )
    console.print(table)


@app.command()
def tools(
    effort: Annotated[str | None, typer.Option(help="gpt-oss reasoning_effort")] = None,
    thinking: Annotated[bool | None, typer.Option(help="Qwen-style enable_thinking")] = None,
    recipe_path: RecipeOpt = None,
) -> None:
    """Tool-call reliability: right tool, valid JSON args, no call when none is needed."""
    m = recipe.load(recipe_path).models.llm
    results = asyncio.run(bench.with_llm(m, bench.tools, reasoning_params(effort, thinking)))
    table = Table("ok", "expected", "called", "detail", "prompt", title=m.bundle)
    for r in results:
        mark = "[green]✓[/]" if r.ok else "[red]✗[/]"
        table.add_row(mark, r.expected or "—", r.called or "—", r.detail, r.prompt[:40])
    console.print(table)
    console.print(f"passed {sum(r.ok for r in results)}/{len(results)}")
