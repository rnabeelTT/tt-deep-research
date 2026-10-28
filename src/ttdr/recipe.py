import os
import tomllib
from pathlib import Path

from pydantic import BaseModel


class Model(BaseModel):
    bundle: str
    model: str
    url: str
    profile: str | None = None
    sampling: dict[str, float | int] = {}
    max_state_tokens: int | None = None
    """Decision models only: tokens per state (Laya 512, Kev 65,536)."""
    chat_template: str | None = None
    """Repo path of a chat template that `ttdr up` stages for the server and passes to it."""
    prefetch: list[str] = []
    """`repo@revision` snapshots `ttdr up` downloads into the model cache before serving."""
    serve_order: int = 0
    """`ttdr up` serves models largest first (lowest order first), so tt assigns chips in order."""


class Models(BaseModel):
    llm: Model
    judge: Model
    embed: Model
    decide: Model


class Services(BaseModel):
    qdrant: str
    searxng: str
    redis: str
    mcp: str


class Features(BaseModel):
    triage: bool = False
    """Rerank search candidates with the decision model (stage 7)."""


class Recipe(BaseModel):
    version: str
    models: Models
    services: Services
    features: Features = Features()


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _read(path: Path) -> dict:
    data = tomllib.loads(path.read_text())
    if parent := data.pop("extends", None):
        data = _merge(_read(path.parent / parent), data)
    return data


def load(path: str | Path | None = None) -> Recipe:
    """Load a recipe; `extends = "<path>"` overlays it on another recipe."""
    return Recipe.model_validate(_read(Path(path or os.environ.get("TTDR_RECIPE", "recipe.toml"))))
