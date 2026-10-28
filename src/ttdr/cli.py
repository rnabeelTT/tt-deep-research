import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ttdr import health, recipe
from ttdr.bench_cli import app as bench_app
from ttdr.corpus.cli import app as corpus_app
from ttdr.evals.cli import app as eval_app
from ttdr.research_cli import jobs, research, verify

app = typer.Typer(no_args_is_help=True)
app.add_typer(eval_app, name="eval")
app.add_typer(bench_app, name="bench")
app.add_typer(corpus_app, name="corpus")
app.command()(research)
app.command()(jobs)
app.command()(verify)


@app.callback()
def main() -> None:
    """Deep research on a Tenstorrent QB2."""


@app.command("mcp")
def mcp_cmd(
    recipe_path: Annotated[Path | None, typer.Option("--recipe", help="Recipe file")] = None,
    host: Annotated[
        str | None,
        typer.Option(envvar="TTDR_MCP_HOST", help="Bind address (default: the recipe's MCP host)"),
    ] = None,
):
    """Serve the engine's MCP tools over streamable HTTP. Beyond localhost, set TTDR_MCP_TOKEN."""
    from urllib.parse import urlparse

    from ttdr import mcp_server

    r = recipe.load(recipe_path)
    u = urlparse(r.services.mcp)
    mcp_server.serve(r, host or u.hostname, u.port, u.path)


@app.command("up")
def up_cmd(
    recipe_path: Annotated[Path | None, typer.Option("--recipe", help="Recipe file")] = None,
    build: Annotated[bool, typer.Option(help="Rebuild the app images")] = False,
) -> None:
    """Serve the recipe's models, start data services and the app, and check every endpoint."""
    from ttdr import stack

    console = Console()
    status = stack.up(recipe.load(recipe_path), build, lambda m: console.print(f"[dim]$ {m}[/]"))
    for name, ok in status.items():
        console.print(f"{'[green]up[/]' if ok else '[red]down[/]'} {name}")
    console.print('UI http://<host>:3000 · jobs `ttdr jobs` · CLI `ttdr research "..."`')
    raise typer.Exit(0 if all(status.values()) else 1)


@app.command("aiq-config")
def aiq_config_cmd(
    recipe_path: Annotated[Path | None, typer.Option("--recipe", help="Recipe file")] = None,
) -> None:
    """Render AI-Q's config from the recipe (data/aiq/config/ttdr.yml)."""
    from ttdr import aiq_config

    Console().print(aiq_config.write(recipe.load(recipe_path)))


@app.command("down")
def down_cmd(
    models: Annotated[bool, typer.Option("--models", help="Also stop the models")] = False,
    recipe_path: Annotated[Path | None, typer.Option("--recipe", help="Recipe file")] = None,
) -> None:
    """Stop the app containers (and, with --models, the models)."""
    from ttdr import stack

    console = Console()
    stack.down(models, recipe.load(recipe_path), lambda m: console.print(f"[dim]$ {m}[/]"))


@app.command("health")
def health_cmd(
    recipe_path: Annotated[Path | None, typer.Option("--recipe", help="Recipe file")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Probe every model and service in the recipe."""
    checks = asyncio.run(health.run(recipe.load(recipe_path)))
    if as_json:
        print(json.dumps([asdict(c) for c in checks], indent=2))
    else:
        table = Table("endpoint", "status", "ms", "detail")
        for c in checks:
            status = "[green]up[/]" if c.ok else "[red]down[/]"
            table.add_row(c.name, status, f"{c.ms:.0f}", c.detail)
        Console().print(table)
    raise typer.Exit(0 if all(c.ok for c in checks) else 1)
