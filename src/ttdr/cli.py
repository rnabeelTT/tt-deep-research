import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ttdr import health, recipe

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Deep research on a Tenstorrent QB2."""


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
