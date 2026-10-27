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


class Models(BaseModel):
    llm: Model
    judge: Model
    embed: Model
    decide: Model


class Services(BaseModel):
    qdrant: str
    searxng: str
    redis: str


class Recipe(BaseModel):
    version: str
    models: Models
    services: Services


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
