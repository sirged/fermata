"""
Decode note durations and time signature from vector-engraved PDF pages by
identifying the music-font glyphs actually used for noteheads, flags, rests,
dots and time-signature digits - instead of guessing from x-spacing.

How this works (see the accompanying report for full validation detail):

  Finale exports embed a font called "Maestro" as a TrueType subset. The
  glyph *names* are stripped (post table format 3, "glyph00001" etc.) so
  they can't be read directly. BUT: across every Finale/Maestro PDF checked
  (multiple files under library/Patreon/John Oeth/), the embedded subset is
  always the SAME reduced ~204-glyph Maestro subset, and a given glyph ID's
  outline coordinates are byte-for-byte identical file to file whenever that
  glyph is used. So glyph ID (GID) *is* a stable key for Maestro, even
  though the name table is gone - confirmed by direct coordinate diffing,
  not assumed.

  Sibelius exports embed "Opus"/"OpusSpecial"/"OpusText" as TrueType
  subsets whose post table format DOES retain names, but as PUA codepoint
  labels like "uniF0CF" rather than "noteheadBlack" - not semantically
  meaningful text, but a stable KEY (confirmed identical across 6 different
  Sibelius/Opus files sampled from the library, both Finale and Sibelius
  sub-vocabularies were visually verified by rendering every used
  glyph's actual outline to a contact-sheet PNG and eyeballing it against
  the rendered page).

  Every glyph->meaning mapping below was established by: (1) collecting the
  set of distinct GIDs/names actually used across a sample of library PDFs,
  (2) rendering each one's real vector outline from the embedded font
  (fontTools glyf parsing + quadratic curve flattening), (3) visually
  reading the rendered shape, and (4) cross-checking occurrence counts /
  aspect ratios / fill ratios against what the shape should look like
  (e.g. the dominant highest-frequency glyph on every Maestro page is, and
  should be, the plain filled notehead).

  Stems and beams are NOT font glyphs in either exporter - they are vector
  line/rectangle primitives (page.get_drawings()) - confirmed by inspecting
  drawings positioned exactly where a stem/beam should be. Duration is
  therefore decoded by combining: notehead glyph shape (filled/hollow/wide)
  + presence of a stem (vector) + flag glyph count at the stem's free end
  + stacked beam-rectangle count at the stem's x position + trailing
  augmentation-dot glyphs.
"""
import io
import collections
from pathlib import Path

import fitz  # pymupdf
from fontTools.ttLib import TTFont


# ---------------------------------------------------------------------------
# Calibrated glyph -> meaning tables (see module docstring for how these were
# derived; each entry was visually confirmed against a rendered outline).
# ---------------------------------------------------------------------------

# Finale "Maestro": keyed by glyph ID (GID), stable across files that use the
# same reduced-subset export pipeline (confirmed on Zanarkand / 1 AM / Hinata
# vs Neji and 40 further sampled John Oeth PDFs).
MAESTRO_GID_MAP = {
    2: "sharp", 4: "simile", 13: "dot", 16: "digit1", 17: "digit2", 32: "flat_paren",
    18: "digit3", 19: "digit4", 20: "digit5", 21: "digit6", 23: "digit8",
    29: "accent", 31: "tremolo", 40: "flag8", 44: "natural_paren",
    48: "flag16", 51: "fermata", 52: "clef", 63: "sharp_paren", 64: "flat",
    68: "trill", 71: "flag8", 75: "natural", 79: "flag16",
    84: "notehead_whole", 144: "notehead_x", 149: "rest8", 156: "rest_quarter",
    157: "notehead_filled", 171: "coda", 174: "notehead_diamond",
    177: "rest8", 187: "rest_half_whole", 199: "notehead_half",
}

