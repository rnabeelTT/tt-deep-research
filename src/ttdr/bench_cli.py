import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ttdr import bench, recipe
from ttdr.clients import reasoning_params

app = typer.Typer(no_args_is_help=True, help="Component benchmarks and load tests.")
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


@app.command()
def load(
    concurrency: Annotated[list[int] | None, typer.Option("-c", help="Repeatable")] = None,
    jobs: int = 8,
    agent: str = "shallow_researcher",
    suite: str = "financebench",
    api: str = "http://127.0.0.1:8100",
    recipe_path: RecipeOpt = None,
) -> None:
    """Research jobs through AI-Q at each concurrency: reports/hour, latency, prefill/decode split."""
    import json

    from ttdr import load as loadtest

    llm_url = recipe.load(recipe_path).models.llm.url
    lines = Path(f"data/suites/{suite}.jsonl").read_text().splitlines()
    questions = [json.loads(x)["question"] for x in lines]
    table = Table(
        "jobs at once",
        "ok",
        "reports/h",
        "p50 s",
        "p95 s",
        "prompt tok",
        "output tok",
        "prefix hit",
        "prefill share",
        title=f"{agent} on {suite}",
    )
    offset = 0
    for c in concurrency or [1, 2, 4, 8]:
        qs = (questions[offset:] + questions)[:jobs]
        offset += jobs
        r = asyncio.run(loadtest.run(api, llm_url, agent, qs, c))
        table.add_row(
            str(c),
            f"{r['ok']}/{r['jobs']}",
            str(r["reports_per_hour"]),
            str(r["p50_s"]),
            str(r["p95_s"]),
            f"{r['prompt_tokens']:,}",
            f"{r['output_tokens']:,}",
            f"{r['prefix_hit']:.0%}",
            f"{r['prefill_share']:.0%}",
        )
        console.print(json.dumps(r))
    console.print(table)
