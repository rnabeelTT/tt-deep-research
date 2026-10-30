"""The recipe: which models exist, which role each one plays, and where everything lives.

Models are a registry (`[models.<name>]`, each with a `kind`); `[roles]` assigns them to the
jobs the stack needs. Swapping, adding or removing a model is a recipe change; adding a new
*kind* of model is one client class (see docs/ARCHITECTURE.md).
"""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator

Kind = Literal["chat", "embed", "decide", "check"]
Reasoning = Literal["enable_thinking", "reasoning_effort"]

# role -> (kinds it accepts, required?)
ROLES: dict[str, tuple[tuple[str, ...], bool]] = {
    "llm": (("chat",), True),  # plan, read, write
    "embed": (("embed",), True),  # queries, chunks, page images
    "judge": (("chat",), False),  # on-box judge for quick checks
    # verify claims, triage; a chat model answers the same typed questions, a claim checker
    # (check) only verifies; without it both are off
    "decide": (("decide", "chat", "check"), False),
    "ocr": (("chat",), False),  # vision model for OCR; without it the CPU OCR backend is used
}


class Model(BaseModel):
    kind: Kind
    bundle: str | None = None
    """tt-model bundle `ttdr up` serves; models without one (CPU) run elsewhere."""
    model: str
    url: str
    profile: str | None = None
    sampling: dict[str, float | int] = {}
    reasoning: Reasoning | None = None
    """How this model's thinking is switched: Qwen-style template flag or gpt-oss effort."""
    max_state_tokens: int | None = None
    """Decision models only: tokens per state (Laya 512, Kev 65,536)."""
    chat_template: str | None = None
    """Repo path of a chat template that `ttdr up` stages for the server and passes to it."""
    prefetch: list[str] = []
    """`repo@revision` snapshots `ttdr up` downloads into the model cache before serving."""
    serve_order: int = 0
    """`ttdr up` serves models largest first (lowest order first), so tt assigns chips in order."""

    def request_settings(self, thinking: bool | None = None) -> dict:
        """OpenAI-client settings: temperature/top_p, plus the rest of the sampling and the
        model's own thinking switch in extra_body (for AI-Q, LangChain or any OpenAI client)."""
        top = {k: self.sampling[k] for k in ("temperature", "top_p") if k in self.sampling}
        extra = {k: v for k, v in self.sampling.items() if k not in top}
        if thinking is not None and self.reasoning == "enable_thinking":
            extra["chat_template_kwargs"] = {"enable_thinking": thinking}
        elif thinking is not None and self.reasoning == "reasoning_effort":
            extra["reasoning_effort"] = "high" if thinking else "low"
        return {**top, "extra_body": extra}


class Services(BaseModel):
    qdrant: str
    redis: str
    mcp: str
    searxng: str | None = None
    """Without it, search covers the private corpus only."""


class Features(BaseModel):
    triage: bool = False
    """Rerank search candidates with the decide model (stage 7)."""
    ocr: bool = True
    """Queue scanned pages for OCR and run the OCR worker."""
    ui: bool = True
    """Run the web UI container."""
    auto_tool_choice: bool = False
    """AI-Q: let the model choose tools instead of forcing structured output (patch 0004)."""


def _drop_empty(data: dict) -> dict:
    """An overlay removes a role or optional service by setting it to ""."""
    data = dict(data)
    if "roles" in data:
        data["roles"] = {k: v for k, v in data["roles"].items() if v}
    if "services" in data:
        data["services"] = {k: v for k, v in data["services"].items() if v != ""}
    return data


def _legacy(data: dict) -> dict:
    """Recipes recorded before roles had fixed model keys (llm, judge, embed, decide)."""
    kinds = {"llm": "chat", "judge": "chat", "embed": "embed", "decide": "decide"}
    models = data.get("models", {})
    if "roles" in data or not set(models) <= set(kinds):
        return data
    return {
        **data,
        "models": {k: {"kind": kinds[k], **v} for k, v in models.items()},
        "roles": {k: k for k in models},
    }


class Recipe(BaseModel):
    version: str
    models: dict[str, Model]
    roles: dict[str, str]
    services: Services
    features: Features = Features()

    @model_validator(mode="before")
    @classmethod
    def _upgrade(cls, data):
        return _drop_empty(_legacy(data)) if isinstance(data, dict) else data

    @model_validator(mode="after")
    def _check_roles(self):
        for role, name in self.roles.items():
            if role not in ROLES:
                raise ValueError(f"unknown role {role!r}; known: {', '.join(ROLES)}")
            if name not in self.models:
                raise ValueError(f"role {role!r} names model {name!r}, which isn't in [models]")
            if self.models[name].kind not in ROLES[role][0]:
                kinds = " or ".join(ROLES[role][0])
                raise ValueError(f"role {role!r} needs a {kinds} model, {name!r} is not")
        missing = [r for r, (_, required) in ROLES.items() if required and r not in self.roles]
        if missing:
            raise ValueError(f"missing required roles: {', '.join(missing)}")
        if self.features.triage and self.decide and self.decide.kind == "check":
            raise ValueError("triage needs a decide or chat model; a claim checker only verifies")
        return self

    def role(self, name: str) -> Model | None:
        return self.models[self.roles[name]] if name in self.roles else None

    @property
    def llm(self) -> Model:
        return self.models[self.roles["llm"]]

    @property
    def embed(self) -> Model:
        return self.models[self.roles["embed"]]

    @property
    def judge(self) -> Model:
        return self.role("judge") or self.llm

    @property
    def decide(self) -> Model | None:
        return self.role("decide")

    def served(self) -> dict[str, Model]:
        """Models some role uses, in serve order."""
        used = {n: self.models[n] for n in dict.fromkeys(self.roles.values())}
        return dict(sorted(used.items(), key=lambda kv: kv[1].serve_order))


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
