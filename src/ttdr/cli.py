import asyncio
import getpass
import json
import socket
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ttdr import health, home, recipe
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
def main(ctx: typer.Context) -> None:
    """Deep research on a Tenstorrent QB2."""
    home.enter(refresh=ctx.invoked_subcommand in ("up", "recipes"))


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


@app.command("ui-server")
def ui_server_cmd(
    recipe_path: Annotated[Path | None, typer.Option("--recipe", help="Recipe file")] = None,
    host: Annotated[str, typer.Option(help="Bind address")] = "127.0.0.1",
    port: Annotated[int | None, typer.Option(help="Port [default: as ttdr up placed it]")] = None,
):
    """Serve AI-Q's web UI protocol (chat websocket, job SSE) on the Deep Agents harness."""
    from ttdr import ports, ui_server

    ui_server.serve(recipe.load(recipe_path), host, port or ports.port("api"))


@app.command("up")
def up_cmd(
    recipe_path: Annotated[
        Path | None, typer.Option("--recipe", help="Recipe name (`ttdr recipes`) or file")
    ] = None,
    build: Annotated[bool, typer.Option(help="Rebuild the app images")] = False,
    aiq: Annotated[
        bool, typer.Option("--aiq", help="Also run AI-Q's harness: UI :3001, job API :8100")
    ] = False,
) -> None:
    """Serve the recipe's models, start data services and the app, and check every endpoint."""
    from ttdr import progress, stack

    r = recipe.load(recipe_path, moved=False)
    if aiq:
        r = r.model_copy(update={"features": r.features.model_copy(update={"aiq": True})})
    log = lambda m: progress.console.print(f"[dim]$ {m}[/]")
    status, chosen = stack.up(r, build, log, recipe.resolve(recipe_path))
    if not all(status.values()):
        down = ", ".join(name for name, ok in status.items() if not ok)
        progress.pointer(f"logs: docker logs ttdr-<service>-1 (for {down})")
        raise typer.Exit(1)
    ui = chosen["ui"]
    progress.pointer(f"UI: http://localhost:{ui}")
    if r.features.aiq:
        progress.pointer(f"AI-Q UI: http://localhost:{chosen['aiq-ui']}")
    here = f"{getpass.getuser()}@{socket.gethostname()}"
    progress.pointer(f"from another machine: ssh -L {ui}:localhost:{ui} {here}, then the same URL")
    progress.pointer('CLI: ttdr research "…" · ttdr jobs')


def _chips(chips: str) -> str:
    return f"chips {chips}" if "-" in chips or "," in chips else f"chip {chips}"


@app.command("recipes")
def recipes_cmd(
    show_all: Annotated[
        bool, typer.Option("--all", help="Also recipes without a description")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """The recipes `ttdr up --recipe <name>` can start: what each serves, and on which chips."""
    recipes = recipe.catalog()
    if as_json:
        print(json.dumps(recipes, indent=2))
        return
    table = Table("recipe", "serves", "about")
    table.columns[0].no_wrap = True  # never cut a name short
    for r in recipes:
        if not (show_all or r["description"]):
            continue
        hosted = {m["cohosted_with"] for m in r["models"]} - {None}
        serves = [
            m["model"].split("/")[-1]
            + ("" if hosted or not m["chips"] else f" ({_chips(m['chips'])})")
            for m in r["models"]
        ]
        if hosted:
            host = next(m for m in r["models"] if m["name"] in hosted)
            serves.insert(0, f"one bundle on {_chips(host['chips'])}:")
        table.add_row(
            r["name"] + (" (default)" if r["default"] else ""),
            "\n".join(serves),
            r["description"] or "",
        )
    Console().print(table)


@app.command("aiq-config")
def aiq_config_cmd(
    recipe_path: Annotated[Path | None, typer.Option("--recipe", help="Recipe file")] = None,
) -> None:
    """Render AI-Q's config from the recipe (data/aiq/config/ttdr.yml)."""
    from ttdr import aiq_config

    Console().print(aiq_config.write(recipe.load(recipe_path)))


@app.command("down")
def down_cmd(
    keep_models: Annotated[
        bool, typer.Option("--keep-models", help="Leave the models serving")
    ] = False,
    recipe_path: Annotated[Path | None, typer.Option("--recipe", help="Recipe file")] = None,
) -> None:
    """Stop the app containers and the recipe's models; the data is kept."""
    from ttdr import stack

    stack.down(recipe.load(recipe_path), keep_models)


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
