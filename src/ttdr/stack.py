"""`ttdr up` / `ttdr down`: the whole stack from one recipe.

Models are served with tt (largest first, so tt assigns chips in a stable order), the data
services and the app run under Docker Compose, then every endpoint is health-checked.
"""

import asyncio
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ttdr import health
from ttdr.recipe import Model, Recipe

MODEL_CACHE = Path("/opt/tenstorrent/models")
TEMPLATE_DIR = "ttdr"
APP = {"engine": None, "aiq": "http://127.0.0.1:8100/health", "ui": "http://127.0.0.1:3000"}
APP_SERVICES = ["engine", "aiq", "ui", "ocr"]


def _run(cmd: list[str], log) -> None:
    log(" ".join(cmd))
    subprocess.run(cmd, check=True)


def serve_command(m: Model) -> list[str]:
    port = str(urlparse(m.url).port)
    extra = ["--profile", m.profile] if m.profile else []
    if m.chat_template:
        extra += ["--chat-template", f"/hf/{TEMPLATE_DIR}/{Path(m.chat_template).name}"]
    return ["tt", "serve", m.bundle, "--yes", "--port", port, *(["--", *extra] if extra else [])]


def serve_models(recipe: Recipe, log) -> None:
    up = {c.name: c.ok for c in asyncio.run(health.run(recipe))}
    models = {
        "llm": recipe.models.llm,
        "embed": recipe.models.embed,
        "decide": recipe.models.decide,
    }
    for name, m in sorted(models.items(), key=lambda kv: kv[1].serve_order):
        if up.get(name):
            log(f"{name}: already serving ({m.bundle})")
            continue
        for snap in m.prefetch:
            repo, rev = snap.split("@")
            _run(
                [
                    "hf",
                    "download",
                    repo,
                    "--revision",
                    rev,
                    "--cache-dir",
                    str(MODEL_CACHE / "hub"),
                ],
                log,
            )
        if m.chat_template:
            (MODEL_CACHE / TEMPLATE_DIR).mkdir(parents=True, exist_ok=True)
            shutil.copy(m.chat_template, MODEL_CACHE / TEMPLATE_DIR)
        _run(serve_command(m), log)


def wait(urls: dict[str, str], timeout_s: float = 600) -> dict[str, bool]:
    deadline, status = time.time() + timeout_s, {}
    with httpx.Client(timeout=5) as http:
        for name, url in urls.items():
            while True:
                try:
                    status[name] = http.get(url).status_code < 500
                except httpx.HTTPError:
                    status[name] = False
                if status[name] or time.time() > deadline:
                    break
                time.sleep(3)
    return status


def up(recipe: Recipe, build: bool, log) -> dict[str, bool]:
    serve_models(recipe, log)
    if not Path("external/aiq").exists():
        _run(["scripts/setup-aiq.sh"], log)
    Path("data/corpus").mkdir(parents=True, exist_ok=True)  # else Docker creates it as root
    _run(
        ["docker", "compose", "--profile", "app", "up", "-d", *(["--build"] if build else [])], log
    )
    return wait({"engine": recipe.services.mcp, **{k: v for k, v in APP.items() if v}})


def down(stop_models: bool, recipe: Recipe, log) -> None:
    """Stop the app containers; with stop_models, the models too."""
    # Only the app: the data services (and Redis' caches) keep running.
    _run(["docker", "compose", "--profile", "app", "rm", "--stop", "--force", *APP_SERVICES], log)
    if stop_models:
        for m in {
            recipe.models.llm.bundle,
            recipe.models.embed.bundle,
            recipe.models.decide.bundle,
        }:
            _run(["tt", "model", "stop", m], log)