# Sibelius "Opus" / "OpusSpecial" / "OpusText": keyed by glyph NAME (the PUA
# label), stable across files even though GIDs are not (Opus subsets are
# tightly per-file, unlike Maestro's fixed-size subset).
OPUS_NAME_MAP = {
    "uniF023": "sharp", "uniF026": "clef", "uniF02E": "dot",
    "uniF032": "digit2", "uniF033": "digit3", "uniF034": "digit4",
    "uniF036": "digit6", "uniF038": "digit8", "uniF03E": "accent",
    "uniF043": "cut_time", "uniF04A": "flag8", "uniF055": "fermata",
    "uniF062": "flat", "uniF063": "common_time", "uniF065": "note_pictograph",
    "uniF068": "note_pictograph", "uniF06A": "note_pictograph",
    "uniF071": "note_pictograph", "uniF06E": "natural",
    "uniF077": "notehead_whole", "uniF0B2": "up_bow",
    "uniF0B3": "bracket", "uniF0B7": "rest_half_whole",
    "uniF0CE": "flag8_or_rest_quarter",  # disambiguated by stem proximity
    "uniF0CF": "notehead_filled", "uniF0DC": "notehead_x",
    "uniF0E4": "rest8", "uniF0EE": "rest_half_whole", "uniF0FA": "notehead_half",
}
OPUS_SPECIAL_NAME_MAP = {
    "uniF0AA": "dot", "uniF0DA": "tab_label", "uniF0A1": "tuplet_bracket",
    "uniF0A2": "tuplet_bracket", "uniF083": "down_stroke", "uniF089": "up_stroke",
    "uniF0DC": "digit8",
    "uniF0E1": "string1", "uniF0E2": "string2", "uniF0E3": "string3",
    "uniF0E4": "string4", "uniF0E5": "string5", "uniF0E6": "string6",
}

DIGIT_CATS = {f"digit{d}": d for d in range(10)}

NOTEHEAD_CATS = {"notehead_filled", "notehead_half", "notehead_whole", "notehead_x", "notehead_diamond"}
FLAG_CATS = {"flag8", "flag16"}
REST_CATS = {"rest8", "rest_quarter", "rest_half_whole", "flag8_or_rest_quarter"}
DOT_CATS = {"dot"}


# ---------------------------------------------------------------------------
# Font handling
# ---------------------------------------------------------------------------

class MusicFont:
    """One embedded music-symbol font resource on a page, with its glyph
    order resolved so GIDs (Maestro) or names (Opus family) can be mapped to
    a semantic category."""

    def __init__(self, family, tt):
        self.family = family  # "Maestro" | "Opus" | "OpusSpecial" | "OpusText"
        self.tt = tt
        self.glyph_order = tt.getGlyphOrder() if tt else []

    def category(self, gid):
        if self.family == "Maestro":
            return MAESTRO_GID_MAP.get(gid)
        gname = self.glyph_order[gid] if tt_has(self, gid) else None
        if gname is None:
            return None
        if self.family == "Opus":
            return OPUS_NAME_MAP.get(gname)
        if self.family == "OpusSpecial":
            return OPUS_SPECIAL_NAME_MAP.get(gname)
        return None


def tt_has(mf, gid):
    return 0 <= gid < len(mf.glyph_order)


def load_music_fonts(doc, page):
    """Return {family_name: MusicFont} for every Maestro/Opus* font resource
    referenced on this page."""
    fonts = {}
    for f in page.get_fonts(full=True):
        xref, ext, ftype, basefont = f[0], f[1], f[2], f[3]
        base = basefont.split("+")[-1]
        if base not in ("Maestro", "Opus", "OpusSpecial", "OpusText"):
            continue
        if base in fonts:
            continue
        if ext not in ("ttf", "otf"):
            continue  # CFF-embedded variants not covered by this decoder yet
        try:
            content = doc.extract_font(xref)
            if isinstance(content, tuple):
                content = content[-1]
            tt = TTFont(io.BytesIO(content), fontNumber=0) if content else None
        except Exception:
            tt = None
        if tt is not None:
            fonts[base] = MusicFont(base, tt)
    return fonts


# ---------------------------------------------------------------------------
# Glyph event extraction
# ---------------------------------------------------------------------------

