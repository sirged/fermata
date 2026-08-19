from pathlib import Path

import fitz  # PyMuPDF

from .config import CACHE_DIR

THUMB_WIDTH = 400


def thumb_path(file_hash: str) -> Path:
    return CACHE_DIR / "thumbs" / f"{file_hash}.png"


def generate_pdf_thumb(pdf_path: Path, file_hash: str) -> Path | None:
    out = thumb_path(file_hash)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with fitz.open(pdf_path) as doc:
            if doc.page_count == 0:
                return None
            page = doc[0]
            zoom = THUMB_WIDTH / max(page.rect.width, 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            pix.save(out)
        return out
    except Exception:
        return None


def pdf_info(pdf_path: Path) -> tuple[int, str | None, str | None]:
    """Return (page_count, title, creator) for a PDF, tolerating bad files."""
    try:
        with fitz.open(pdf_path) as doc:
            meta = doc.metadata or {}
            return doc.page_count, meta.get("title") or None, meta.get("creator") or None
    except Exception:
        return 0, None, None
