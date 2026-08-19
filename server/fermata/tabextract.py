"""Guitar tablature extraction from vector-engraved PDF scores.

Staff systems are located from vector line primitives (page.get_drawings()):
a run of 6 evenly spaced long horizontal lines is a tab staff, 5 is a
standard staff. Fret numbers come from text spans that are pure digits,
assigned to a tab staff and string by proximity - font name is deliberately
not used as a filter, since different engravers (Finale, Sibelius, Opus)
pick different fonts for fret numbers. Barlines come from vertical line
primitives spanning a staff's height.

Rhythm and time signature: when a tab staff is paired with a standard-
notation staff above it (score+tab layout) drawn in a music font
glyph_rhythm knows how to read, durations and the time signature are decoded
directly from the engraved notehead/stem/flag/beam/dot and digit glyphs
(see glyph_rhythm.py) - not guessed. That covers the common case (Finale
Maestro and Sibelius Opus exports, the bulk of the library).

Where glyph decoding isn't possible - a raster page, a CFF-flavor font
embedding, an unrecognised font family, or a tab staff with no standard
staff above it - rhythm falls back to a weaker heuristic: durations inferred
from the horizontal spacing between note columns, normalized to a measure's
quarter-note budget and snapped to the nearest plain duration (no dotted
notes or ties modeled), and the time signature falls back to a best-effort
scan for stacked plain-text digits that frequently fails outright. Which
path was used, and any resulting gaps, are surfaced through
ExtractionResult.warnings and .confidence rather than papered over; callers
can also offer a manual time-signature override (see extract()'s
time_signature argument) for when auto-detection comes up empty.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from . import glyph_rhythm as glyph

DEFAULT_TUNING = ["E2", "A2", "D3", "G3", "B3", "E4"]
DROP_D_TUNING = ["D2", "A2", "D3", "G3", "B3", "E4"]

# Plain (non-dotted) duration budgets, in quarter-note units, used to snap
# spacing-derived durations to an alphaTex duration code.
_PLAIN_DURATIONS = [(4.0, 1), (2.0, 2), (1.0, 4), (0.5, 8), (0.25, 16), (0.125, 32)]


@dataclass
class ExtractionResult:
    extractable: bool
    reason: str | None = None
    alphatex: str | None = None
    title: str | None = None
    tempo: int | None = None
    tuning: list[str] = field(default_factory=lambda: list(DEFAULT_TUNING))
    tuning_label: str | None = None
    time_signature: tuple[int, int] | None = None
    time_signature_source: str = "not detected"
    bars: int = 0
    beats: int = 0
    notes: int = 0
    # Total tab / standard staff systems found across the whole document
    # (summed across pages) - same definition analyze() uses, not a
    # per-page maximum.
    tab_staff_count: int = 0
    standard_staff_count: int = 0
    pages_processed: int = 0
    confidence: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "extractable": self.extractable,
            "reason": self.reason,
            "alphatex": self.alphatex,
            "title": self.title,
            "tempo": self.tempo,
            "tuning": self.tuning,
            "tuning_label": self.tuning_label,
            "time_signature": list(self.time_signature) if self.time_signature else None,
            "time_signature_source": self.time_signature_source,
            "bars": self.bars,
            "beats": self.beats,
            "notes": self.notes,
            "tab_staff_count": self.tab_staff_count,
            "standard_staff_count": self.standard_staff_count,
            "pages_processed": self.pages_processed,
            "confidence": self.confidence,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Staff detection
# ---------------------------------------------------------------------------


class _Staff:
    def __init__(self, kind, line_ys, x0, x1):
        self.kind = kind  # "tab" (6 lines) or "standard" (5 lines)
        self.line_ys = line_ys  # sorted top->bottom
        self.x0 = x0
        self.x1 = x1

    @property
    def top(self):
        return self.line_ys[0]

    @property
    def bottom(self):
        return self.line_ys[-1]

    @property
    def spacing(self):
        return (self.bottom - self.top) / (len(self.line_ys) - 1)

    def string_for_y(self, y):
        """Nearest line index -> string number (1 = top line = high string)."""
        best_i, best_d = 0, abs(y - self.line_ys[0])
        for i, ly in enumerate(self.line_ys):
            d = abs(y - ly)
            if d < best_d:
                best_i, best_d = i, d
        return best_i + 1


def _long_horizontal_segments(page, min_len_ratio=0.25):
    """Near-horizontal vector primitives long enough to plausibly be staff
    lines, as opposed to beams, ledger lines, or stems."""
    min_len = page.rect.width * min_len_ratio
    segs = []
    for d in page.get_drawings():
        for item in d.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) < 0.08 and abs(p1.x - p2.x) >= min_len:
                    y = (p1.y + p2.y) / 2
                    segs.append((y, min(p1.x, p2.x), max(p1.x, p2.x)))
            elif item[0] == "re":
                r = item[1]
                if r.height < 1.0 and r.width >= min_len:
                    y = (r.y0 + r.y1) / 2
                    segs.append((y, r.x0, r.x1))
    return segs


def _detect_staves(page):
    """Cluster long horizontal line primitives into staff systems.

    Returns (staves, anomalies): anomalies records line-groups whose size was
    neither 5 nor 6, so callers can surface what was thrown away.
    """
    segs = _long_horizontal_segments(page)
    if not segs:
        return [], []

    by_y = {}
    for y, x0, x1 in segs:
        key = round(y, 1)
        if key not in by_y:
            by_y[key] = [x0, x1]
        else:
            by_y[key][0] = min(by_y[key][0], x0)
            by_y[key][1] = max(by_y[key][1], x1)
    ys = sorted(by_y.keys())

    clusters = []
    cur = [ys[0]]
    for prev, y in zip(ys, ys[1:]):
        if (y - prev) > 15.0:
            clusters.append(cur)
            cur = [y]
        else:
            cur.append(y)
    clusters.append(cur)

    staves = []
    anomalies = []
    for c in clusters:
        n = len(c)
        x0 = min(by_y[y][0] for y in c)
        x1 = max(by_y[y][1] for y in c)
        if n == 6:
            staves.append(_Staff("tab", c, x0, x1))
        elif n == 5:
            staves.append(_Staff("standard", c, x0, x1))
        else:
            anomalies.append({"line_count": n, "ys": c, "x0": x0, "x1": x1})
    return staves, anomalies


def _vertical_segments(page, min_len=15.0):
    segs = []
    for d in page.get_drawings():
        for item in d.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.x - p2.x) < 0.08 and abs(p1.y - p2.y) >= min_len:
                    x = (p1.x + p2.x) / 2
                    segs.append((x, min(p1.y, p2.y), max(p1.y, p2.y)))
            elif item[0] == "re":
                r = item[1]
                if r.width < 1.0 and r.height >= min_len:
                    x = (r.x0 + r.x1) / 2
                    segs.append((x, r.y0, r.y1))
    return segs


def _detect_barlines(segs, staff):
    """Vertical segments whose y-span covers most of this staff's height.

    `segs` is the page's full set of vertical line primitives (see
    _vertical_segments) - callers must compute it once per page and reuse it
    across staves. get_drawings() re-parses the page's whole content stream,
    so calling _vertical_segments(page) once per staff here made a 2-page,
    ~7-staves-per-page file re-parse the same page content ~14 times inside
    a single synchronous request.
    """
    xs = []
    span = staff.bottom - staff.top
    for x, y0, y1 in segs:
        if y0 <= staff.top + span * 0.3 and y1 >= staff.bottom - span * 0.3:
            if staff.x0 - 2 <= x <= staff.x1 + 2:
                xs.append(round(x, 1))
    xs = sorted(set(xs))
    merged = []
    for x in xs:
        if merged and x - merged[-1] < 2.0:
            continue
        merged.append(x)
    return merged


# ---------------------------------------------------------------------------
# Digit (fret number) extraction
# ---------------------------------------------------------------------------


class _DigitToken:
    __slots__ = ("text", "bbox", "font", "size")

    def __init__(self, text, bbox, font, size):
        self.text = text
        self.bbox = bbox  # (x0, y0, x1, y1)
        self.font = font
        self.size = size

    @property
    def x0(self):
        return self.bbox[0]

    @property
    def x1(self):
        return self.bbox[2]

    @property
    def yc(self):
        return (self.bbox[1] + self.bbox[3]) / 2

    @property
    def width(self):
        return self.bbox[2] - self.bbox[0]


def _extract_digit_tokens(page):
    """All spans that are purely ASCII digits, 1-2 chars, any font. Font name
    is deliberately not used as a filter - position relative to a detected
    tab staff is what identifies a fret number, not the exporter's font
    choice for it."""
    tokens = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if text.isdigit() and 1 <= len(text) <= 2:
                    tokens.append(_DigitToken(text, span["bbox"], span.get("font"), span.get("size")))
    return tokens


def _assign_tokens_to_tab_staves(tokens, tab_staves):
    """Return ({staff_index: [tokens]}, unmatched_tokens)."""
    by_staff = collections.defaultdict(list)
    unmatched = []
    for tok in tokens:
        best = None
        best_d = None
        for i, st in enumerate(tab_staves):
            pad = st.spacing * 0.75
            if st.top - pad <= tok.yc <= st.bottom + pad and st.x0 - 5 <= tok.x0 <= st.x1 + 5:
                center = (st.top + st.bottom) / 2
                d = abs(tok.yc - center)
                if best is None or d < best_d:
                    best, best_d = i, d
        if best is None:
            unmatched.append(tok)
        else:
            by_staff[best].append(tok)
    return by_staff, unmatched


# No standard guitar has more frets than this; a merge result above it is a
# sign two unrelated notes were concatenated, not a real fret number.
_MAX_SANE_FRET = 24


def _merge_multidigit(tokens_for_staff, staff):
    """Merge adjacent 1-digit tokens on the same string line into 2-digit
    fret numbers (e.g. "1" then "2" immediately right -> "12").

    Returns (merged_notes, rejected_count, suspicious_count):
    - rejected_count is how many candidate merges were declined because the
      result exceeded _MAX_SANE_FRET (kept as two separate notes instead).
    - suspicious_count is how many notes - merged or original two-character
      spans straight from the PDF text (e.g. Finale can emit a two-digit
      fret as a single span, and occasionally two adjacent single-digit
      frets end up close enough to read as one) - are still above
      _MAX_SANE_FRET and were emitted as-is because there's no safe way to
      split an already-single span back into two notes.
    Callers should surface both as warnings rather than silently trusting
    an impossible fret number.
    """
    per_string = collections.defaultdict(list)
    for tok in tokens_for_staff:
        s = staff.string_for_y(tok.yc)
        per_string[s].append(tok)

    merged_notes = []  # (x0, string, fret_text, yc)
    rejected = 0
    suspicious = 0
    for s, toks in per_string.items():
        toks.sort(key=lambda t: t.x0)
        i = 0
        # Only 1-char tokens are merge candidates; averaging in already-merged
        # 2-char token widths inflated the window enough that two separate
        # adjacent single-digit notes (e.g. a "5" then, well clear of it, a
        # "7") could be pulled together into a nonsense fret like "57".
        single_widths = [t.width for t in toks if len(t.text) == 1]
        avg_w = sum(single_widths) / len(single_widths) if single_widths else 5.0
        while i < len(toks):
            t = toks[i]
            if (
                len(t.text) == 1
                and i + 1 < len(toks)
                and len(toks[i + 1].text) == 1
                and (toks[i + 1].x0 - t.x1) < avg_w * 0.35
                and abs(toks[i + 1].yc - t.yc) < 1.5
            ):
                nxt = toks[i + 1]
                fret_text = t.text + nxt.text
                if int(fret_text) > _MAX_SANE_FRET:
                    rejected += 1
                    merged_notes.append((t.x0, s, t.text, t.yc))
                    i += 1
                else:
                    merged_notes.append((t.x0, s, fret_text, (t.yc + nxt.yc) / 2))
                    i += 2
            else:
                if len(t.text) == 2 and int(t.text) > _MAX_SANE_FRET:
                    suspicious += 1
                merged_notes.append((t.x0, s, t.text, t.yc))
                i += 1
    merged_notes.sort(key=lambda n: n[0])
    return merged_notes, rejected, suspicious


# ---------------------------------------------------------------------------
# Column / chord grouping
# ---------------------------------------------------------------------------


def _group_into_columns(notes, x_tol=1.5, wide_chord_ratio=0.35):
    """notes: list of (x0, string, fret_text, yc) sorted by x0.
    Returns [{"x": float, "notes": [(string, fret_text), ...]}].

    Two passes: tight x-proximity clustering catches chords engraved at
    exactly the same column; a second pass merges adjacent columns whose gap
    is small relative to the local column spacing, since engravers commonly
    offset a bass tab number a couple points right of a treble number in the
    same chord to keep both legible.
    """
    columns = []
    for x0, s, fret, yc in notes:
        if columns and (x0 - columns[-1]["x"]) < x_tol:
            columns[-1]["notes"].append((s, fret))
            columns[-1]["x"] = min(columns[-1]["x"], x0)
        else:
            columns.append({"x": x0, "notes": [(s, fret)]})

    if len(columns) > 2:
        gaps = [b["x"] - a["x"] for a, b in zip(columns, columns[1:])]
        median_gap = sorted(gaps)[len(gaps) // 2]
        merged = [columns[0]]
        for col, gap in zip(columns[1:], gaps):
            used_strings = {s for s, _ in merged[-1]["notes"]}
            new_strings = {s for s, _ in col["notes"]}
            if (
                median_gap > 0
                and gap < median_gap * wide_chord_ratio
                and not (used_strings & new_strings)
            ):
                merged[-1]["notes"].extend(col["notes"])
            else:
                merged.append(col)
        columns = merged

    for col in columns:
        seen = set()
        deduped = []
        for s, fret in col["notes"]:
            if s in seen:
                continue
            seen.add(s)
            deduped.append((s, fret))
        col["notes"] = sorted(deduped, key=lambda n: n[0])
    return columns


# ---------------------------------------------------------------------------
# Time signature (best-effort)
# ---------------------------------------------------------------------------


def _detect_time_signature(page, standard_staff):
    """Look for two stacked plain digits near the start of a standard staff.
    Frequently fails - see module docstring - callers should treat a None
    result as normal, not an error."""
    d = page.get_text("dict")
    candidates = []
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if text.isdigit() and len(text) == 1:
                    bbox = span["bbox"]
                    yc = (bbox[1] + bbox[3]) / 2
                    x0 = bbox[0]
                    if (
                        standard_staff.top - 2 <= yc <= standard_staff.bottom + 2
                        and standard_staff.x0 - 5 <= x0 <= standard_staff.x0 + 40
                    ):
                        candidates.append((x0, yc, text))
    if len(candidates) < 2:
        return None
    candidates.sort(key=lambda c: c[0])
    mid = (standard_staff.top + standard_staff.bottom) / 2
    for i in range(len(candidates)):
        for j in range(len(candidates)):
            if i == j:
                continue
            x0a, ya, ta = candidates[i]
            x0b, yb, tb = candidates[j]
            if abs(x0a - x0b) < 3.0 and ya < mid < yb:
                return int(ta), int(tb)
    return None


# ---------------------------------------------------------------------------
# Rhythm inference (heuristic, low confidence - see module docstring)
# ---------------------------------------------------------------------------


def _snap_duration(quarter_units):
    """Snap a duration in quarter-note units to the nearest alphaTex
    duration code, ignoring dotted values (not modeled)."""
    best = min(_PLAIN_DURATIONS, key=lambda p: abs(p[0] - quarter_units))
    return best[1]


def _measure_quarter_length(ts: tuple[int, int]) -> float:
    """Quarter-note budget for one measure of this time signature, e.g. 3/4
    and 6/8 both budget 3.0 quarters. The denominator matters: using the
    numerator alone would give 6/8 a budget of 6.0, doubling every
    spacing-inferred duration in a compound meter."""
    return ts[0] * 4.0 / ts[1]


def _infer_measure_rhythm(columns_in_measure, measure_quarter_len, bar_end_x):
    """Treat the x-gap from each column to the next - and from the last
    column to the measure's own barline (bar_end_x) - as proportional to
    that column's duration, normalized so gaps sum to measure_quarter_len,
    then snapped per-column. Not a real rhythm decoder - see module
    docstring.

    bar_end_x must be the actual barline (or staff end) position for this
    measure: using the mean of the preceding gaps instead systematically
    shortened the last note of any bar that ends on a long note.
    """
    if not columns_in_measure:
        return []
    xs = [c["x"] for c in columns_in_measure]
    if len(xs) == 1:
        gaps = [measure_quarter_len]
    else:
        gaps = [b - a for a, b in zip(xs, xs[1:])]
        gaps.append(bar_end_x - xs[-1])
    total = sum(gaps)
    if total <= 0:
        return [measure_quarter_len / len(xs)] * len(xs)
    return [g / total * measure_quarter_len for g in gaps]


# ---------------------------------------------------------------------------
# Rhythm decode from glyphs (high confidence - see glyph_rhythm.py)
# ---------------------------------------------------------------------------


def _pair_standard_staff(tab_staff, std_staves):
    """The standard-notation staff for a tab staff is the nearest standard
    staff directly above it (score+tab layouts always draw notation above
    its own tab line)."""
    above = [s for s in std_staves if s.bottom <= tab_staff.top + 2]
    if not above:
        return None
    return min(above, key=lambda s: tab_staff.top - s.bottom)


def _cluster_pitched_glyph_events(events, cluster_x_tol=1.5):
    """Group glyph-decoded pitched note events sharing (almost) the same x
    into one cluster - the members of a chord (chord noteheads land at
    identical x, a beat apart from neighbors by a full note-spacing), or of
    two overlapping voices notated at the same onset. A cluster becomes ONE
    beat, not one beat per member: matching every member independently
    against tab columns let a chord's later noteheads each go hunting for
    their own "nearest unused column" once the first member had already
    claimed the right one, silently stealing a neighboring beat's column in
    a dense passage."""
    events = sorted(events, key=lambda n: n.x)
    clusters = []
    for ev in events:
        if clusters and abs(ev.x - clusters[-1][0].x) <= cluster_x_tol:
            clusters[-1].append(ev)
        else:
            clusters.append([ev])
    return clusters


def _cluster_glyph_duration(cluster):
    """Pick one representative (duration_code, dots) for a cluster. Members
    usually agree (chord noteheads share one stem, so one duration); when
    they genuinely don't (overlapping voices with different notated values
    at the same onset), take the most common reading, breaking ties toward
    the longer value - true polyphony isn't modeled here (one beat, one
    duration), so when two voices genuinely disagree something is picked
    rather than fragmenting the beat."""
    counts = collections.Counter((ev.duration_code, ev.dotted) for ev in cluster)
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0][0]))[0]


def _build_measure_beats_glyph(m_cols, m_lo, m_hi, note_events, x_tol):
    """Build one measure's beats from glyph-decoded note/rest events on the
    tab staff's paired standard-notation staff, matching each event to the
    tab column at (approximately) the same x. Every rest becomes its own
    beat with no tab column consumed; a tab column with no matching glyph
    event within x_tol falls back to an eighth-note placeholder (counted in
    the returned unmatched-column total rather than silently trusted - see
    _extract's unmatched-column warning).

    Returns (beats, unmatched_columns, unmatched_glyph_notes) where beats is
    a list of (duration_code, dots, notes) triples in x order, ready for
    _fmt_beat; unmatched_columns is how many tab columns had no glyph note
    within x_tol; unmatched_glyph_notes is how many pitched glyph notes had
    no tab column to match (expected to be rare - every played tab note
    should have a fret number).
    """
    measure_events = [n for n in note_events if m_lo <= n.x < m_hi]
    rest_events = [n for n in measure_events if n.is_rest]
    pitched_clusters = _cluster_pitched_glyph_events([n for n in measure_events if not n.is_rest])

    cols_sorted = sorted(m_cols, key=lambda c: c["x"])
    used = [False] * len(cols_sorted)

    tagged = []  # (x, duration_code, dots, notes)
    unmatched_glyph_notes = 0

    for ev in rest_events:
        tagged.append((ev.x, ev.duration_code, ev.dotted, []))

    for cluster in pitched_clusters:
        cx = sum(ev.x for ev in cluster) / len(cluster)
        code, dots = _cluster_glyph_duration(cluster)
        best_i, best_d = None, None
        for i, col in enumerate(cols_sorted):
            if used[i]:
                continue
            d = abs(col["xc"] - cx)
            if d <= x_tol and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is None:
            # a pitched glyph cluster with no matching tab digit column - can
            # happen on decode noise; nothing to emit a fret number for, so
            # skip rather than fabricate one.
            unmatched_glyph_notes += len(cluster)
            continue
        used[best_i] = True
        tagged.append((cx, code, dots, cols_sorted[best_i]["notes"]))

    unmatched_columns = 0
    for i, col in enumerate(cols_sorted):
        if not used[i]:
            unmatched_columns += 1
            tagged.append((col["x"], 8, 0, col["notes"]))  # conservative fallback

    tagged.sort(key=lambda t: t[0])
    return [(code, dots, notes) for _, code, dots, notes in tagged], unmatched_columns, unmatched_glyph_notes


# ---------------------------------------------------------------------------
# alphaTex emission
# ---------------------------------------------------------------------------


def _fmt_note(string, fret):
    return f"{fret}.{string}"


def _fmt_beat(duration_code, dots, notes):
    # An empty notes list marks a rest beat - alphaTex spells one as the
    # bare identifier "r". A dotted duration is NOT valid alphaTex as a
    # trailing dot on the duration code (":8." / ":8.." fail to parse) - it
    # is a beat effect appended to the note/chord body instead
    # (":8 3.4{d}", or with a chord ":2 (3.4 5.3){d}").
    if not notes:
        body = "r"
    else:
        body = (
            " ".join(_fmt_note(s, f) for s, f in notes)
            if len(notes) == 1
            else "(" + " ".join(_fmt_note(s, f) for s, f in notes) + ")"
        )
    dot_effect = "{d}" if dots == 1 else "{dd}" if dots == 2 else ""
    return f":{duration_code} {body}{dot_effect}"


def _escape_tex_string(s: str) -> str:
    """Escape a value for embedding inside an alphaTex quoted string
    (backslash then double-quote, in that order so a literal backslash
    isn't doubled by the quote-escaping pass)."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _build_alphatex(title, tempo, tuning, ts, measures):
    """measures: list of list of (duration_code, dots, notes) beats."""
    lines = [f'\\title "{_escape_tex_string(title)}"']
    if tempo:
        lines.append(f"\\tempo {tempo}")
    if ts:
        lines.append(f"\\ts {ts[0]} {ts[1]}")
    if tuning:
        # alphaTex binds the FIRST \tuning entry to string 1, and
        # _Staff.string_for_y assigns string 1 to the top tab line (the
        # highest-pitched string). `tuning`/DEFAULT_TUNING/DROP_D_TUNING are
        # kept low-to-high (index 0 = lowest string) everywhere else in this
        # module and in the API response, so they must be reversed here -
        # emitting them as-is puts every note on its mirrored string.
        lines.append("\\tuning " + " ".join(reversed(tuning)))
    lines.append(".")
    body_lines = []
    for measure in measures:
        beats = " ".join(_fmt_beat(dur, dots, notes) for dur, dots, notes in measure)
        body_lines.append(beats + " |")
    lines.append("\n".join(body_lines))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _page_is_raster(page) -> bool:
    return not page.get_fonts(full=True) and not page.get_drawings() and not page.get_text("text").strip()


def analyze(pdf_path) -> dict:
    """Cheap triage: is this PDF vector or raster, how many tab/notation
    staves does it have, is tab extraction worth attempting. Never raises -
    a malformed PDF comes back as extractable: false with a reason."""
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return {
            "extractable": False,
            "reason": f"could not open pdf: {exc}",
            "vector": False,
            "tab_staff_count": 0,
            "standard_staff_count": 0,
            "page_count": 0,
        }
    try:
        if doc.page_count == 0:
            return {
                "extractable": False,
                "reason": "pdf has no pages",
                "vector": False,
                "tab_staff_count": 0,
                "standard_staff_count": 0,
                "page_count": 0,
            }
        vector_pages = 0
        tab_total = 0
        std_total = 0
        for page in doc:
            if _page_is_raster(page):
                continue
            vector_pages += 1
            staves, _ = _detect_staves(page)
            tab_total += sum(1 for s in staves if s.kind == "tab")
            std_total += sum(1 for s in staves if s.kind == "standard")
        if vector_pages == 0:
            return {
                "extractable": False,
                "reason": "no fonts, no vector drawings, no text on any page - pdf is a raster scan",
                "vector": False,
                "tab_staff_count": 0,
                "standard_staff_count": 0,
                "page_count": doc.page_count,
            }
        extractable = tab_total > 0
        reason = None
        if not extractable:
            reason = (
                "no 6-line tab staff groups found - pages are vector but appear to be "
                "standard-notation only (fingering numbers are not fret numbers)"
            )
        return {
            "extractable": extractable,
            "reason": reason,
            "vector": True,
            "tab_staff_count": tab_total,
            "standard_staff_count": std_total,
            "page_count": doc.page_count,
        }
    except Exception as exc:
        return {
            "extractable": False,
            "reason": f"analysis failed: {exc}",
            "vector": False,
            "tab_staff_count": 0,
            "standard_staff_count": 0,
            "page_count": 0,
        }
    finally:
        doc.close()


def extract(pdf_path, time_signature: tuple[int, int] | None = None) -> ExtractionResult:
    """Full extraction: returns alphaTex plus bars/beats/notes, tuning,
    per-section confidence, and an explicit list of warnings. Never raises -
    a malformed PDF or one with no tab staves comes back as
    extractable: false with a reason, not an exception.

    time_signature lets a caller supply the numerator/denominator by hand,
    since auto-detection frequently fails (see module docstring).
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return ExtractionResult(extractable=False, reason=f"could not open pdf: {exc}")
    try:
        return _extract(doc, pdf_path, time_signature)
    except Exception as exc:
        return ExtractionResult(extractable=False, reason=f"extraction failed: {exc}")
    finally:
        doc.close()


def _extract(doc, pdf_path, time_signature: tuple[int, int] | None) -> ExtractionResult:
    if doc.page_count == 0:
        return ExtractionResult(extractable=False, reason="pdf has no pages")

    warnings: list[str] = []
    ts = tuple(time_signature) if time_signature else None
    ts_source = "manual override" if ts else None
    tempo = None
    tuning = list(DEFAULT_TUNING)
    tuning_label = None
    # tab_staff_count / standard_staff_count are the total number of tab /
    # standard staff systems found across the whole document (summed across
    # pages), matching analyze()'s definition - see ExtractionResult.
    tab_count = 0
    std_count = 0
    vector_pages = 0
    pages_with_tab = []  # (page, tab_staves, std_staves) in page order

    # Pass 1: staff census plus tempo/tuning/time-signature hints, cheapest
    # first so we know up front whether there's anything worth extracting.
    for page_no in range(doc.page_count):
        page = doc[page_no]
        text = page.get_text("text")
        if not page.get_fonts(full=True) and not page.get_drawings() and not text.strip():
            warnings.append(f"page {page_no + 1} has no fonts, drawings, or text (raster scan) - skipped")
            continue
        vector_pages += 1

        staves, anomalies = _detect_staves(page)
        if anomalies:
            warnings.append(
                f"page {page_no + 1}: {len(anomalies)} staff-line group(s) with an unexpected "
                "line count were ignored"
            )
        tab_staves = sorted((s for s in staves if s.kind == "tab"), key=lambda s: s.top)
        std_staves = sorted((s for s in staves if s.kind == "standard"), key=lambda s: s.top)
        tab_count += len(tab_staves)
        std_count += len(std_staves)

        if ts is None and std_staves:
            # Try the glyph decoder first - it reads the actual printed
            # time-signature digits and is right whenever it succeeds at
            # all. Only fall back to the plain-text digit scan (which
            # frequently fails outright - see module docstring) when no
            # system on this page has decodable time-signature glyphs.
            for s in std_staves:
                glyph_ts, _reason = glyph.decode_time_signature(page, s.top, s.bottom, s.x0)
                if glyph_ts is not None:
                    ts = glyph_ts
                    ts_source = "glyph-decoded"
                    break
            if ts is None:
                detected = _detect_time_signature(page, std_staves[0])
                if detected:
                    ts = detected
                    ts_source = "auto-detected"

        if tempo is None:
            q_match = re.search(r"=\s*(\d{2,3})", text)
            if q_match:
                tempo = int(q_match.group(1))

        if tuning_label is None and "Drop D" in text:
            tuning_label = "Drop D"
            tuning = list(DROP_D_TUNING)

        if tab_staves:
            pages_with_tab.append((page, tab_staves, std_staves))

    if vector_pages == 0:
        return ExtractionResult(
            extractable=False,
            reason="no fonts, no vector drawings, no text on any page - pdf is a raster scan",
        )

    if not pages_with_tab:
        return ExtractionResult(
            extractable=False,
            reason=(
                "no 6-line tab staff groups found - pages are vector but appear to be "
                "standard-notation only (fingering numbers are not fret numbers)"
            ),
            tab_staff_count=tab_count,
            standard_staff_count=std_count,
            warnings=warnings,
        )

    if ts is None:
        ts = (4, 4)
        ts_source = "not detected (assumed 4/4)"
        warnings.append(
            "time signature not detected - glyphs live in a subsetted music font at remapped "
            "codepoints; assumed 4/4 for bar/beat grouping, pass time_signature to override"
        )

    measure_quarter_len = _measure_quarter_length(ts)

    # Pass 2: real extraction, now that ts/tempo/tuning are settled.
    all_measures = []  # list of measures, each a list of (duration_code, dots, notes) beats
    unmatched_total = 0
    rejected_merges_total = 0
    suspicious_frets_total = 0
    # How many tab staves' rhythm came from the glyph decoder vs. the
    # spacing heuristic - drives which rhythm warning/confidence gets
    # reported below, so it reflects what was actually used, not a
    # blanket claim either way.
    staves_glyph_decoded = 0
    staves_fallback = 0
    unmatched_columns_glyph = 0
    unmatched_glyph_notes_total = 0
    for page, tab_staves, std_staves in pages_with_tab:
        tokens = _extract_digit_tokens(page)
        by_staff, unmatched = _assign_tokens_to_tab_staves(tokens, tab_staves)
        unmatched_total += len(unmatched)
        # Computed once per page and reused for every staff on it - see
        # _detect_barlines docstring.
        vseg = _vertical_segments(page)
        # Glyph note events are decoded once per standard staff and reused
        # by every tab staff paired to it (a page can have more than one
        # tab staff per system, e.g. lead + bass).
        glyph_notes_by_std = {}
        for si, staff in enumerate(tab_staves):
            toks = by_staff.get(si, [])
            if not toks:
                continue
            notes, rejected, suspicious = _merge_multidigit(toks, staff)
            rejected_merges_total += rejected
            suspicious_frets_total += suspicious
            columns = _group_into_columns(notes)
            barline_xs = _detect_barlines(vseg, staff)
            col_xs = [c["x"] for c in columns]
            lo, hi = min(col_xs) - 5, max(col_xs) + 5
            bars = [x for x in barline_xs if lo <= x <= hi]
            bounds = sorted(set([staff.x0] + bars + [staff.x1]))

            measure_idx = 0
            measures_for_staff = [[] for _ in range(len(bounds) - 1)]
            for col in columns:
                while measure_idx < len(bounds) - 2 and col["x"] >= bounds[measure_idx + 1]:
                    measure_idx += 1
                measures_for_staff[measure_idx].append(col)

            std_staff = _pair_standard_staff(staff, std_staves)
            note_events = None
            if std_staff is not None:
                if id(std_staff) not in glyph_notes_by_std:
                    glyph_notes_by_std[id(std_staff)], _stats = glyph.decode_note_events(
                        page, std_staff.top, std_staff.bottom, std_staff.x0, std_staff.x1, std_staff.line_ys
                    )
                note_events = glyph_notes_by_std[id(std_staff)]

            # Glyph decoding is only actually usable when it found real
            # events to work with - an empty list means no paired standard
            # staff, a raster page, a CFF-embedded music font (not covered
            # by glyph_rhythm), or a font family it doesn't have a glyph
            # map for. Any of those fall back to the spacing heuristic.
            glyph_ok = bool(note_events)
            if glyph_ok:
                staves_glyph_decoded += 1
                # col["x"] is a fret digit's LEFT edge; glyph note events'
                # x is the notehead bbox CENTER - comparing them directly
                # is a systematic offset of about half a digit's width,
                # enough to pick the wrong neighbor in a dense passage.
                # Approximate each column's center with this staff's own
                # measured average digit width.
                avg_digit_w = (sum(t.width for t in toks) / len(toks)) if toks else 5.0
                for col in columns:
                    col["xc"] = col["x"] + avg_digit_w / 2
                # Two staff-line-spacings is comfortably wider than normal
                # engraving jitter between a notehead and its tab digit,
                # but tight enough to actually reject a mismatch in a
                # dense passage.
                x_tol = staff.spacing * 2.5
            else:
                staves_fallback += 1

            for i, m_cols in enumerate(measures_for_staff):
                m_lo, m_hi = bounds[i], bounds[i + 1]
                if glyph_ok:
                    beats, unmatched_cols, unmatched_notes = _build_measure_beats_glyph(
                        m_cols, m_lo, m_hi, note_events, x_tol
                    )
                    unmatched_columns_glyph += unmatched_cols
                    unmatched_glyph_notes_total += unmatched_notes
                    if not beats:
                        # Nothing decoded and no tab columns either - still
                        # emit an explicit rest bar rather than dropping the
                        # measure (see the non-glyph branch below for why).
                        beats = [(_snap_duration(measure_quarter_len), 0, [])]
                    all_measures.append(beats)
                    continue
                if not m_cols:
                    # No digit columns landed in this bar - emit an explicit
                    # rest bar instead of dropping it. Skipping it entirely
                    # would omit its "|" separator and shift every later
                    # bar's number one position earlier than the PDF, which
                    # breaks side-by-side comparison against the original.
                    all_measures.append([(_snap_duration(measure_quarter_len), 0, [])])
                    continue
                durations_q = _infer_measure_rhythm(m_cols, measure_quarter_len, m_hi)
                beats = [(_snap_duration(dq), 0, col["notes"]) for col, dq in zip(m_cols, durations_q)]
                all_measures.append(beats)

    # Rhythm warning/confidence reflects what was actually used across the
    # document, not a blanket claim either way - see the counts above.
    if staves_glyph_decoded and not staves_fallback:
        warnings.append(
            "tuplets (triplets and similar) are not detected - a note written inside a tuplet "
            "will show its plain written duration rather than the shortened tuplet duration"
        )
        warnings.append(
            "tie detection is low confidence - some tied notes may show up as separately "
            "re-struck notes instead of one held note"
        )
    elif staves_glyph_decoded and staves_fallback:
        warnings.append(
            f"durations were read from the engraved notation for {staves_glyph_decoded} staff "
            f"system(s); {staves_fallback} staff system(s) had no matching notation staff to read "
            "and use a rougher estimate from note spacing instead - treat those sections as low "
            "confidence"
        )
        warnings.append(
            "tuplets (triplets and similar) are not detected in the sections read from notation - "
            "a note written inside a tuplet will show its plain written duration"
        )
        warnings.append(
            "tie detection is low confidence in the sections read from notation - some tied notes "
            "may show up as separately re-struck notes instead of one held note"
        )
    else:
        warnings.append(
            "note durations are inferred from horizontal spacing between columns, not decoded from "
            "the score - treat as low confidence (no dotted notes or ties modeled)"
        )

    if unmatched_columns_glyph:
        warnings.append(
            f"{unmatched_columns_glyph} fret number(s) could not be matched to a note in the "
            "engraved notation and got an estimated duration instead - treat those specific notes "
            "as low confidence"
        )
    if unmatched_glyph_notes_total:
        warnings.append(
            f"{unmatched_glyph_notes_total} note(s) read from the engraved notation had no "
            "matching fret number and were dropped from the tab"
        )
    if unmatched_total:
        warnings.append(
            f"{unmatched_total} digit token(s) near a tab staff could not be assigned to a string"
        )
    if rejected_merges_total:
        warnings.append(
            f"{rejected_merges_total} adjacent-digit merge(s) were rejected because they would "
            f"have produced a fret number above {_MAX_SANE_FRET} - kept as separate notes instead"
        )
    if suspicious_frets_total:
        warnings.append(
            f"{suspicious_frets_total} fret number(s) above {_MAX_SANE_FRET} were read directly "
            "from the PDF's own text (not from a merge) - likely two adjacent notes rendered as "
            "one text span in the source - treat those frets as low confidence"
        )

    if not all_measures:
        return ExtractionResult(
            extractable=False,
            reason=(
                "tab staff systems were found but no fret-number digits could be matched to a "
                "string - likely an outlined-text export where fret numbers are vector paths, "
                "not selectable text"
            ),
            tab_staff_count=tab_count,
            standard_staff_count=std_count,
            pages_processed=len(pages_with_tab),
            warnings=warnings,
        )

    title = Path(pdf_path).stem
    alphatex = _build_alphatex(title, tempo, tuning, ts, all_measures)
    beats_total = sum(len(m) for m in all_measures)
    notes_total = sum(len(notes) for m in all_measures for _, _, notes in m)

    if staves_glyph_decoded and not staves_fallback:
        rhythm_confidence = (
            "high - decoded directly from the notehead/stem/flag/beam/dot glyphs in the score's "
            "own engraving"
        )
    elif staves_glyph_decoded and staves_fallback:
        rhythm_confidence = (
            "mixed - decoded from the score's engraving where a standard-notation staff was "
            "paired with the tab staff; a low-confidence spacing estimate elsewhere"
        )
    else:
        rhythm_confidence = "low - inferred from note spacing only, no dotted notes or ties modeled"

    confidence = {
        "frets": "high - read directly from vector text spans positioned against detected tab staff lines",
        "rhythm": rhythm_confidence,
        "time_signature": {
            "manual override": "n/a - caller supplied",
            "glyph-decoded": "high - read directly from the time-signature digit glyphs printed on the score",
            "auto-detected": "medium - read from page text",
        }.get(ts_source, "low - not detected, assumed 4/4"),
    }

    return ExtractionResult(
        extractable=True,
        alphatex=alphatex,
        title=title,
        tempo=tempo,
        tuning=tuning,
        tuning_label=tuning_label,
        time_signature=ts,
        time_signature_source=ts_source,
        bars=len(all_measures),
        beats=beats_total,
        notes=notes_total,
        tab_staff_count=tab_count,
        standard_staff_count=std_count,
        pages_processed=len(pages_with_tab),
        confidence=confidence,
        warnings=warnings,
    )