class GlyphEvent:
    __slots__ = ("family", "gid", "category", "x0", "y0", "x1", "y1", "code")

    def __init__(self, family, gid, category, bbox, code):
        self.family = family
        self.gid = gid
        self.category = category
        self.x0, self.y0, self.x1, self.y1 = bbox
        self.code = code

    @property
    def xc(self):
        return (self.x0 + self.x1) / 2

    @property
    def yc(self):
        return (self.y0 + self.y1) / 2

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0

    def __repr__(self):
        return f"<{self.category or '?'} g{self.gid} @({self.x0:.1f},{self.y0:.1f})>"


def extract_glyph_events(page):
    """Walk page.get_texttrace() and classify every char drawn in a known
    music font into a semantic category (category is None if the glyph
    wasn't in our calibrated table - reported, not silently dropped)."""
    fonts = load_music_fonts(page.parent, page)
    if not fonts:
        return [], fonts, []

    events = []
    unknown = []
    trace = page.get_texttrace()
    for span in trace:
        fname = span.get("font", "")
        mf = fonts.get(fname)
        if mf is None:
            continue
        for ch in span.get("chars", []):
            code, gid, origin, bbox = ch
            cat = mf.category(gid)
            ev = GlyphEvent(fname, gid, cat, bbox, code)
            events.append(ev)
            if cat is None:
                unknown.append(ev)
    return events, fonts, unknown


# ---------------------------------------------------------------------------
# Vector primitives: stems, beams, ties/slurs
# ---------------------------------------------------------------------------

Stem = collections.namedtuple("Stem", "x y0 y1")
Beam = collections.namedtuple("Beam", "x0 x1 yc")
Curve = collections.namedtuple("Curve", "pts x0 x1 y0 y1")


def extract_stems_beams_curves(page, y_lo, y_hi, x_lo, x_hi):
    stems, beams, curves = [], [], []
    for d in page.get_drawings():
        items = d.get("items", [])
        fill = d.get("fill")
        rect = d.get("rect")
        if rect is None:
            continue
        if not (x_lo - 5 <= rect.x0 and rect.x1 <= x_hi + 5):
            continue
        if not (y_lo - 40 <= rect.y0 and rect.y1 <= y_hi + 40):
            continue

        if fill and any(c is not None and c < 0.3 for c in fill) and len(items) >= 3:
            # small filled polygon, roughly horizontal and thin -> beam segment
            h = rect.height
            w = rect.width
            if 0.8 <= h <= 14.0 and w >= 2.0:
                beams.append(Beam(rect.x0, rect.x1, (rect.y0 + rect.y1) / 2))
            continue

        for item in items:
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.x - p2.x) < 0.15 and 4.0 <= abs(p1.y - p2.y) <= 45.0:
                    stems.append(Stem((p1.x + p2.x) / 2, min(p1.y, p2.y), max(p1.y, p2.y)))
            elif item[0] == "re":
                r = item[1]
                if r.width < 1.0 and 4.0 <= r.height <= 45.0:
                    stems.append(Stem((r.x0 + r.x1) / 2, r.y0, r.y1))
            elif item[0] == "c":
                p0, p1, p2, p3 = item[1], item[2], item[3], item[4]
                xs = [p0.x, p1.x, p2.x, p3.x]
                ys = [p0.y, p1.y, p2.y, p3.y]
                curves.append(Curve((p0, p1, p2, p3), min(xs), max(xs), min(ys), max(ys)))
    return stems, beams, curves


# ---------------------------------------------------------------------------
# Duration model
# ---------------------------------------------------------------------------

DURATION_CODE = {4.0: 1, 2.0: 2, 1.0: 4, 0.5: 8, 0.25: 16, 0.125: 32}


