"""`ttdr research`: submit a question to AI-Q's job API, show live progress, print the report."""

import json
import time
from collections import Counter
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown

console = Console()
DONE = {"success", "failure", "interrupted", "not_found"}
SOURCE_TOOLS = {"ttdr__search", "ttdr__fetch_page", "ttdr__get_evidence"}


def _events(http: httpx.Client, api: str, job: str):
    """Server-sent events for a job, resuming from the last event ID if the stream drops."""
    last = None
    while True:
        url = f"{api}/v1/jobs/async/job/{job}/stream" + (f"/{last}" if last else "")
        event: dict = {}
        with http.stream("GET", url, timeout=None) as r:
            for line in r.iter_lines():
                if line.startswith("id:"):
                    event["id"] = line[3:].strip()
                elif line.startswith("event:"):
                    event["event"] = line[6:].strip()
                elif line.startswith("data:"):
                    event["data"] = json.loads(line[5:].strip() or "{}")
                elif not line and event:
                    last = event.get("id", last)
                    yield event
                    event = {}
        status = http.get(f"{api}/v1/jobs/async/job/{job}").json()["status"]
        if status in DONE:
            return


def _describe(e: dict) -> str | None:
    data = e.get("data", {})
    kind = e.get("event")
    if kind == "workflow.start" and data.get("name", "").endswith("agent"):
        return f"[bold]{data['name']}[/]"
    if kind == "tool.start" and data.get("name") in SOURCE_TOOLS:
        args = data.get("data", {}).get("input", {})
        target = args.get("query") or args.get("url") or ", ".join(args.get("ids", []))
        return f"  {data['name'].removeprefix('ttdr__')}: {str(target)[:100]}"
    if kind == "job.status":
        return f"[dim]status {data.get('status')}[/]"
    return None


def _research_deepagents(question: str, shallow: bool, out: Path | None, quiet: bool) -> None:
    import asyncio

    from ttdr import recipe as recipes
    from ttdr.harness import DeepAgentsHarness

    h = DeepAgentsHarness(recipes.load())
    if shallow:
        h.max_subquestions, h.max_tool_calls = 1, 4
    result = asyncio.run(h.run(question, log=(lambda _: None) if quiet else console.print))
    console.rule()
    console.print(Markdown(result.report))
    console.rule()
    if out:
        out.write_text(result.report)
    console.print(", ".join(f"{k} {v:,}" for k, v in result.stats.items()))


def research(
    question: str,
    shallow: Annotated[bool, typer.Option(help="Quick cited answer instead of a report")] = False,
    harness: Annotated[
        str, typer.Option(help="aiq (job API) or lite (built-in, over MCP)")
    ] = "aiq",
    api: Annotated[str, typer.Option(help="AI-Q job API")] = "http://127.0.0.1:8100",
    out: Annotated[Path | None, typer.Option(help="Write the report (Markdown) here")] = None,
    quiet: Annotated[bool, typer.Option(help="Hide live progress")] = False,
) -> None:
    """Research a question on the QB2 and print the cited report."""
    if harness == "deepagents":
        _research_deepagents(question, shallow, out, quiet)
        return
    agent = "shallow_researcher" if shallow else "deep_researcher"
    start = time.perf_counter()
    stats: Counter = Counter()
    with httpx.Client(timeout=60) as http:
        r = http.post(
            f"{api}/v1/jobs/async/submit",
            json={"agent_type": agent, "input": question, "data_sources": ["ttdr"]},
        )
        r.raise_for_status()
        job = r.json()["job_id"]
        console.print(f"[dim]job {job} ({agent})[/]")
        for e in _events(http, api, job):
            kind, data = e.get("event"), e.get("data", {})
            if kind == "llm.end":
                usage = data.get("metadata", {}).get("usage", {})
                stats["llm calls"] += 1
                stats["input tokens"] += usage.get("input_tokens", 0)
                stats["output tokens"] += usage.get("output_tokens", 0)
            elif kind == "tool.start" and data.get("name") in SOURCE_TOOLS:
                stats[data["name"].removeprefix("ttdr__")] += 1
            if not quiet and (line := _describe(e)):
                console.print(line)
        status = http.get(f"{api}/v1/jobs/async/job/{job}").json()["status"]
        report = http.get(f"{api}/v1/jobs/async/job/{job}/report").json().get("report") or ""
    console.rule()
    console.print(Markdown(report) if report else f"[red]no report (job {status})[/]")
    console.rule()
    if out and report:
        out.write_text(report)
    summary = ", ".join(f"{k} {v:,}" for k, v in stats.items())
    console.print(f"[bold]{status}[/] in {time.perf_counter() - start:.0f}s · {summary}")
    raise typer.Exit(0 if status == "success" else 1)


