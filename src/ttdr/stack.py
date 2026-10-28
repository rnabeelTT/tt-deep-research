"""`ttdr up` / `ttdr down`: the whole stack from one recipe.

Models are served with tt (largest first, so tt assigns chips in a stable order), the data
services and the app run under Docker Compose, then every endpoint is health-checked. What runs
follows the recipe: models its roles use, app services its features enable.
"""

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ttdr import aiq_config, health
from ttdr.recipe import Model, Recipe

MODEL_CACHE = Path("/opt/tenstorrent/models")
TEMPLATE_DIR = "ttdr"
# app service -> health URL (None: checked through the recipe's MCP URL)
APP = {
    "engine": None,
    "aiq": "http://127.0.0.1:8100/health",
    "ui": "http://127.0.0.1:3000",
    "ocr": None,
}


def _run(cmd: list[str], log) -> None:
    log(" ".join(cmd))
    subprocess.run(cmd, check=True)


def serve_command(m: Model) -> list[str]:
    port = str(urlparse(m.url).port)
    extra = ["--profile", m.profile] if m.profile else []
    if m.chat_template:
        extra += ["--chat-template", f"/hf/{TEMPLATE_DIR}/{Path(m.chat_template).name}"]
    return ["tt", "serve", m.bundle, "--yes", "--port", port, *(["--", *extra] if extra else [])]


def app_services(recipe: Recipe) -> list[str]:
    f = recipe.features
    return [s for s in APP if (s != "ui" or f.ui) and (s != "ocr" or f.ocr)]


def serve_models(recipe: Recipe, log) -> None:
    up = {c.name.split(" ")[0]: c.ok for c in asyncio.run(health.run(recipe))}
    for name, m in recipe.served().items():
        if up.get(name):
            log(f"{name}: already serving ({m.bundle})")
            continue
        for snap in m.prefetch:
            repo, rev = snap.split("@")
            hub = str(MODEL_CACHE / "hub")
            _run(["hf", "download", repo, "--revision", rev, "--cache-dir", hub], log)
        if m.chat_template:
            (MODEL_CACHE / TEMPLATE_DIR).mkdir(parents=True, exist_ok=True)
            shutil.copy(m.chat_template, MODEL_CACHE / TEMPLATE_DIR)
        _run(serve_command(m), log)


MCP_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "ttdr-up", "version": "0"},
    },
}


def _up(http: httpx.Client, name: str, url: str) -> bool:
    """The engine counts as up only when an MCP handshake succeeds; other apps on any 2xx/3xx."""
    if name == "engine":
        r = http.post(url, json=MCP_INIT, headers={"accept": "application/json, text/event-stream"})
        return r.status_code == 200
    return http.get(url).status_code < 400


def wait(urls: dict[str, str], timeout_s: float = 600) -> dict[str, bool]:
    deadline, status = time.time() + timeout_s, {}
    with httpx.Client(timeout=5) as http:
        for name, url in urls.items():
            while True:
                try:
                    status[name] = _up(http, name, url)
                except httpx.HTTPError:
                    status[name] = False
                if status[name] or time.time() > deadline:
                    break
                time.sleep(3)
    return status


def compose_env(recipe: Recipe) -> dict[str, str]:
    """Recipe values Compose needs, written to .env (Compose reads it automatically)."""
    env = {
        "AIQ_AUTO_TOOL_CHOICE": "1" if recipe.features.auto_tool_choice else "0",
        "TTDR_UID": str(os.getuid()),
        "TTDR_GID": str(os.getgid()),
    }
    ocr = recipe.role("ocr")
    if not ocr:
        return {**env, "TTDR_OCR_BACKEND": "paddle"}
    return {**env, "TTDR_OCR_BACKEND": "llm", "TTDR_OCR_URL": ocr.url, "TTDR_OCR_MODEL": ocr.model}


def up(recipe: Recipe, build: bool, log) -> dict[str, bool]:
    serve_models(recipe, log)
    if not Path("external/aiq").exists():
        _run(["scripts/setup-aiq.sh"], log)
    log(f"rendered {aiq_config.write(recipe)}")
    Path(".env").write_text("".join(f"{k}={v}\n" for k, v in compose_env(recipe).items()))
    for d in ("data/corpus", "data/aiq/app"):  # else Docker creates them as root
        Path(d).mkdir(parents=True, exist_ok=True)
    services = app_services(recipe)
    flags = ["--build"] if build else []
    _run(["docker", "compose", "--profile", "app", "up", "-d", *flags, *services], log)
    urls = {"engine": recipe.services.mcp}
    urls |= {s: APP[s] for s in services if APP[s]}
    return wait(urls)


def down(stop_models: bool, recipe: Recipe, log) -> None:
    """Stop the app containers (data services and Redis' caches keep running)."""
    _run(["docker", "compose", "--profile", "app", "rm", "--stop", "--force", *APP], log)
    if stop_models:
        for m in recipe.served().values():
            _run(["tt", "model", "stop", m.bundle], log)
