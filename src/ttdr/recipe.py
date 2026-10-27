import os
import tomllib
from pathlib import Path

from pydantic import BaseModel


class Model(BaseModel):
    bundle: str
    model: str
    url: str
    profile: str | None = None


class Models(BaseModel):
    llm: Model
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


def load(path: str | Path | None = None) -> Recipe:
    path = Path(path or os.environ.get("TTDR_RECIPE", "recipe.toml"))
    return Recipe.model_validate(tomllib.loads(path.read_text()))
