import socket
from pathlib import Path

import pytest

from ttdr import aiq_config, ports, recipe, stack
from ttdr.health import Check

ROOT = Path(__file__).parents[1]


@pytest.fixture
def raw():
    return recipe.load(ROOT / "recipe.toml", moved=False)


def test_defaults_are_the_recipes_ports_and_the_apps(raw):
    wanted = ports.defaults(raw)
    assert wanted["ui"] == 3000 and wanted["api"] == 8200 and wanted["aiq"] == 8100
    assert wanted["qdrant"] == 6333 and wanted["redis"] == 6379 and wanted["mcp"] == 9901
    assert wanted[ports.MODEL + raw.roles["llm"]] == 8000


def test_cohost_bundles_keep_their_ports():
    r = recipe.load(ROOT / "recipes/cohost-ornith-35b.toml", moved=False)
    assert not [n for n in ports.defaults(r) if n.startswith(ports.MODEL)]


def test_a_taken_port_moves_to_the_next_free_one_and_ports_never_collide():
    taken = {3000, 3001, 8200}
    chosen = ports.assign({"ui": 3000, "aiq-ui": 3001, "api": 8200}, {}, lambda p: p not in taken)
    assert chosen == {"ui": 3002, "aiq-ui": 3003, "api": 8201}


def test_a_port_our_running_service_holds_stays():
    chosen = ports.assign({"ui": 3000, "api": 8200}, {"ui": 3004}, lambda p: p == 8205)
    assert chosen == {"ui": 3004, "api": 8205}


def test_no_free_port_is_an_error_not_a_hang():
    with pytest.raises(SystemExit, match="no free port for ui"):
        ports.assign({"ui": 3000}, {}, lambda p: False)


def test_free_sees_a_listener():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        assert not ports.free(s.getsockname()[1])


def test_apply_moves_every_url_and_keeps_the_rest(raw):
    llm = raw.roles["llm"]
    moved = ports.apply(raw, {"redis": 6380, "mcp": 9902, ports.MODEL + llm: 8010})
    assert moved.services.redis == "redis://localhost:6380"
    assert moved.services.mcp == "http://localhost:9902/mcp"
    assert moved.services.qdrant == raw.services.qdrant
    assert moved.models[llm].url == "http://localhost:8010/v1"
    assert (
        ports._moved("redis://:secret@localhost:6379/0", 6380) == "redis://:secret@localhost:6380/0"
    )


def test_every_load_sees_the_ports_up_recorded(raw):
    ports.write({"ui": 3001, "qdrant": 6334})
    assert recipe.load(ROOT / "recipe.toml").services.qdrant == "http://localhost:6334"
    assert recipe.load(ROOT / "recipe.toml", moved=False).services.qdrant == raw.services.qdrant
    assert ports.port("ui") == 3001 and ports.port("api") == 8200
    assert ports.local("ui") == "http://127.0.0.1:3001"


def test_place_keeps_what_runs_and_moves_what_another_program_holds(raw, monkeypatch):
    llm = raw.roles["llm"]
    ports.write({"api": 8201})
    monkeypatch.setattr(stack, "_running", lambda: {"deepagents", "qdrant"})
    monkeypatch.setattr(stack.health, "run", _answering(f"{llm} (llm)"))
    foreign = {3000, 8000, 8201, 6333}  # 8201 and 6333 are ours, 3000 and 8000 another program's
    monkeypatch.setattr(ports, "free", lambda p: p not in foreign)
    placed, chosen = stack.place(raw)
    assert chosen["api"] == 8201 and chosen["qdrant"] == 6333  # held
    assert chosen[ports.MODEL + llm] == 8000  # answers as the recipe's model
    assert chosen["ui"] == 3001 and chosen["aiq-ui"] == 3002  # moved past 3000, then past the UI
    assert ports.read() == chosen
    assert placed.models[llm].url == raw.models[llm].url


def test_compose_and_ai_q_get_the_chosen_ports(raw):
    chosen = {**ports.defaults(raw), "ui": 3002, "aiq": 8101}
    env = stack.compose_env(raw, chosen=chosen)
    assert env["TTDR_UI_PORT"] == "3002" and env["TTDR_AIQ_PORT"] == "8101"
    assert env["TTDR_PAGES_URL"] == "http://localhost:3002/pages"
    ports.write(chosen)
    assert aiq_config.render(raw)["general"]["front_end"]["port"] == 8101


def _answering(*names):
    async def run(_recipe, timeout=60):
        return [Check(n, True, 1.0, "") for n in names]

    return run
