"""PDF text layer and page flags. Pages are 0-indexed, matching FinanceBench."""

from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

MIN_TEXT_CHARS = 200
BAD_TEXT_RATIO = 0.3
LARGE_FIGURE_SHARE = 0.4


@dataclass
class Page:
    doc: str
    page: int
    text: str
    flags: list[str] = field(default_factory=list)


def _image_share(page: pdfium.PdfPage) -> float:
    w, h = page.get_size()
    area = 0.0
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=2):
        left, bottom, right, top = obj.get_bounds()
        area += max(0.0, right - left) * max(0.0, top - bottom)
    return min(1.0, area / (w * h)) if w and h else 0.0


def _bad_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    odd = sum(1 for c in chars if not (c.isprintable() and (c.isascii() or c.isalpha())))
    return odd / len(chars)


def flags_for(text: str, image_share: float) -> list[str]:
    stripped = len(text.strip())
    if stripped < MIN_TEXT_CHARS and image_share > 0.05:
        return ["image_only"]
    flags = []
    if stripped >= MIN_TEXT_CHARS and _bad_ratio(text) > BAD_TEXT_RATIO:
        flags.append("bad_text")
    if image_share > LARGE_FIGURE_SHARE:
        flags.append("large_figure")
    return flags or ["text_ok"]


def read(path: Path) -> list[Page]:
    doc = pdfium.PdfDocument(path)
    pages = []
    try:
        for i in range(len(doc)):
            p = doc[i]
            text = p.get_textpage().get_text_bounded()
            pages.append(Page(path.stem, i, text, flags_for(text, _image_share(p))))
    finally:
        doc.close()
    return pages