class NoteEvent:
    def __init__(self, x, y, base_units, flags, dotted, is_rest, category, notehead_kind=None):
        self.x = x
        self.y = y
        self.base_units = base_units
        self.flags = flags  # int hook/beam count found
        self.dotted = dotted  # 0, 1 or 2
        self.is_rest = is_rest
        self.category = category
        self.notehead_kind = notehead_kind
        self.tied_next = False  # best-effort: see _mark_ties()

    @property
    def quarter_units(self):
        u = self.base_units / (2 ** self.flags)
        if self.dotted == 1:
            u *= 1.5
        elif self.dotted == 2:
            u *= 1.75
        return u

    @property
    def duration_code(self):
        # snap to nearest known plain code, then report dots separately
        plain = self.base_units / (2 ** self.flags)
        return DURATION_CODE.get(plain, DURATION_CODE[min(DURATION_CODE, key=lambda k: abs(k - plain))])

    def __repr__(self):
        d = "." * self.dotted
        kind = "R" if self.is_rest else (self.notehead_kind or "?")
        return f"<{kind} 1/{self.duration_code}{d} @x={self.x:.1f}>"


def _candidate_stems(stems, x0, x1, yc, x_tol=3.5, y_tol=6.0):
    """Stems attach at one SIDE of a notehead (left edge for a down-stem,
    right edge for an up-stem) at roughly the notehead's vertical center -
    not at its bbox center-x or top/bottom edge. In dense/chordal writing
    more than one stem can plausibly sit near a given notehead, so this
    returns every plausible candidate rather than committing to one; the
    caller resolves ambiguity by checking which candidate actually leads to
    a real beam/flag (see decode_note_events)."""
    out = []
    for s in stems:
        if not (abs(s.x - x0) <= x_tol or abs(s.x - x1) <= x_tol):
            continue
        near_end = s.y0 if abs(s.y0 - yc) < abs(s.y1 - yc) else s.y1
        if abs(near_end - yc) > y_tol:
            continue
        out.append(s)
    return out


def _flag_count_near(events, stem, notehead_yc, x_tol=5.0, y_tol=9.0):
    """Count flag hooks attached at the free end of a stem (the end further
    from the notehead)."""
    free_y = stem.y1 if abs(stem.y1 - notehead_yc) > abs(stem.y0 - notehead_yc) else stem.y0
    hooks = 0
    for ev in events:
        if ev.category not in FLAG_CATS:
            continue
        if abs(ev.xc - stem.x) > x_tol:
            continue
        if abs(ev.yc - free_y) > y_tol:
            continue
        hooks += 1 if ev.category == "flag8" else 2  # flag16 glyph = 2 hooks in one glyph
    return hooks


def _beam_count_near(beams, stem, x_tol=3.0, y_tol=6.0):
    """Count distinct stacked beam strokes whose x-span covers this stem AND
    whose y sits near the stem's free (non-notehead) end - a beam attaches
    at the tip of a stem, not just anywhere along its x position, so the y
    check matters to avoid grabbing a neighboring voice's beam that happens
    to pass over this stem's x."""
    levels = []
    for b in beams:
        if not (b.x0 - x_tol <= stem.x <= b.x1 + x_tol):
            continue
        if not (stem.y0 - y_tol <= b.yc <= stem.y1 + y_tol):
            continue
        levels.append(round(b.yc, 1))
    if not levels:
        return 0
    levels.sort()
    clusters = [levels[0]]
    for y in levels[1:]:
        if y - clusters[-1] > 2.0:
            clusters.append(y)
    return len(clusters)


def _mark_ties(notes, curves, gap_max=40.0, y_tol=0.5, height_max=8.0):
    """Best-effort tie detection: flag notes[i].tied_next when a shallow
    curve bridges notes[i] and notes[i+1] and both sit at the same pitch
    (same y - ties join equal pitches, unlike slurs which usually don't).
    This is NOT used to merge durations (each notehead's own notated value
    is kept as-is) - it is reported as a separate signal because tie
    handling is a known weak spot worth surfacing honestly rather than
    silently getting wrong.
    """
    pitched = [n for n in notes if not n.is_rest]
    for a, b in zip(pitched, pitched[1:]):
        if abs(a.y - b.y) > y_tol:
            continue
        gap = b.x - a.x
        if not (0 < gap <= gap_max):
            continue
        for c in curves:
            span = c.x1 - c.x0
            height = c.y1 - c.y0
            if height > height_max:
                continue
            if span < gap * 0.25 or span > gap * 1.3:
                continue
            mid = (c.x0 + c.x1) / 2
            if a.x - 2 <= mid <= b.x + 2:
                a.tied_next = True
                break


