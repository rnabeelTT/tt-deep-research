import re
from dataclasses import dataclass

from ttdr.corpus.pdf import Page

CHUNK_CHARS = 1500
OVERLAP_CHARS = 200


@dataclass
class Chunk:
    doc: str
    page: int
    n: int
    text: str
    flags: list[str]

    @property
    def id(self) -> str:
        """Evidence ID: readable, stable across re-ingestion."""
        return f"{self.doc}#p{self.page}.{self.n}"


def split(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= size:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = text.rfind("\n", start + size // 2, end)
            end = cut if cut > 0 else end
        out.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in out if c]


def chunks(page: Page) -> list[Chunk]:
    return [Chunk(page.doc, page.page, n, t, page.flags) for n, t in enumerate(split(page.text))]
