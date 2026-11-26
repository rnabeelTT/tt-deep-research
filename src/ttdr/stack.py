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

TEMPLATE_DIR = "ttdr"
# app service -> health URL (None: checked through the recipe's MCP URL)
APP = {
    "engine": None,
    "aiq": "http://127.0.0.1:8100/health",
    "ui": "http://127.0.0.1:3001",
    "deepagents": "http://127.0.0.1:8200/health",
    "ui-deepagents": "http://127.0.0.1:3000",
    "ocr": None,
    "pyrun": None,  # sandboxed Python: up when the recipe sets services.code
}
UI_SERVICES = {"ui", "deepagents", "ui-deepagents"}  # the two web UIs and Deep Agents' API for one
AIQ_SERVICES = {"aiq", "ui"}  # AI-Q's harness: opt in with features.aiq
COHOST_TT_MODEL = (
    "git+https://github.com/rnabeelTT/tt-kernel-package-manager@raahem/cohost-mixed-groups"
)
INSTALL = {
    "docker": "install Docker Engine with the Compose plugin (https://docs.docker.com/engine/install/)",
    "git": "install git (needed once to clone AI-Q into external/aiq)",
    "tt": "install the Tenstorrent CLI, tt >= 1.2.1 (`tt self update --yes` if already installed)",
    "tt-model": f"cohost bundles need tt-model with mixed cohost groups: "
    f"`uv tool install --force {COHOST_TT_MODEL}`",
    "hf": "`uv tool install huggingface_hub` (for the recipe's prefetch snapshots)",
}


def model_cache() -> Path:
    """tt's Hugging Face cache, which `tt serve` mounts into model containers at /hf: chat
    templates and prefetch snapshots are staged there."""
    return Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")


def missing_tools(recipe: Recipe, up: dict[str, bool] | None = None) -> list[str]:
    """Commands `ttdr up` will call that aren't on PATH: models already serving need none."""
    up = up or {}
    served = recipe.served()
    cohosted = {c for m in served.values() for c in m.cohosts}
    need = {"docker"} | (set() if Path("external/aiq").exists() else {"git"})
    for name, m in served.items():
        if up.get(name) or name in cohosted or not m.bundle:
            continue
        need.add("tt-model" if m.cohosts else "tt")
        if m.prefetch:
            need.add("hf")
    missing = {t for t in need if not shutil.which(t)}
    compose = ["docker", "compose", "version"]  # the Compose plugin too
    if (
        "docker" not in missing
        and subprocess.run(compose, check=False, capture_output=True).returncode
    ):
        missing.add("docker")
    return sorted(missing)


def access_problems(recipe: Recipe, up: dict[str, bool] | None = None) -> list[str]:
    """Permissions `ttdr up` needs: the Docker daemon without sudo, and a writable model cache
    when a model to serve has a chat template or prefetch snapshots to stage there."""
    up, problems = up or {}, []
    if (
        shutil.which("docker")
        and subprocess.run(["docker", "info"], check=False, capture_output=True).returncode
    ):
        problems.append(
            "the Docker daemon isn't reachable as this user: `sudo usermod -aG docker $USER`, "
            "then log in again (or start Docker: `sudo systemctl start docker`)"
        )
    staged = [
        m
        for n, m in recipe.served().items()
        if not up.get(n) and not m.cohosts and (m.chat_template or m.prefetch)
    ]
    cache = model_cache()
    existing = next(d for d in (cache, *cache.parents) if d.exists())
    if staged and not os.access(existing, os.W_OK):
        problems.append(
            f"{cache} isn't writable (chat templates are staged there, in tt's model cache): "
            "make it yours, or point HF_HOME at a folder you can write"
        )
    return problems


def preflight(recipe: Recipe, up: dict[str, bool]) -> None:
    lines = [f"  {t}: {INSTALL[t]}" for t in missing_tools(recipe, up)]
    lines += [f"  {p}" for p in access_problems(recipe, up)]
    if lines:
        raise SystemExit("ttdr up can't start yet:\n" + "\n".join(lines))


def _run(cmd: list[str], log) -> None:
    log(" ".join(cmd))
    subprocess.run(cmd, check=True)


