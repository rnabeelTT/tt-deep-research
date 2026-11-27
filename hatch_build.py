"""Wheel build hook: a built wheel carries the app files `ttdr up` needs, which an installed ttdr
stages into $TTDR_HOME (src/ttdr/home.py). An editable install (a checkout, the engine image) runs
from the files themselves and gets none: the image copies only what it builds from."""

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

APP_FILES = (
    "compose.yaml",
    "recipe.toml",
    "recipes",
    "configs",
    "docker",
    "workers",
    "scripts",
    "patches",
    ".dockerignore",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "hatch_build.py",
    "src/ttdr",
)


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if version != "editable":
            build_data["force_include"].update({path: f"ttdr/_app/{path}" for path in APP_FILES})
