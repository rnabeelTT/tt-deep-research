"""Where the stack listens.

Every service has a default port: the recipe's URLs for the data services, the engine, the
sandbox and the models ttdr serves, APP for the app's own servers. `ttdr up` keeps a port our
running service already holds, moves one another program holds to the next free port up, and
records the result in data/ports.json. Every recipe load applies it, so the CLI, the engine and
the UI server all see the same ports.
"""

import json
import socket
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

FILE = Path("data/ports.json")
APP = {"ui": 3000, "api": 8200, "aiq-ui": 3001, "aiq": 8100, "dask": 8796}
"""The app's own servers: Deep Agents' UI and API, AI-Q's UI, job API and Dask scheduler."""
SERVICES = ("qdrant", "searxng", "redis", "mcp", "code")
"""Recipe [services] whose URLs carry a port."""
MODEL = "model:"
LAST = 65535


def movable(recipe) -> dict:
    """Models ttdr serves on a port of its choosing: a bundle of its own, no cohosts."""
    served = recipe.served()
    cohosted = {c for m in served.values() for c in m.cohosts}
    return {n: m for n, m in served.items() if m.bundle and not m.cohosts and n not in cohosted}


def defaults(recipe) -> dict[str, int]:
    """Every port the recipe's stack listens on, by name, before anything moves."""
    urls = {n: getattr(recipe.services, n) for n in SERVICES}
    urls |= {MODEL + n: m.url for n, m in movable(recipe).items()}
    return {**APP, **{n: p for n, u in urls.items() if u and (p := urlparse(u).port)}}


def free(port: int) -> bool:
    """Nothing listens on PORT, on any address."""
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", port))
        except OSError:
            return False
    return True


def assign(
    wanted: dict[str, int], held: dict[str, int], is_free: Callable[[int], bool] | None = None
) -> dict[str, int]:
    """HELD (ports our running services hold) stay; every other service gets its WANTED
    port, or the next one up that's free (IS_FREE, default free) and not given to another."""
    is_free = is_free or free
    taken = set(held.values())
    ports = {}
    for name, default in wanted.items():
        if name in held:
            ports[name] = held[name]
            continue
        port = next((p for p in range(default, LAST + 1) if p not in taken and is_free(p)), None)
        if port is None:
            raise SystemExit(f"no free port for {name} from {default} up")
        ports[name] = port
        taken.add(port)
    return ports


def _moved(url: str | None, port: int | None) -> str | None:
    if not url or not port:
        return url
    u = urlparse(url)
    host = u.netloc.rsplit(":", 1)[0] if u.port else u.netloc
    return u._replace(netloc=f"{host}:{port}").geturl()


def apply(recipe, ports: dict[str, int]):
    """RECIPE with its URLs on PORTS."""
    if not ports:
        return recipe
    services = recipe.services.model_copy(
        update={n: _moved(getattr(recipe.services, n), ports.get(n)) for n in SERVICES}
    )
    models = {
        n: m.model_copy(update={"url": _moved(m.url, ports.get(MODEL + n))})
        for n, m in recipe.models.items()
    }
    # The UI's origins move with it: Microsoft accepts any port of a localhost redirect URI.
    origins = [
        _moved(o, ports.get("ui")) if urlparse(o).port == APP["ui"] else o
        for o in recipe.connectors.origins
    ]
    connectors = recipe.connectors.model_copy(update={"origins": origins})
    return recipe.model_copy(
        update={"services": services, "models": models, "connectors": connectors}
    )


def read() -> dict[str, int]:
    return json.loads(FILE.read_text()) if FILE.exists() else {}


def write(ports: dict[str, int]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(ports, indent=2) + "\n")


def port(name: str) -> int:
    """Where app server NAME listens: as recorded by `ttdr up`, else its default."""
    return read().get(name, APP[name])


def local(name: str) -> str:
    return f"http://127.0.0.1:{port(name)}"
