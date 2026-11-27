"""Progress on stderr in tt's design language, so `tt launch deep-research` reads as one tool:
a purple spinner on what is running, ✓ or ✗ with the time it took, dim details, and gutter
lines (○ a note, → where to look). Without a terminal: one line per finished row, no motion."""

import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Self

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

ACCENT = "color(99)"  # Tenstorrent purple, as in tt's theme
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
console = Console(stderr=True, highlight=False)


def duration(seconds: float) -> str:
    """`45.2s`, `3m 34s`: how tt prints the time a step took."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m {int(seconds % 60)}s"


def clock(seconds: float) -> str:
    """`4:38`: how tt prints a counter that is still ticking."""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def shown(path: Path) -> str:
    """A path as the user would type it: absolute, with ~ for their home."""
    path = path.resolve()
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def heading(text: str) -> None:
    console.print(Text(text, style="bold"))


def note(text: str) -> None:
    console.print(Text(f"  ○ {text}", style="dim"))


def pointer(text: str) -> None:
    console.print(Text(f"  → {text}", style="dim"))


def _live() -> bool:
    # Stricter than Rich's is_terminal, which FORCE_COLOR turns on for a pipe.
    return console.is_terminal and sys.stderr.isatty()


class Board:
    """Rows that progress together: running (spinner, ticking clock), done (✓, time taken)
    or failed (✗). On a terminal they redraw in place; otherwise each prints once, finished."""

    def __init__(self, labels: dict[str, str]):
        now = time.time()
        width = max(map(len, labels.values()), default=0)
        self.rows = {
            key: {"label": label.ljust(width), "detail": "", "state": "running", "began": now}
            for key, label in labels.items()
        }
        self._display: Live | None = None

    def __enter__(self) -> Self:
        if _live():
            self._display = Live(console=console, get_renderable=self._render, refresh_per_second=8)
            self._display.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        if self._display is not None:
            self._display.__exit__(*exc)

    def set(self, key: str, *, detail: str | None = None, state: str | None = None) -> None:
        row = self.rows[key]
        if detail is not None:
            row["detail"] = detail
        if state and state != row["state"]:
            row["state"], row["took"] = state, time.time() - row["began"]
            if self._display is None:
                console.print(self._line(row))

    def state(self, key: str) -> str:
        return self.rows[key]["state"]

    def _render(self) -> Group:
        return Group(*(self._line(row) for row in self.rows.values()))

    def _line(self, row: dict) -> Text:
        detail = f"  {row['detail']}" if row["detail"] else ""
        if row["state"] == "failed":
            return Text.assemble(("✗ " + row["label"], "bold red"), (detail, "dim"))
        if row["state"] == "done":
            took = f"  {duration(row['took'])}" if row["took"] >= 0.8 else ""
            return Text.assemble(("✓ ", "green"), row["label"], (detail, "dim"), (took, "dim"))
        frame = FRAMES[int(time.time() * 10) % len(FRAMES)]
        ticking = f"  {clock(time.time() - row['began'])}"
        return Text.assemble(
            (f"{frame} ", ACCENT), (row["label"], "dim"), (detail + ticking, "dim")
        )


@contextmanager
def step(label: str):
    """One operation as one row: spinning while it runs, then ✓ (or ✗ if it raised). Yields a
    function that sets the row's detail."""
    with Board({"step": label}) as board:
        try:
            yield lambda detail: board.set("step", detail=detail)
        except BaseException:
            board.set("step", state="failed")
            raise
        board.set("step", state="done")


def tail(path: Path, lines: int = 15) -> None:
    """The end of a log, indented under the row that failed."""
    for line in path.read_text(errors="replace").splitlines()[-lines:]:
        console.print(Text(f"    {line}", style="dim"))
