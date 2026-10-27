import asyncio
import dataclasses
import json
import subprocess
from datetime import datetime
from pathlib import Path

import httpx

from ttdr.clients import connect
from ttdr.evals import datasets
from ttdr.evals.schema import Answer, Item
from ttdr.evals.systems import SYSTEMS
from ttdr.recipe import Recipe

RUNS_DIR = Path("runs")


def _git_sha() -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    )
    dirty = subprocess.run(["git", "diff", "--quiet"], check=False).returncode != 0
    return (r.stdout.strip() or "none") + ("-dirty" if dirty else "")


async def _answer_all(system, items: list[Item], recipe: Recipe, concurrency: int, out: Path):
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=1800) as http:
        clients = connect(recipe, http)

        async def one(item: Item) -> None:
            async with sem:
                try:
                    a = await system.answer(item, clients)
                except Exception as e:  # noqa: BLE001 - recorded per item, graded as an error
                    a = Answer(id=item.id, text="", latency_s=0, error=f"{type(e).__name__}: {e}")
            with out.open("a") as f:
                f.write(a.model_dump_json() + "\n")

        await asyncio.gather(*(one(i) for i in items))


def run(
    system_name: str,
    suites: list[str],
    recipe: Recipe,
    params: dict,
    concurrency: int = 16,
    limit: int | None = None,
) -> Path:
    system = SYSTEMS[system_name](**params)
    items = [i for s in suites for i in datasets.load(s)[:limit]]
    run_dir = RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{system_name}"
    run_dir.mkdir(parents=True)
    config = {
        "system": system_name,
        "params": dataclasses.asdict(system),
        "suites": suites,
        "limit": limit,
        "concurrency": concurrency,
        "git": _git_sha(),
        "recipe": recipe.model_dump(),
        "started": datetime.now().isoformat(timespec="seconds"),
    }
    (run_dir / "config.json").write_text(json.dumps(config, indent=2))
    (run_dir / "items.jsonl").write_text("".join(i.model_dump_json() + "\n" for i in items))
    asyncio.run(_answer_all(system, items, recipe, concurrency, run_dir / "answers.jsonl"))
    return run_dir