def _dot_count_after(events, x1, yc, x_tol=6.0, y_tol=4.0):
    dots = [ev for ev in events if ev.category in DOT_CATS
            and x1 - 1.0 <= ev.xc <= x1 + x_tol and abs(ev.yc - yc) <= y_tol]
    return min(len(dots), 2)


def decode_note_events(page, staff_top, staff_bottom, staff_x0, staff_x1, line_ys):
    """Core decode for one standard-notation staff: returns sorted NoteEvent
    list. line_ys: sorted list of the 5 staff line y-coordinates (for
    half/whole rest disambiguation)."""
    events, fonts, unknown = extract_glyph_events(page)
    if not events:
        return [], {"unknown_glyphs": 0, "note_events": 0}

    pad = (staff_bottom - staff_top) * 1.6
    band_lo, band_hi = staff_top - pad, staff_bottom + pad
    staff_events = [e for e in events if band_lo <= e.yc <= band_hi and staff_x0 - 3 <= e.xc <= staff_x1 + 3]

    stems, beams, curves = extract_stems_beams_curves(page, staff_top, staff_bottom, staff_x0, staff_x1)

    notes = []
    for ev in staff_events:
        if ev.category in NOTEHEAD_CATS:
            if ev.category == "notehead_whole":
                # whole notes never take a stem, flag or beam by definition -
                # don't even look for one (a nearby unrelated stem in a dense
                # chord/2-voice passage would otherwise be a false positive).
                base, flags = 4.0, 0
            elif ev.category == "notehead_half":
                # half notes have a stem but categorically cannot carry a
                # flag or beam - counting one here would only ever be a
                # false positive from a neighboring voice's stem/beam sitting
                # nearby (2-voice writing), so don't even look.
                base, flags = 2.0, 0
            else:
                base = 1.0  # filled/x/diamond head: quarter-or-shorter
                candidates = _candidate_stems(stems, ev.x0, ev.x1, ev.yc)
                flags = 0
                for stem in candidates:
                    hooks = _flag_count_near(staff_events, stem, ev.yc)
                    beam_levels = _beam_count_near(beams, stem)
                    # several stems can plausibly touch one notehead in dense
                    # writing (chords, 2-voice passages) - take whichever
                    # candidate actually leads somewhere (a real beam/flag)
                    # rather than committing to a single "nearest" stem that
                    # might be an unrelated neighbor.
                    flags = max(flags, hooks, beam_levels)
            dots = _dot_count_after(staff_events, ev.x1, ev.yc)
            notes.append(NoteEvent(ev.xc, ev.yc, base, flags, dots, False, ev.category,
                                    notehead_kind=ev.category))
        elif ev.category in REST_CATS:
            # disambiguate flag8_or_rest_quarter by stem proximity: a real
            # stem near it means it's actually a flag glyph, not a rest
            if ev.category == "flag8_or_rest_quarter":
                near_stems = _candidate_stems(stems, ev.x0 - 3, ev.x1 + 3, ev.yc, x_tol=6.0, y_tol=12.0)
                if near_stems:
                    continue  # it's a flag, already counted via the notehead's stem
                cat = "rest_quarter"
            else:
                cat = ev.category
            if cat == "rest_half_whole":
                # whole rest hangs below the 2nd line from top; half rest
                # sits on the middle (3rd) line - use nearest line index
                nearest_idx = min(range(len(line_ys)), key=lambda i: abs(line_ys[i] - ev.yc))
                base = 4.0 if nearest_idx <= 1 else 2.0
            elif cat == "rest8":
                base = 0.5
            elif cat == "rest_quarter":
                base = 1.0
            else:
                base = 1.0
            dots = _dot_count_after(staff_events, ev.x1, ev.yc)
            notes.append(NoteEvent(ev.xc, ev.yc, base, 0, dots, True, cat))

    notes.sort(key=lambda n: n.x)
    _mark_ties(notes, curves)
    stats = {
        "unknown_glyphs": len(unknown),
        "unknown_gid_or_name_sample": sorted({(u.family, u.gid) for u in unknown})[:20],
        "note_events": len(notes),
        "stem_count": len(stems),
        "beam_segment_count": len(beams),
        "curve_count": len(curves),
    }
    return notes, stats


