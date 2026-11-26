from pathlib import Path

import pytest

from ttdr import home


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A packaged app with one recipe, and ttdr run from a folder that is not a checkout."""
    files = tmp_path / "_app"
    (files / "recipes").mkdir(parents=True)
    for name in ("compose.yaml", "recipe.toml", "recipes/a.toml"):
        (files / name).write_text("packaged")
    invoked = tmp_path / "cwd"
    invoked.mkdir()
    monkeypatch.setattr(home, "APP", files)
    monkeypatch.setattr(home, "INVOKED_FROM", invoked)
    monkeypatch.setenv("TTDR_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(invoked)
    return tmp_path / "home"


def test_an_installed_ttdr_stages_its_home_and_works_there(app):
    home.enter(refresh=False)
    assert Path.cwd() == app
    assert (app / "recipes" / "a.toml").read_text() == "packaged"


def test_staged_files_are_refreshed_only_by_up_and_user_data_is_kept(app):
    home.enter(refresh=False)
    (app / "recipes" / "a.toml").write_text("edited")
    (app / ".env").write_text("KEY=1\n")
    home.enter(refresh=False)
    assert (app / "recipes" / "a.toml").read_text() == "edited"
    home.enter(refresh=True)
    assert (app / "recipes" / "a.toml").read_text() == "packaged"
    assert (app / ".env").read_text() == "KEY=1\n"


def test_a_checkout_is_never_overwritten(app, monkeypatch):
    checkout = home.INVOKED_FROM
    for name in ("compose.yaml", "recipe.toml"):
        (checkout / name).write_text("mine")
    monkeypatch.delenv("TTDR_HOME")
    home.enter(refresh=True)
    assert Path.cwd() == checkout
    assert (checkout / "recipe.toml").read_text() == "mine"
    assert not app.exists()


def test_typed_paths_are_relative_to_where_ttdr_ran(app):
    home.enter(refresh=False)
    assert home.here(Path("out.md")) == home.INVOKED_FROM / "out.md"
    assert home.here(Path("/abs/out.md")) == Path("/abs/out.md")
    assert home.here(None) is None
