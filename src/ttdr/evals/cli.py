import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ttdr import recipe
from ttdr.evals import claims, datasets, grading, report, runner

app = typer.Typer(no_args_is_help=True, help="Build suites, run systems, grade and compare.")
console = Console()

RecipeOpt = Annotated[Path | None, typer.Option("--recipe", help="Recipe file")]
SuitesOpt = Annotated[
    list[str] | None, typer.Option("--suite", "-s", help="Repeatable; default all")
]


LITERALS = {"true": True, "false": False, "none": None}


def _params(raw: list[str]) -> dict:
    out = {}
    for p in raw:
        k, v = p.split("=", 1)
        out[k] = LITERALS[v.lower()] if v.lower() in LITERALS else int(v) if v.isdigit() else v
    return out


def _print_summary(summary: dict) -> None:
    table = Table(
        "group",
        "n",
        "acc",
        "wrong",
        "abstain",
        "err",
        "cut",
        "p50 s",
        "p95 s",
        "tok",
        "ret",
        "cite",
    )
    for name, s in summary.items():
        if name.startswith("_"):
            continue
        table.add_row(
            name,
            str(s["n"]),
            f"{s['accuracy']:.1%}",
            str(s["incorrect"]),
            str(s["not_attempted"]),
            str(s["errors"]),
            str(s.get("truncated", 0)),
            f"{s['latency_p50_s']:.1f}",
            f"{s['latency_p95_s']:.1f}",
            f"{s['completion_tokens_mean']:.0f}",
            f"{s['retrieval_hit']:.0%}" if "retrieval_hit" in s else "",
            f"{s['citation_hit']:.0%}" if s.get("citation_hit") is not None else "",
        )
    console.print(table)
    console.print(f"graded by: {summary['_grading']}")


@app.command()
def build(suites: SuitesOpt = None) -> None:
    """Download pinned sources and write data/suites/<suite>.jsonl."""
    for s in suites or datasets.BUILDERS:
        console.print(f"{s}: {datasets.build(s)}")


@app.command()
def run(
    system: str,
    suites: SuitesOpt = None,
    param: Annotated[list[str] | None, typer.Option("--param", "-p", help="key=value")] = None,
    limit: Annotated[int | None, typer.Option(help="First N items per suite")] = None,
    concurrency: int = 16,
    grade: Annotated[
        bool, typer.Option(help="On-box LLM judge (provisional); default judge is external")
    ] = False,
    recipe_path: RecipeOpt = None,
) -> None:
    """Answer every item with SYSTEM, then grade and summarize."""
    r = recipe.load(recipe_path)
    suites = suites or list(datasets.BUILDERS)
    run_dir = runner.run(system, suites, r, _params(param or []), concurrency, limit)
    console.print(f"run: {run_dir}")
    if grade:
        grading.grade(run_dir, r, concurrency)
        _print_summary(report.summarize(run_dir))


@app.command("grade")
def grade_cmd(run_dir: Path, concurrency: int = 16, recipe_path: RecipeOpt = None) -> None:
    """(Re)grade a run: exact/numeric match first, then the LLM judge."""
    grading.grade(run_dir, recipe.load(recipe_path), concurrency)
    _print_summary(report.summarize(run_dir))


@app.command()
def show(run_dir: Path) -> None:
    """Print a graded run's summary."""
    _print_summary(report.summarize(run_dir))


@app.command("compare")
def compare_cmd(a: Path, b: Path, as_json: Annotated[bool, typer.Option("--json")] = False):
    """Paired bootstrap of B vs A per suite (95% CI on the accuracy difference)."""
    result = report.compare(a, b)
    if as_json:
        print(json.dumps(result, indent=2))
        return
    table = Table("suite", "n", "A", "B", "B−A", "95% CI", "+/−", "p50 A→B s")
    for suite, c in result.items():
        lo, hi = c["ci95"]
        table.add_row(
            suite,
            str(c["n"]),
            f"{c['a_accuracy']:.1%}",
            f"{c['b_accuracy']:.1%}",
            f"{c['diff']:+.1%}",
            f"[{lo:+.1%}, {hi:+.1%}]",
            f"+{c['flips']['gained']}/−{c['flips']['lost']}",
            f"{c['a_latency_p50_s']:.1f}→{c['b_latency_p50_s']:.1f}",
        )
    console.print(table)


@app.command("judge-export")
def judge_export(
    runs: list[Path],
    out: Annotated[Path, typer.Option(help="Queue directory")] = Path("data/judge/queue"),
    chunks: int = 6,
) -> None:
    """Blind, shuffled queue of answers the deterministic matcher can't decide, for an external judge."""
    n = grading.export_queue(runs, out, chunks)
    console.print(f"{n} answers to judge → {out}/queue-*.jsonl (rubric: {out}/rubric.md)")


@app.command("judge-import")
def judge_import(
    queue: Path, judge: Annotated[str, typer.Option(help="Judge name to record")]
) -> None:
    """Merge external verdicts (verdicts-*.jsonl) back into each run, then summarize."""
    for run_dir in grading.import_verdicts(queue, judge):
        console.print(f"[bold]{run_dir}[/]")
        _print_summary(report.summarize(run_dir))


@app.command("claims-build")
def claims_build(n_questions: int = 40) -> None:
    """Claim-passage pairs from FinanceBench gold pages (labels by construction)."""
    console.print(claims.build(n_questions))


@app.command("claims-run")
def claims_run(recipe_path: RecipeOpt = None) -> None:
    """Score the decision model on the claim-passage pairs; sweeps the verdict thresholds."""
    import asyncio

    import httpx

    from ttdr.clients import connect
    from ttdr.engine.verify import Verifier, window_chars

    r = recipe.load(recipe_path)

    async def go():
        async with httpx.AsyncClient(timeout=300) as http:
            window = window_chars(r.models.decide.max_state_tokens)
            return await claims.score(Verifier(connect(r, http).decide, window=window))

    console.print_json(json.dumps(asyncio.run(go()), default=str))
