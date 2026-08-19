import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass
class ScoreMeta:
    title: str
    composer: str | None
    collection: str | None
    series: str | None
    source: str | None
    content_kind: str


_PAREN_RE = re.compile(r"\s*\(([^)]+)\)\s*$")


def _clean_stem(stem: str) -> str:
    # Underscores stand in for apostrophes in many exported filenames
    # (Terra_s Theme -> Terra's Theme). Dash-separated names get spaces,
    # and CamelCase run-together names get split.
    if "_" in stem and " " in stem:
        stem = stem.replace("_", "'")
    elif " " not in stem:
        stem = stem.replace("-", " ").replace("_", " ")
        stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)
    return stem.strip()


_EXT_RE = re.compile(r"\.(musx?|sib|ly|gp[x345]?|xml|musicxml|mxl|pdf|docx?)$", re.I)
_JUNK_TITLE_RE = re.compile(r"https?://|www\.|^Microsoft Word", re.I)


def _clean_pdf_title(pdf_title: str) -> str | None:
    """Engraving tools often write their document filename (or worse) as the
    PDF title; keep it only when it looks like an actual piece title."""
    t = _EXT_RE.sub("", pdf_title.strip())
    t = _PAREN_RE.sub("", t).strip()
    if t and 2 < len(t) < 120 and not _JUNK_TITLE_RE.search(t):
        return t
    return None


def musicxml_info(path: Path) -> tuple[str | None, str | None]:
    """Return (title, composer) from a MusicXML or compressed .mxl file."""
    try:
        if path.suffix.lower() == ".mxl":
            with zipfile.ZipFile(path) as z:
                names = [
                    n
                    for n in z.namelist()
                    if n.endswith(".xml") and not n.startswith("META-INF")
                ]
                if not names:
                    return None, None
                root = ET.fromstring(z.read(names[0]))
        else:
            root = ET.parse(path).getroot()
        title = root.findtext("work/work-title") or root.findtext(
            "movement-title"
        )
        composer = None
        for creator in root.iterfind("identification/creator"):
            if creator.get("type") in (None, "composer"):
                composer = (creator.text or "").strip() or None
                break
        return (title or "").strip() or None, composer
    except Exception:
        return None, None


def parse_path(rel_path: str, pdf_title: str | None = None, pdf_creator: str | None = None) -> ScoreMeta:
    """Derive metadata from a library-relative path plus optional PDF metadata.

    Layout convention observed in real libraries:
    Collection/Artist-or-Composer/[Series/.../]Title (Source).ext
    """
    p = PurePosixPath(rel_path.replace("\\", "/"))
    parts = p.parts
    stem = p.stem

    source = None
    m = _PAREN_RE.search(stem)
    if m:
        source = m.group(1).strip().replace("_", "'")
        stem = _PAREN_RE.sub("", stem)
    title = _clean_stem(stem)

    collection = parts[0] if len(parts) > 1 else None
    composer = parts[1] if len(parts) > 2 else None
    series = "/".join(parts[2:-1]) if len(parts) > 3 else None

    # Prefer an embedded PDF title when the engraving tool wrote a real one.
    if pdf_title and (cleaned := _clean_pdf_title(pdf_title)):
        title = cleaned

    kind = "unknown"
    words = {w for w in re.split(r"[^A-Za-z]+", f"{_clean_stem(p.stem)} {title}") if w}
    if "TAB" in {w.upper() for w in words}:
        kind = "tab"
    elif pdf_creator and "finale" in pdf_creator.lower():
        # Engraved guitar arrangements from Finale in this corpus carry
        # standard notation with a tab staff underneath.
        kind = "both"

    return ScoreMeta(
        title=title,
        composer=composer,
        collection=collection,
        series=series,
        source=source,
        content_kind=kind,
    )
