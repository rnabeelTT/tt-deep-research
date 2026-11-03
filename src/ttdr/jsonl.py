"""JSONL reading."""

from pathlib import Path


def lines(path: str | Path) -> list[str]:
    """The non-empty lines of a JSONL file. Splits on newlines only: str.splitlines also breaks at
    U+2028 and other separators that JSON strings may hold unescaped (model output does)."""
    return [line for line in Path(path).read_text().split("\n") if line.strip()]