JOB_STORES = (Path("data/aiq/app/jobs.db"), Path("data/aiq/jobs.db"))  # container, host nat serve


def jobs(
    limit: int = 15,
    db: Annotated[Path | None, typer.Option(help="AI-Q job store")] = None,
) -> None:
    """Recent AI-Q jobs with live progress: agents done, searches, fetches, last activity."""
    import sqlite3
    from datetime import UTC, datetime

    from rich.table import Table

    db = db or next((p for p in JOB_STORES if p.exists()), JOB_STORES[0])
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT i.job_id, a.agent_type, i.status, i.created_at FROM job_info i "
        "LEFT JOIN job_access a USING (job_id) ORDER BY i.created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    table = Table("job", "agent", "status", "started", "agents", "search", "fetch", "idle")
    now = datetime.now(UTC).replace(tzinfo=None)
    for job, agent, status, created in rows:
        ev = con.execute(
            "SELECT event_type, event_data, created_at FROM job_events WHERE job_id = ? "
            "AND event_type IN ('workflow.start','workflow.end','tool.start','llm.end')",
            (job,),
        ).fetchall()
        tools = Counter(json.loads(d).get("name", "") for t, d, _ in ev if t == "tool.start")
        started = sum(t == "workflow.start" for t, _, _ in ev)
        done = sum(t == "workflow.end" for t, _, _ in ev)
        last = max((c for _, _, c in ev), default=None)
        idle = (
            ""
            if not last or status != "running"
            else f"{(now - datetime.fromisoformat(last)).seconds // 60} min"
        )
        table.add_row(
            job[:8],
            (agent or "").replace("_researcher", ""),
            status,
            created[5:16],
            f"{done}/{started}",
            str(tools["ttdr__search"]),
            str(tools["ttdr__fetch_page"]),
            idle,
        )
    console.print(table)
    console.print(f"[dim]{db} · idle = minutes since the last model call or tool call[/]")


def verify(
    report: Path,
    out: Annotated[
        Path | None, typer.Option(help="Write the badged (or revised) report here")
    ] = None,
    revise_weak: Annotated[bool, typer.Option("--revise", help="Rewrite weak claims once")] = False,
    recipe_path: Annotated[Path | None, typer.Option("--recipe", help="Recipe file")] = None,
) -> None:
    """Check every cited claim in a report against its sources with the decision model."""
    import asyncio

    from ttdr import recipe as recipes
    from ttdr.engine import Engine, review
    from ttdr.engine.verify import from_recipe

    r = recipes.load(recipe_path)
    text = report.read_text()

    async def go():
        engine = Engine.open(r)
        verifier = from_recipe(r, engine.clients.decide)
        checked = await review.check(engine, verifier, text)
        result = {"before": review.summary(checked)}
        final = review.annotate(text, checked)
        if revise_weak:
            revised = await review.revise(engine.clients.llm, text, checked)
            rechecked = await review.check(engine, verifier, revised)
            result["after"] = review.summary(rechecked)
            final = review.annotate(revised, rechecked)
        return checked, result, final

    checked, result, final = asyncio.run(go())
    for c in checked:
        label = c.verdict.label if c.verdict else "unreachable"
        console.print(f"{review.BADGE.get(label, '?')} [dim]{label:16s}[/] {c.claim.text[:110]}")
    console.print_json(json.dumps(result))
    if out:
        out.write_text(final)
