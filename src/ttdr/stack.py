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

from ttdr import aiq_config, health, ports, progress
from ttdr.recipe import Model, Recipe

TEMPLATE_DIR = "ttdr"
# app service -> (its port's name, health path); None: checked through the recipe's MCP URL
APP = {
    "engine": None,
    "aiq": ("aiq", "/health"),
    "ui": ("aiq-ui", "/"),
    "deepagents": ("api", "/health"),
    "ui-deepagents": ("ui", "/"),
    "ocr": None,
    "pyrun": None,  # sandboxed Python: up when the recipe sets services.code
}
OWNER = {
    "ui": "ui-deepagents",
    "api": "deepagents",
    "aiq-ui": "ui",
    "aiq": "aiq",
    "dask": "aiq",
    "qdrant": "qdrant",
    "searxng": "searxng",
    "redis": "redis",
    "mcp": "engine",
    "code": "pyrun",
}
"""The Compose service listening on each port (ttdr.ports names)."""
PORT_ENV = {
    "ui": "TTDR_UI_PORT",
    "api": "TTDR_API_PORT",
    "aiq-ui": "TTDR_AIQ_UI_PORT",
    "aiq": "TTDR_AIQ_PORT",
    "dask": "TTDR_DASK_PORT",
    "qdrant": "TTDR_QDRANT_PORT",
    "searxng": "TTDR_SEARXNG_PORT",
    "redis": "TTDR_REDIS_PORT",
    "code": "TTDR_CODE_PORT",
}
"""compose.yaml's variable for each port it publishes or passes to a server."""
DATA = ("qdrant", "searxng", "redis")  # started by the app's depends_on
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


def serve_models(recipe: Recipe, log, timeout_s: float = 1800, name: str = "") -> None:
    up = {c.name.split(" ")[0]: c.ok for c in asyncio.run(health.run(recipe))}
    preflight(recipe, up)
    served = recipe.served()
    cohosted = {c for m in served.values() for c in m.cohosts}
    shown = {n: m for n, m in served.items() if n not in cohosted and (m.bundle or up.get(n))}
    todo = [(n, m) for n, m in shown.items() if not up.get(n)]
    for n, m in served.items():
        if not m.bundle and not up.get(n):
            log(f"{n}: no bundle, not served by tt; start it at {m.url}")
    for _, m in todo:
        for snap in m.prefetch:
            repo, rev = snap.split("@")
            hub = str(model_cache() / "hub")
            _run(["hf", "download", repo, "--revision", rev, "--cache-dir", hub], log)
        if m.chat_template:
            (model_cache() / TEMPLATE_DIR).mkdir(parents=True, exist_ok=True)
            shutil.copy(m.chat_template, model_cache() / TEMPLATE_DIR)
    pinned = all(m.chips for _, m in todo)
    if not shown:
        return
    progress.heading(f"Serving the {name} recipe" if name else "Serving the recipe's models")
    if todo:
        how = "each on its own chips" if pinned else "tt picks the chips, one at a time"
        progress.note(
            f"{len(todo)} models to start, {how} · 10-15 min from cold · logs in "
            f"{progress.shown(LOGS)}"
        )
    if not pinned:  # tt picks the chips: one model at a time, largest first, for a stable order
        for _, m in todo:
            _run(serve_command(m), log)
    starts = _start_together(todo) if pinned else {}
    _watch(recipe, shown, up, starts, timeout_s)


LOGS = Path("data/logs")


