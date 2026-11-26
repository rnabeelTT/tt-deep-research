"""Where ttdr works: the checkout it is run from, else $TTDR_HOME (default ~/.tt-deep-research),
which an installed ttdr fills with the app files its package ships."""

import os
import shutil
from pathlib import Path

INVOKED_FROM = Path.cwd()
APP = Path(__file__).parent / "_app"  # only in a built wheel
STAMP = ".ttdr-staged"


def _is_project(d: Path) -> bool:
    return (d / "compose.yaml").is_file() and (d / "recipe.toml").is_file()


def root() -> Path:
    if home := os.environ.get("TTDR_HOME"):
        return Path(home).expanduser()
    return INVOKED_FROM if _is_project(INVOKED_FROM) else Path.home() / ".tt-deep-research"


def enter(refresh: bool) -> None:
    """Work in root(), staging the app files there first if it has none yet, or, with refresh,
    if they were staged before. A checkout is never overwritten."""
    home = root()
    if APP.is_dir() and (not _is_project(home) or (refresh and (home / STAMP).exists())):
        shutil.copytree(APP, home, dirs_exist_ok=True)
        (home / STAMP).touch()
    os.chdir(home)
    os.environ["PWD"] = str(home)  # compose.yaml mounts ${PWD}


def here(path: Path | None) -> Path | None:
    """A path the user typed, relative to where they ran ttdr."""
    return INVOKED_FROM / path if path else None