def device_ids(chips: str) -> str:
    """tt-model's --device-id from a recipe's chips: "0-1" -> "0,1", "2" -> "2"."""
    ids: list[int] = []
    for part in chips.split(","):
        first, _, last = part.partition("-")
        ids += range(int(first), int(last or first) + 1)
    return ",".join(map(str, ids))


def serve_command(m: Model) -> list[str]:
    port = str(urlparse(m.url).port)
    pin = ["--device-id", device_ids(m.chips)] if m.chips else []
    profile = ["--profile", m.profile] if m.profile else []
    if m.cohosts:  # `tt serve` refuses cohost bundles
        return ["tt-model", "serve", m.bundle, "--port", port, "--detach", *pin, *profile]
    extra = [*pin, *profile]
    if m.chat_template:
        extra += ["--chat-template", f"/hf/{TEMPLATE_DIR}/{Path(m.chat_template).name}"]
    extra += m.serve_args
    # --detach: back once the container holds its chips; serve_models waits for the models.
    tail = ["--", *extra] if extra else []
    return ["tt", "serve", m.bundle, "--yes", "--port", port, "--detach", *tail]


def app_services(recipe: Recipe) -> list[str]:
    f = recipe.features
    return [
        s
        for s in APP
        if (s not in UI_SERVICES or f.ui)
        and (s != "ocr" or f.ocr)
        and (s != "pyrun" or recipe.services.code)
        and (s not in AIQ_SERVICES or f.aiq)
    ]


def serve_models(recipe: Recipe, log, timeout_s: float = 1800) -> None:
    up = {c.name.split(" ")[0]: c.ok for c in asyncio.run(health.run(recipe))}
    preflight(recipe, up)
    served = recipe.served()
    cohosted = {c for m in served.values() for c in m.cohosts}
    todo = []
    for name, m in served.items():
        if up.get(name):
            log(f"{name}: already serving ({m.bundle or m.url})")
        elif name in cohosted:
            continue
        elif not m.bundle:
            log(f"{name}: no bundle, not served by tt; start it at {m.url}")
        else:
            todo.append((name, m))
    for _, m in todo:
        for snap in m.prefetch:
            repo, rev = snap.split("@")
            hub = str(model_cache() / "hub")
            _run(["hf", "download", repo, "--revision", rev, "--cache-dir", hub], log)
        if m.chat_template:
            (model_cache() / TEMPLATE_DIR).mkdir(parents=True, exist_ok=True)
            shutil.copy(m.chat_template, model_cache() / TEMPLATE_DIR)
    starts = {}
    if all(m.chips for _, m in todo):
        starts = _start_together(todo)
    else:  # tt picks the chips: one model at a time, largest first, for a stable order
        for _, m in todo:
            _run(serve_command(m), log)
    if todo:
        _watch(recipe, todo, starts, timeout_s)


def _start_together(todo: list[tuple[str, Model]]) -> dict[str, tuple[subprocess.Popen, Path]]:
    """Start models pinned to their own chips all at once, so their downloads and loads
    overlap: {model: (its tt serve, its log in data/logs/)}."""
    if any(not m.cohosts for _, m in todo):
        # `tt serve` installs tt-model on first use; do that once, not in every serve at once.
        subprocess.run(["tt", "model", "package", "--help"], check=True, capture_output=True)
    logs = Path("data/logs")
    logs.mkdir(parents=True, exist_ok=True)
    starts = {}
    for name, m in todo:
        path = logs / f"serve-{name}.log"
        with path.open("w") as out:
            starts[name] = (subprocess.Popen(serve_command(m), stdout=out, stderr=out), path)
    return starts


STAGES = {
    "starting": "downloading and starting",
    "loading": "loading (10-15 min from cold)",
    "up": "up",
}