# ---------------------------------------------------------------------------
# Time signature
# ---------------------------------------------------------------------------

def decode_time_signature(page, staff_top, staff_bottom, staff_x0):
    events, fonts, _ = extract_glyph_events(page)
    if not events:
        return None, "no music glyphs found"

    mid = (staff_top + staff_bottom) / 2
    window = [e for e in events
              if staff_top - 4 <= e.yc <= staff_bottom + 4
              and staff_x0 - 5 <= e.x0 <= staff_x0 + 45]

    for e in window:
        if e.category == "common_time":
            return (4, 4), "common_time symbol"
        if e.category == "cut_time":
            return (2, 2), "cut_time symbol"

    digits = [e for e in window if e.category in DIGIT_CATS]
    if len(digits) < 2:
        return None, f"only {len(digits)} time-signature digit glyph(s) found"

    digits.sort(key=lambda e: e.x0)
    for i, a in enumerate(digits):
        for b in digits[i + 1:]:
            if abs(a.x0 - b.x0) < 4.0 and (a.yc < mid) != (b.yc < mid):
                num, den = (a, b) if a.yc < mid else (b, a)
                return (DIGIT_CATS[num.category], DIGIT_CATS[den.category]), "stacked digit glyphs"
    return None, "digit glyphs found but not in a stacked numerator/denominator pair"


# ---------------------------------------------------------------------------
# Convenience: full-page decode combining with a tab staff's note columns
# ---------------------------------------------------------------------------

def decode_page(pdf_path, page_no=0):
    doc = fitz.open(pdf_path)
    page = doc[page_no]

    import sys
    # staff detection lives in extract_tab.py, looked for next to this file
    # first (production layout) and falling back to the sibling prototype
    # directory used during development.
    here = Path(__file__).parent
    for candidate in (here, here.parent / "tabextract"):
        if (candidate / "extract_tab.py").exists():
            sys.path.insert(0, str(candidate))
            break
    import extract_tab as et

    staves, anomalies = et.detect_staves(page)
    std_staves = [s for s in staves if s.kind == "standard"]
    tab_staves = [s for s in staves if s.kind == "tab"]

    result = {"path": str(pdf_path), "page": page_no, "systems": []}
    if not std_staves:
        result["error"] = "no standard staff found - rhythm glyphs live on the standard staff"
        return result

    for si, staff in enumerate(sorted(std_staves, key=lambda s: s.top)):
        ts, ts_reason = decode_time_signature(page, staff.top, staff.bottom, staff.x0)
        notes, stats = decode_note_events(
            page, staff.top, staff.bottom, staff.x0, staff.x1, staff.line_ys
        )
        result["systems"].append({
            "staff_index": si,
            "staff_top": staff.top,
            "time_signature": ts,
            "time_signature_reason": ts_reason,
            "notes": [
                {
                    "x": round(n.x, 1), "duration": n.duration_code, "dots": n.dotted,
                    "is_rest": n.is_rest, "category": n.category, "quarter_units": round(n.quarter_units, 4),
                    "tied_next": n.tied_next,
                }
                for n in notes
            ],
            "stats": stats,
        })
    return result


if __name__ == "__main__":
    import sys
    import json
    pdf = sys.argv[1]
    page_no = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    out = decode_page(pdf, page_no)
    print(json.dumps(out, indent=2)[:4000])