def _start_together(todo: list[tuple[str, Model]]) -> dict[str, tuple[subprocess.Popen, Path]]:
    """Start models pinned to their own chips all at once, so their downloads and loads
    overlap: {model: (its tt serve, its log)}."""
    if any(not m.cohosts for _, m in todo):
        # `tt serve` installs tt-model on first use; do that once, not in every serve at once.
        subprocess.run(["tt", "model", "package", "--help"], check=True, capture_output=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    starts = {}
    for name, m in todo:
        path = LOGS / f"serve-{name}.log"
        with path.open("w") as out:
            starts[name] = (subprocess.Popen(serve_command(m), stdout=out, stderr=out), path)
    return starts


def _watch(recipe: Recipe, shown: dict[str, Model], up: dict, starts, timeout_s: float) -> None:
    """A row per model until each answers: starting (downloading, starting its container),
    loading, then ✓ with how long it took. A start that fails stops the wait and shows its log."""
    labels = {n: m.model.split("/")[-1] for n, m in shown.items()}
    chips = {n: f"chips {m.chips}" if m.chips else "" for n, m in shown.items()}
    chips = {n: c.ljust(max(map(len, chips.values()))) for n, c in chips.items()}  # stages line up
    began, checked = time.time(), 0.0
    with progress.Board(labels) as board:
        for n in shown:
            stage = "already serving" if up.get(n) else "starting" if n in starts else "loading"
            board.set(n, detail=" · ".join(filter(None, [chips[n], stage])))
            if up.get(n):
                board.set(n, state="done")
        while any(board.state(n) == "running" for n in shown) and time.time() - began < timeout_s:
            for n, (proc, _) in starts.items():
                if board.state(n) == "running" and proc.poll() is not None:
                    if proc.returncode:
                        board.set(n, state="failed")
                    else:
                        board.set(n, detail=" · ".join(filter(None, [chips[n], "loading"])))
            if any(board.state(n) == "failed" for n in shown):
                break
            if time.time() - checked > 10:
                checked = time.time()
                answering = {c.name.split(" ")[0] for c in asyncio.run(health.run(recipe)) if c.ok}
                for n in answering & set(shown):
                    if board.state(n) == "running":
                        board.set(n, detail=chips[n], state="done")
            time.sleep(1)
        failed = [n for n in shown if board.state(n) == "failed"]
        late = [n for n in shown if board.state(n) == "running"]
        for n in late:
            board.set(n, state="failed")
    for n in failed:
        progress.pointer(f"{labels[n]} did not start; the end of {progress.shown(starts[n][1])}:")
        progress.tail(starts[n][1])
    if failed:
        raise SystemExit(1)
    if late:
        logs = "; ".join(f"`tt model logs {shown[n].bundle} --follow`" for n in late)
        progress.pointer(f"not answering after {progress.duration(timeout_s)}: {logs}")
        raise SystemExit(1)


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
    """A row per app endpoint until each answers, or the timeout."""
    deadline, status = time.time() + timeout_s, dict.fromkeys(urls, False)
    with httpx.Client(timeout=5) as http, progress.Board(dict(zip(urls, urls))) as board:
        for name in urls:
            board.set(name, detail="starting")
        while not all(status.values()) and time.time() < deadline:
            for name, url in urls.items():
                if status[name]:
                    continue
                try:
                    status[name] = _up(http, name, url)
                except httpx.HTTPError:
                    pass
                if status[name]:
                    board.set(name, detail="answering", state="done")
            if not all(status.values()):
                time.sleep(3)
        for name, ok in status.items():
            if not ok:
                board.set(name, detail="not answering", state="failed")
    return status


def compose_env(
    recipe: Recipe, path: Path | None = None, chosen: dict[str, int] | None = None
) -> dict[str, str]:
    """Recipe values and CHOSEN ports Compose needs, written to .env (Compose reads it
    automatically). The engine loads the same recipe file, overlays included. Page links in
    citations use TTDR_PAGES_URL from the environment: the UI's address as browsers reach it,
    plus /pages."""
    chosen = chosen or ports.APP
    mcp = urlparse(recipe.services.mcp)
    pages = f"http://localhost:{chosen['ui']}/pages"
    env = {
        "TTDR_RECIPE": str(path or "recipe.toml"),
        "TTDR_ENGINE_URL": f"{mcp.scheme}://{mcp.netloc}",
        "TTDR_PAGES_URL": os.environ.get("TTDR_PAGES_URL", pages),
        "AIQ_AUTO_TOOL_CHOICE": "1" if recipe.features.auto_tool_choice else "0",
        "TTDR_UID": str(os.getuid()),
        "TTDR_GID": str(os.getgid()),
        **{var: str(chosen[n]) for n, var in PORT_ENV.items() if n in chosen},
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


def _running() -> set[str]:
    """Compose services running now."""
    ps = ["docker", "compose", "ps", "--status", "running", "--format", "{{.Service}}"]
    return set(subprocess.run(ps, capture_output=True, text=True, check=False).stdout.split())


def held(recipe: Recipe, last: dict[str, int]) -> dict[str, int]:
    """Ports our own services hold now, where `ttdr up` LAST put them: running Compose
    services, and models that answer as the recipe's."""
    where = {n: last.get(n, p) for n, p in ports.defaults(recipe).items()}
    running = _running()
    answering = {
        c.name.split(" ")[0] for c in asyncio.run(health.run(ports.apply(recipe, where))) if c.ok
    }

    def ours(name: str) -> bool:
        model = name.removeprefix(ports.MODEL)
        return model in answering if model != name else OWNER[name] in running

    return {n: p for n, p in where.items() if ours(n)}


def place(recipe: Recipe) -> tuple[Recipe, dict[str, int]]:
    """RECIPE (with its own ports) on ports free for it, recorded for every later load."""
    wanted = ports.defaults(recipe)
    chosen = ports.assign(wanted, held(recipe, ports.read()))
    ports.write(chosen)
    for name, port in chosen.items():
        if port != wanted[name]:
            progress.note(f"{name}: port {wanted[name]} is taken, using {port}")
    return ports.apply(recipe, chosen), chosen


def up(
    recipe: Recipe, build: bool, log, path: Path | None = None
) -> tuple[dict[str, bool], dict[str, int]]:
    """RECIPE with its own ports (`recipe.load(path, moved=False)`): `ttdr up` places them."""
    from ttdr.recipe import name_of

    recipe, chosen = place(recipe)
    serve_models(recipe, log, name=name_of(path) if path else "")
    progress.heading("Starting the app")
    LOGS.mkdir(parents=True, exist_ok=True)
    if not Path("external/aiq").exists():
        with progress.step("AI-Q source") as detail:
            _logged(["scripts/setup-aiq.sh"], LOGS / "setup-aiq.log")
            detail("cloned and patched")
    aiq_config.write(recipe)
    write_env(compose_env(recipe, path, chosen))
    for d in ("data/corpus/library", "data/aiq/app"):  # else Docker creates them as root
        Path(d).mkdir(parents=True, exist_ok=True)
    services = app_services(recipe)
    flags = ["--build"] if build else []
    with progress.step("App containers") as detail:
        detail(", ".join(services))
        _logged(
            ["docker", "compose", "--profile", "app", "up", "-d", *flags, *services],
            LOGS / "compose.log",
        )
    urls = {"engine": recipe.services.mcp}
    urls |= {s: f"http://127.0.0.1:{chosen[APP[s][0]]}{APP[s][1]}" for s in services if APP[s]}
    return wait(urls), chosen


def _logged(cmd: list[str], path: Path) -> None:
    """Run a noisy command with its output in a log; on failure, show the log's end."""
    with path.open("w") as out:
        code = subprocess.run(cmd, stdout=out, stderr=out, check=False).returncode
    if code:
        progress.pointer(f"`{' '.join(cmd[:4])} …` failed; the end of {progress.shown(path)}:")
        progress.tail(path)
        raise SystemExit(code)


def down(recipe: Recipe, keep_models: bool = False) -> None:
    """Remove the app containers, stop the data services (Qdrant, Redis, SearXNG; their
    volumes keep the indexes and history) and, unless kept, the recipe's models."""
    LOGS.mkdir(parents=True, exist_ok=True)
    with progress.step("App and data services") as detail:
        _logged(
            ["docker", "compose", "--profile", "app", "rm", "--stop", "--force", *APP],
            LOGS / "compose.log",
        )
        _logged(["docker", "compose", "stop", *DATA], LOGS / "compose.log")
        detail("stopped; the data is kept")
    if keep_models:
        return
    served = recipe.served()
    cohosted = {c for m in served.values() for c in m.cohosts}  # stopped with their host
    hosts = {n: m for n, m in served.items() if m.bundle and n not in cohosted}
    running = _running_bundles()
    with progress.Board({n: m.model.split("/")[-1] for n, m in hosts.items()}) as board:
        for name, m in hosts.items():
            if m.bundle not in running:
                board.set(name, detail="not running", state="done")
                continue
            with (LOGS / "stop.log").open("a") as out:
                stop = ["tt", "model", "stop", m.bundle]
                code = subprocess.run(stop, stdout=out, stderr=out, check=False).returncode
            if code:  # keep stopping the others
                board.set(name, detail=f"see {progress.shown(LOGS / 'stop.log')}", state="failed")
            else:
                board.set(name, detail="stopped", state="done")
    if any(board.state(n) == "failed" for n in hosts):
        raise SystemExit(1)


def _running_bundles() -> set[str]:
    """The bundles tt-model is serving, read off its containers' labels."""
    label = "org.tenstorrent.tt-model"
    listed = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"label={label}",
            "--format",
            f'{{{{.Label "{label}.repo"}}}}',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return set(listed.stdout.split())