def _watch(recipe: Recipe, todo, starts, timeout_s: float) -> None:
    """A live row per model until each answers: starting (downloading, starting its
    container), loading, up. A start that fails stops the wait and prints its log's end."""
    from rich.console import Console
    from rich.live import Live
    from rich.spinner import Spinner
    from rich.table import Table

    began, checked = time.time(), 0.0
    state = {name: "starting" if name in starts else "loading" for name, _ in todo}

    def view() -> Table:
        minutes, seconds = divmod(int(time.time() - began), 60)
        rows = Table.grid(padding=(0, 2))
        for name, m in todo:
            stage = state[name]
            mark = {"up": "[green]✓[/]", "failed": "[red]✗[/]"}.get(stage) or Spinner("dots")
            label = f"[red]failed: {starts[name][1]}[/]" if stage == "failed" else STAGES[stage]
            clock = "" if stage in ("up", "failed") else f"[dim]{minutes}:{seconds:02d}[/]"
            chips = f"[dim]chips {m.chips}[/]" if m.chips else ""
            rows.add_row(mark, m.model.split("/")[-1], chips, label, clock)
        return rows

    with Live(view(), console=Console(), refresh_per_second=4) as live:
        while set(state.values()) - {"up", "failed"} and time.time() - began < timeout_s:
            for name, (proc, _) in starts.items():
                if state[name] == "starting" and proc.poll() is not None:
                    state[name] = "loading" if proc.returncode == 0 else "failed"
            if "failed" in state.values():
                break
            if time.time() - checked > 10:
                checked = time.time()
                answering = {c.name.split(" ")[0] for c in asyncio.run(health.run(recipe)) if c.ok}
                state.update({name: "up" for name in state if name in answering})
            live.update(view())
            time.sleep(1)
        live.update(view())
    failed = [name for name, stage in state.items() if stage == "failed"]
    if failed:
        tails = "\n".join(
            f"{name} ({starts[name][1]}):\n" + "\n".join(starts[name][1].read_text().splitlines()[-15:])
            for name in failed
        )
        raise SystemExit(f"tt could not start {', '.join(failed)}:\n{tails}")
    pending = [m.bundle for name, m in todo if state[name] != "up"]
    if pending:
        logs = "; ".join(f"`tt model logs {b} --follow`" for b in pending)
        raise TimeoutError(f"models not ready after {timeout_s:.0f}s; see {logs}")


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


def compose_env(recipe: Recipe, path: Path | None = None) -> dict[str, str]:
    """Recipe values Compose needs, written to .env (Compose reads it automatically). The
    engine loads the same recipe file, overlays included. Page links in citations use
    TTDR_PAGES_URL from the environment: the UI's address as browsers reach it, plus /pages."""
    mcp = urlparse(recipe.services.mcp)
    env = {
        "TTDR_RECIPE": str(path or "recipe.toml"),
        "TTDR_ENGINE_URL": f"{mcp.scheme}://{mcp.netloc}",
        "TTDR_PAGES_URL": os.environ.get("TTDR_PAGES_URL", "http://localhost:3000/pages"),
        "AIQ_AUTO_TOOL_CHOICE": "1" if recipe.features.auto_tool_choice else "0",
        "TTDR_UID": str(os.getuid()),
        "TTDR_GID": str(os.getgid()),
    }
    ocr = recipe.role("ocr")
    if not ocr:
        return {**env, "TTDR_OCR_BACKEND": "paddle"}
    return {**env, "TTDR_OCR_BACKEND": "llm", "TTDR_OCR_URL": ocr.url, "TTDR_OCR_MODEL": ocr.model}


def write_env(values: dict[str, str], env: Path = Path(".env")) -> None:
    """Update the recipe's keys in .env, keeping lines it doesn't own (API keys added by hand)."""
    kept = [
        line
        for line in (env.read_text().splitlines() if env.exists() else [])
        if line.split("=", 1)[0].strip() not in values
    ]
    env.write_text(
        "".join(f"{line}\n" for line in kept) + "".join(f"{k}={v}\n" for k, v in values.items())
    )


def up(recipe: Recipe, build: bool, log, path: Path | None = None) -> dict[str, bool]:
    serve_models(recipe, log)
    if not Path("external/aiq").exists():
        _run(["scripts/setup-aiq.sh"], log)
    log(f"rendered {aiq_config.write(recipe)}")
    write_env(compose_env(recipe, path))
    for d in ("data/corpus/library", "data/aiq/app"):  # else Docker creates them as root
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
        served = recipe.served()
        cohosted = {c for m in served.values() for c in m.cohosts}  # stopped with their host
        for name, m in served.items():
            if m.bundle and name not in cohosted:
                cmd = ["tt", "model", "stop", m.bundle]
                log(" ".join(cmd))
                if subprocess.run(
                    cmd, check=False
                ).returncode:  # not running: keep stopping the others
                    log(f"{name}: not stopped (was it running?)")
