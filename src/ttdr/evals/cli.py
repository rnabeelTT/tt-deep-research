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


@app.command("retry")
def retry_cmd(run_dir: Path, concurrency: int = 8) -> None:
    """Re-answer only the items of RUN_DIR that errored (same system, params and recipe)."""
    console.print(f"recovered {runner.retry(run_dir, concurrency)} answers in {run_dir}")


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
    from ttdr.engine.verify import from_recipe

    r = recipe.load(recipe_path)

    async def go():
        async with httpx.AsyncClient(timeout=300) as http:
            return await claims.score(from_recipe(r, connect(r, http).decide))

    console.print_json(json.dumps(asyncio.run(go()), default=str))


@app.command("ab")
def ab(
    system: str,
    recipe_a: Annotated[Path, typer.Option("--a", help="Recipe for variant A (current best)")],
    recipe_b: Annotated[Path, typer.Option("--b", help="Recipe for variant B (candidate)")],
    suites: SuitesOpt = None,
    param: Annotated[list[str] | None, typer.Option("--param", "-p", help="key=value")] = None,
    limit: Annotated[int | None, typer.Option(help="First N items per suite")] = None,
    concurrency: int = 16,
    chunks: int = 6,
) -> None:
    """Run SYSTEM under two recipes on the same items, then build one blinded judge queue.

    Both recipes' endpoints must be up. For two models that share chips, run each variant
    with `ttdr eval run` and swap models in between, then `judge-export` both runs.
    """
    from datetime import datetime

    suites = suites or list(datasets.BUILDERS)
    params = _params(param or [])
    runs = [
        runner.run(system, suites, recipe.load(p), params, concurrency, limit)
        for p in (recipe_a, recipe_b)
    ]
    queue = Path(f"data/judge/ab-{datetime.now():%Y%m%d-%H%M%S}")
    n = grading.export_queue(runs, queue, chunks)
    console.print(f"A: {runs[0]}\nB: {runs[1]}\n{n} answers to judge → {queue}")
    console.print(
        "Next: grade each queue-*.jsonl with a Claude subagent (verdicts-*.jsonl), then\n"
        f"  ttdr eval judge-import {queue} --judge <judge>\n"
        f"  ttdr eval compare {runs[0]} {runs[1]}"
    )


@app.command("check")
def check(
    run_dir: Path,
    minimum: Annotated[
        list[str], typer.Option("--min", help="suite.metric=value, e.g. financebench.accuracy=0.6")
    ],
) -> None:
    """Exit 1 if any summary metric is below its floor (CI guardrail)."""
    summary = json.loads((run_dir / "summary.json").read_text())
    failed = False
    for rule in minimum:
        key, floor = rule.split("=")
        group, metric = key.rsplit(".", 1)
        value = summary.get(group, {}).get(metric)
        ok = value is not None and value >= float(floor)
        failed |= not ok
        console.print(f"{'[green]ok[/]' if ok else '[red]FAIL[/]'} {key} = {value} (min {floor})")
    raise typer.Exit(1 if failed else 0)


@app.command("verify-run")
def verify_run(
    run_dir: Path,
    revise: Annotated[bool, typer.Option(help="Also revise weak claims and re-check")] = False,
    limit: Annotated[int | None, typer.Option(help="First N answers")] = None,
    concurrency: int = 4,
    recipe_path: RecipeOpt = None,
) -> None:
    """Verify every cited claim in a run's answers; with --revise, write <run>-revised to grade."""
    import asyncio
    import shutil

    from ttdr.engine import Engine, review
    from ttdr.engine.verify import from_recipe
    from ttdr.evals.schema import Answer

    r = recipe.load(recipe_path)
    lines = (run_dir / "answers.jsonl").read_text().splitlines()
    answers = [Answer.model_validate_json(x) for x in lines][:limit]
    out_dir = run_dir.with_name(run_dir.name + "-revised")

    async def go():
        engine = Engine.open(r)
        verifier = from_recipe(r, engine.clients.decide)
        sem = asyncio.Semaphore(concurrency)

        async def one(a: Answer):
            async with sem:
                checked = await review.check(engine, verifier, a.text)
                row = {"id": a.id, "before": review.summary(checked)}
                if revise:
                    text = await review.revise(engine.clients.llm, a.text, checked)
                    row["after"] = review.summary(await review.check(engine, verifier, text))
                    row["text"] = text
                return row

        return await asyncio.gather(*(one(a) for a in answers if not a.error))

    rows = asyncio.run(go())

    def pooled(key):
        s = [x[key] for x in rows if x.get(key)]
        claims = sum(x["claims"] for x in s)
        tally = {k: sum(x[k] for x in s) for k in (*review.BADGE, "unreachable")}
        return {
            "claims": claims,
            **tally,
            "validity": round(tally["supported"] / claims, 3) if claims else None,
        }

    result = {"answers": len(rows), "before": pooled("before")}
    if revise:
        result["after"] = pooled("after")
        shutil.copytree(run_dir, out_dir, dirs_exist_ok=True)
        for f in ("grades.jsonl", "summary.json", "judge.json"):
            (out_dir / f).unlink(missing_ok=True)
        texts = {x["id"]: x["text"] for x in rows}
        keep = {x["id"] for x in rows}
        items = [
            x
            for x in (out_dir / "items.jsonl").read_text().splitlines()
            if json.loads(x)["id"] in keep
        ]
        (out_dir / "items.jsonl").write_text("\n".join(items) + "\n")
        revised = [a.model_copy(update={"text": texts[a.id]}) for a in answers if a.id in texts]
        (out_dir / "answers.jsonl").write_text("".join(a.model_dump_json() + "\n" for a in revised))
        result["revised_run"] = str(out_dir)
    (run_dir / "verify.json").write_text(
        json.dumps(
            {
                "summary": result,
                "answers": [{k: v for k, v in x.items() if k != "text"} for x in rows],
            },
            indent=2,
        )
    )
    console.print_json(json.dumps(result))
