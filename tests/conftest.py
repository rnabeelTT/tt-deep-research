"""Overlays the tests write themselves, so they don't depend on which recipes ship."""

from pathlib import Path

import pytest

from ttdr import recipe

ROOT = Path(__file__).parents[1]


@pytest.fixture
def overlay(tmp_path):
    """overlay(toml) -> the recipe `toml` makes on top of recipe.toml."""

    def make(body: str) -> recipe.Recipe:
        path = tmp_path / "overlay.toml"
        path.write_text(f'extends = "{ROOT}/recipe.toml"\n{body}')
        return recipe.load(path)

    return make


GPT_OSS = """
[models.gpt-oss]
kind = "chat"
bundle = "jashansinghTT/gpt-oss-20b-blackhole-fast"
profile = "p150x2"
model = "openai/gpt-oss-20b"
url = "http://localhost:8000/v1"
sampling = { temperature = 1.0, top_k = 20, top_p = 1.0 }
reasoning = "reasoning_effort"
serve_order = 0
[roles]
decide = "gpt-oss"
llm = "gpt-oss"
"""

MINICHECK = """
[models.minicheck]
kind = "check"
model = "lytang/MiniCheck-Flan-T5-Large"
url = "http://localhost:8003"
[roles]
decide = "minicheck"
rerank = ""
"""


@pytest.fixture
def gpt_oss(overlay):
    return overlay(GPT_OSS)


@pytest.fixture
def minicheck(overlay):
    return overlay(MINICHECK)


@pytest.fixture(autouse=True)
def no_recorded_ports(tmp_path, monkeypatch):
    """Recipes load with their own ports, whatever a real `ttdr up` recorded in data/."""
    from ttdr import ports

    monkeypatch.setattr(ports, "FILE", tmp_path / "ports.json")
