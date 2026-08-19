"""Guitar tablature extraction from vector-engraved PDF scores.

Staff systems are located from vector line primitives (page.get_drawings()):
a run of 6 evenly spaced long horizontal lines is a tab staff, 5 is a
standard staff. Fret numbers come from text spans that are pure digits,
assigned to a tab staff and string by proximity - font name is deliberately
not used as a filter, since different engravers (Finale, Sibelius, Opus)
pick different fonts for fret numbers. Barlines come from vertical line
primitives spanning a staff's height.

Rhythm and time signature: when a tab staff is paired with a standard-
notation staff above it IN ITS OWN SYSTEM (score+tab layout) drawn in a
music font glyph_rhythm knows how to read, durations and the time signature
are decoded directly from the engraved notehead/stem/flag/beam/dot and digit
glyphs (see glyph_rhythm.py) - not guessed. That covers the common case
(Finale Maestro and Sibelius Opus exports, the bulk of the library).

Every tab staff resolves to exactly one rhythm SOURCE (see the PROV_*
constants and _resolve_rhythm_source), and the document's rhythm warnings
and confidence are derived from the collected set of those in one place
(_rhythm_report). The source is the decoder only when it can be trusted:

  - the notation staff must be in the tab staff's own system, within a
    spacing-relative distance, over the same horizontal extent, and read by
    no other tab staff (_pair_standard_staves);
  - the music font must be a recognised family AND match its calibrated
    glyph fingerprint (glyph_rhythm.maestro_fingerprint_ok);
  - the decoder's own unknown-glyph stats must be low enough that its
    durations mean what they say (_UNKNOWN_RATIO_*).

Where any of that fails - a raster page, a CFF-flavor font embedding, an
unrecognised or un-fingerprinted font, an ambiguous or absent notation
staff, or a vocabulary the decoder was never calibrated for - rhythm falls
back to a weaker heuristic: durations inferred from the horizontal spacing
between note columns, normalized to a measure's quarter-note budget and
snapped to the nearest plain duration (no dotted notes or ties modeled), and
the time signature falls back to a best-effort scan for stacked plain-text
digits that frequently fails outright. An honest fallback that says so is
always preferred to plausible-looking wrong rhythm at high confidence.

Time signature is a TIMELINE, not one document-wide value: it is read at the
start of every notation system and carried forward, so a score that changes
meter part-way through (the library contains several) gets the change
emitted at the bar where it happens rather than having every later bar
measured against the opening meter. Any signature that reaches the emitted
\\ts is validated first - it is also what gets stored, and alphaTab throws
outright on something like `\\ts 3 12`.

Which path was used, and any resulting gaps, are surfaced through
ExtractionResult.warnings, .confidence and .rhythm_provenance rather than
papered over; callers can also offer a manual time-signature override (see
extract()'s time_signature argument) for when auto-detection comes up empty.

KNOWN LIMITATION, deliberately not modeled: two-voice writing is collapsed
into one voice (one onset, one duration - see _cluster_glyph_duration), so a
bar whose voices genuinely differ sums to more than its meter's budget. This
is the dominant residual error in the emitted rhythm and it is a modelling
gap, not a decode bug.
"""

from __future__ import annotations

import bisect
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
    # How many tab staves resolved to each rhythm source (see the PROV_*
    # constants). Reported so a caller can see the mix directly instead of
    # having to parse it back out of the confidence string.
    rhythm_provenance: dict = field(default_factory=dict)

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
            "rhythm_provenance": self.rhythm_provenance,
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
    for d in glyph.page_drawings(page):
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
    for d in glyph.page_drawings(page):
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


# A tab staff and the notation staff it belongs to are part of one SYSTEM.
# Measured on the library: the vertical gap from a notation staff's bottom
# line to its own tab staff's top line is 3.3-7.3 tab-staff line spacings
# (n=254, median 5.4), while the gap to the next system down is far larger.
# 12 spacings therefore separates "same system" from "different system" with
# roughly a 65% margin either side of anything observed.
_SYSTEM_GAP_SPACINGS = 12.0
# A notation staff and its tab staff are engraved over the same horizontal
# extent - the measured overlap is 100% of the narrower staff on every one of
# those 254 pairs. Requiring most of it rules out pairing across the columns
# of a multi-instrument layout.
_PAIR_MIN_X_OVERLAP = 0.6
# Two notation staves both plausibly above one tab staff (a grand staff, or a
# neighbouring system that slipped inside the ceiling) means the pairing is a
# guess. Anything closer than this ratio to the best candidate makes it
# ambiguous, and an ambiguous pairing degrades to the spacing heuristic
# instead of reading a different line's rhythm at "high confidence".
_PAIR_AMBIGUITY_RATIO = 1.5


def _group_systems(staves):
    """Cluster staves into systems by vertical gap, so a tab staff is only
    ever paired inside its own system.

    Returns a list of lists, each in top-to-bottom order. Pairing on
    "nearest standard staff above, at any distance" instead reaches across
    systems and across instruments: where a tab-only system sits below a
    notation-only one, it read the notation system's rhythm and x-matched it
    onto this staff's fret columns - phantom rests and all - and reported
    high confidence for it.
    """
    ordered = sorted(staves, key=lambda s: s.top)
    systems = []
    for st in ordered:
        if systems:
            prev = systems[-1][-1]
            limit = max(prev.spacing, st.spacing) * _SYSTEM_GAP_SPACINGS
            if st.top - prev.bottom <= limit:
                systems[-1].append(st)
                continue
        systems.append([st])
    return systems


def _x_overlap_ratio(a, b):
    overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    narrower = min(a.x1 - a.x0, b.x1 - b.x0)
    if narrower <= 0:
        return 0.0
    return max(0.0, overlap) / narrower


def _pair_standard_staves(staves):
    """Resolve which standard-notation staff each tab staff should read its
    rhythm from. Returns ({id(tab_staff): std_staff}, {id(tab_staff): reason})
    where a tab staff missing from the mapping has a reason explaining why -
    callers degrade those to the spacing heuristic.

    Pairing is EXCLUSIVE: a notation staff is read by at most one tab staff.
    Letting two tab staves share one (the lead+bass case) meant each built
    its measures from a fresh view of the same events, so every rest was
    emitted twice and each staff's pitched clusters went hunting through the
    other staff's fret columns - tagging bass columns with treble durations.
    The second tab staff has no notation of its own to read, and saying so is
    the honest answer.
    """
    pairs = {}
    reasons = {}
    for system in _group_systems(staves):
        tabs = [s for s in system if s.kind == "tab"]
        stds = [s for s in system if s.kind == "standard"]
        if not tabs:
            continue
        if not stds:
            for t in tabs:
                reasons[id(t)] = "no notation staff in this tab staff's own system"
            continue
        # Score every (tab, std) candidate once, then hand out notation
        # staves closest-pair-first so the assignment is exclusive.
        candidates = []
        for t in tabs:
            for s in stds:
                if s.bottom > t.top + max(t.spacing, s.spacing) * 0.5:
                    continue  # notation is engraved above its tab staff
                gap = t.top - s.bottom
                if gap > max(t.spacing, s.spacing) * _SYSTEM_GAP_SPACINGS:
                    continue
                if _x_overlap_ratio(t, s) < _PAIR_MIN_X_OVERLAP:
                    continue
                candidates.append((gap, t, s))
        candidates.sort(key=lambda c: c[0])
        per_tab = collections.defaultdict(list)
        for gap, t, s in candidates:
            per_tab[id(t)].append((gap, s))
        taken = set()
        for gap, t, s in candidates:
            if id(t) in pairs or id(t) in reasons:
                continue
            mine = per_tab[id(t)]
            if len(mine) > 1 and mine[1][0] <= mine[0][0] * _PAIR_AMBIGUITY_RATIO:
                reasons[id(t)] = (
                    "two notation staves are equally plausible for this tab staff - "
                    "pairing would be a guess"
                )
                continue
            if id(s) in taken:
                reasons[id(t)] = (
                    "the notation staff for this system is already read by another tab staff"
                )
                continue
            taken.add(id(s))
            pairs[id(t)] = s
        for t in tabs:
            if id(t) not in pairs and id(t) not in reasons:
                reasons[id(t)] = "no notation staff within reach in this tab staff's own system"
    return pairs, reasons


def _cluster_pitched_glyph_events(events, cluster_x_tol=1.5):
    """Group glyph-decoded note events sharing (almost) the same x into one
    cluster - the members of a chord (chord noteheads land at identical x, a
    beat apart from neighbors by a full note-spacing), or of two overlapping
    voices notated at the same onset. A cluster becomes ONE beat, not one
    beat per member: matching every member independently against tab columns
    let a chord's later noteheads each go hunting for their own "nearest
    unused column" once the first member had already claimed the right one,
    silently stealing a neighboring beat's column in a dense passage."""
    events = sorted(events, key=lambda n: n.x)
    clusters = []
    for ev in events:
        if clusters and abs(ev.x - clusters[-1][0].x) <= cluster_x_tol:
            clusters[-1].append(ev)
        else:
            clusters.append([ev])
    return clusters


# How close a rest has to sit to a pitched onset before it is taken to BE
# that onset's second voice rather than a beat of its own, in staff-line
# spacings. The library engraves consecutive sixteenths about 1.25 spacings
# apart, so this has to stay well under that to avoid swallowing a real
# separate rest; engraving jitter between a rest glyph's centre and a
# notehead's centre at the same onset is a fraction of a spacing.
_REST_ONSET_MERGE_SPACINGS = 0.8


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


def _build_measure_beats_glyph(m_cols, m_lo, m_hi, note_events, note_xs, x_tol, spacing):
    """Build one measure's beats from glyph-decoded note/rest events on the
    tab staff's paired standard-notation staff, matching each event to the
    tab column at (approximately) the same x. A tab column with no matching
    glyph event within x_tol falls back to an eighth-note placeholder
    (counted in the returned unmatched-column total rather than silently
    trusted - see _extract's unmatched-column warning).

    Rests are clustered by onset and dropped where a pitched onset already
    sits, because the library's core content is two-voice fingerstyle
    writing: a voice-2 quarter rest engraved under a voice-1 note is the
    same beat, not an extra one. Emitting every rest glyph as its own beat
    made a 3/4 bar hold `:4 r` plus three notes - four beats' worth - and
    shifted the whole bar's playback while still reporting high confidence.
    Simultaneous rests in two voices collapse to one beat for the same
    reason.

    `note_events` must be sorted by x and `note_xs` their x values, so each
    measure takes a bisect-bounded slice instead of rescanning the staff's
    whole event list (O(M*E) across a staff's measures).

    Returns (beats, unmatched_columns, unmatched_glyph_notes) where beats is
    a list of (duration_code, dots, notes) triples in x order, ready for
    _fmt_beat; unmatched_columns is how many tab columns had no glyph note
    within x_tol; unmatched_glyph_notes is how many pitched glyph notes had
    no tab column to match (expected to be rare - every played tab note
    should have a fret number).
    """
    lo_i = bisect.bisect_left(note_xs, m_lo)
    hi_i = bisect.bisect_left(note_xs, m_hi)
    measure_events = note_events[lo_i:hi_i]
    pitched_clusters = _cluster_pitched_glyph_events([n for n in measure_events if not n.is_rest])
    rest_clusters = _cluster_pitched_glyph_events([n for n in measure_events if n.is_rest])

    cols_sorted = sorted(m_cols, key=lambda c: c["x"])
    col_xcs = [c["xc"] for c in cols_sorted]
    used = [False] * len(cols_sorted)

    tagged = []  # (x, duration_code, dots, notes)
    unmatched_glyph_notes = 0

    pitched_xs = sorted(sum(ev.x for ev in c) / len(c) for c in pitched_clusters)
    merge_tol = spacing * _REST_ONSET_MERGE_SPACINGS
    for cluster in rest_clusters:
        rx = sum(ev.x for ev in cluster) / len(cluster)
        j = bisect.bisect_left(pitched_xs, rx)
        near = False
        for k in (j - 1, j):
            if 0 <= k < len(pitched_xs) and abs(pitched_xs[k] - rx) <= merge_tol:
                near = True
                break
        if near:
            continue  # a second voice's rest under a sounding note, not a beat
        # one beat per rest onset; pick the longest reading among voices so a
        # collapsed pair doesn't silently shorten the bar
        rep = min(cluster, key=lambda ev: (ev.duration_code, -ev.dotted))
        tagged.append((rx, rep.duration_code, rep.dotted, []))

    for cluster in pitched_clusters:
        cx = sum(ev.x for ev in cluster) / len(cluster)
        code, dots = _cluster_glyph_duration(cluster)
        lo = bisect.bisect_left(col_xcs, cx - x_tol)
        hi = bisect.bisect_right(col_xcs, cx + x_tol)
        best_i, best_d = None, None
        for i in range(lo, hi):
            if used[i]:
                continue
            d = abs(col_xcs[i] - cx)
            if best_d is None or d < best_d:
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
# Rhythm source resolution
# ---------------------------------------------------------------------------

# Where one tab staff's durations came from. Every document-level warning and
# the reported rhythm confidence are derived from the collected set of these
# in ONE place (see _rhythm_report), instead of a branch in the measure loop
# plus a warnings ladder plus a confidence ladder all kept mutually
# consistent by hand.
PROV_GLYPHS = "glyphs"
PROV_GLYPHS_DEGRADED = "glyphs-degraded"
PROV_SPACING = "spacing"

# The decoder reports how much of a staff's music-font text it could not
# classify. Acting on it matters because an unmapped flag or rest glyph does
# not fail loudly - it decodes as a systematically wrong duration while
# every other signal still looks healthy. Measured across the library: the
# unknown-glyph ratio is 0.0 for the 99th percentile of notation staves and
# peaks at 0.065, so these thresholds sit far above anything the calibrated
# vocabulary actually produces and only fire on genuinely foreign input.
_UNKNOWN_RATIO_FALLBACK = 0.35
_UNKNOWN_RATIO_WARN = 0.10


class _RhythmSource:
    """One tab staff's resolved rhythm source: where its durations come
    from, the decoded events if any, and what to tell the user about it."""

    __slots__ = ("provenance", "note_events", "note_xs", "detail", "stats")

    def __init__(self, provenance, note_events=None, detail=None, stats=None):
        self.provenance = provenance
        self.note_events = note_events or []
        self.note_xs = [n.x for n in self.note_events]
        self.detail = detail
        self.stats = stats or {}

    @property
    def uses_glyphs(self):
        return self.provenance in (PROV_GLYPHS, PROV_GLYPHS_DEGRADED)


def _resolve_rhythm_source(page, std_staff, pair_reason, decoded):
    """Decide how one tab staff's durations will be read, and say why.

    `decoded` memoises decode_note_events per notation staff. The decoder's
    own honesty stats gate the answer: a staff whose music-font text is
    mostly outside the calibrated vocabulary, or whose unrecognised glyphs
    sit exactly where flags attach, is degraded or dropped to the spacing
    heuristic rather than reported as high-confidence rhythm.
    """
    if std_staff is None:
        return _RhythmSource(PROV_SPACING, detail=pair_reason or "no paired notation staff")

    key = id(std_staff)
    if key not in decoded:
        decoded[key] = glyph.decode_note_events(
            page, std_staff.top, std_staff.bottom, std_staff.x0, std_staff.x1,
            std_staff.line_ys, std_staff.spacing,
        )
    note_events, stats = decoded[key]

    font_warnings = stats.get("font_warnings") or []
    if not note_events:
        detail = font_warnings[0] if font_warnings else (
            "no readable music-font note glyphs on the paired notation staff"
        )
        return _RhythmSource(PROV_SPACING, detail=detail, stats=stats)

    ratio = stats.get("unknown_ratio", 0.0)
    unknown = stats.get("unknown_glyphs", 0)
    at_flag = stats.get("unknown_at_flag_position", 0)
    sample = stats.get("unknown_gid_or_name_sample") or []

    if ratio > _UNKNOWN_RATIO_FALLBACK:
        return _RhythmSource(
            PROV_SPACING, note_events, stats=stats,
            detail=(
                f"{unknown} of {stats.get('band_glyphs', 0)} music glyphs on the paired "
                f"notation staff are outside this decoder's calibrated vocabulary "
                f"({ratio:.0%}) - durations from it cannot be trusted "
                f"(unrecognised glyphs: {sample[:6]})"
            ),
        )
    if at_flag or ratio > _UNKNOWN_RATIO_WARN:
        why = (
            f"{at_flag} unrecognised glyph(s) sit where a flag attaches to a stem"
            if at_flag else
            f"{unknown} of {stats.get('band_glyphs', 0)} music glyphs are unrecognised ({ratio:.0%})"
        )
        return _RhythmSource(
            PROV_GLYPHS_DEGRADED, note_events, stats=stats,
            detail=(
                f"{why} - some durations on this system may be wrong "
                f"(unrecognised glyphs: {sample[:6]})"
            ),
        )
    return _RhythmSource(PROV_GLYPHS, note_events, stats=stats)


_TUPLET_WARNING = (
    "tuplets (triplets and similar) are not detected - a note written inside a tuplet "
    "will show its plain written duration rather than the shortened tuplet duration"
)
_TIE_WARNING = (
    "tie detection is low confidence - some tied notes may show up as separately "
    "re-struck notes instead of one held note"
)


def _rhythm_report(counts, details):
    """Derive the document's rhythm warnings and confidence string from the
    collected per-staff provenances - the single place that decides both, so
    they cannot drift out of step with each other or with the measure loop.

    `counts` is a Counter over the PROV_* values; `details` maps each
    provenance to the distinct per-staff explanations collected for it.
    """
    glyphs = counts.get(PROV_GLYPHS, 0)
    degraded = counts.get(PROV_GLYPHS_DEGRADED, 0)
    spacing = counts.get(PROV_SPACING, 0)
    warnings = []

    if not glyphs and not degraded:
        warnings.append(
            "note durations are inferred from horizontal spacing between columns, not decoded from "
            "the score - treat as low confidence (no dotted notes or ties modeled)"
        )
        confidence = "low - inferred from note spacing only, no dotted notes or ties modeled"
    else:
        if spacing:
            warnings.append(
                f"durations were read from the engraved notation for {glyphs + degraded} staff "
                f"system(s); {spacing} staff system(s) could not be read that way and use a "
                "rougher estimate from note spacing instead - treat those sections as low "
                "confidence"
            )
        if degraded:
            warnings.append(
                f"{degraded} staff system(s) were read from the engraved notation but contain "
                "music-font glyphs this decoder has not been calibrated for - treat their "
                "durations as medium confidence"
            )
        warnings.append(_TUPLET_WARNING)
        warnings.append(_TIE_WARNING)
        if spacing:
            confidence = (
                "mixed - decoded from the score's engraving where a standard-notation staff was "
                "paired with the tab staff; a low-confidence spacing estimate elsewhere"
            )
        elif degraded:
            confidence = (
                "medium - decoded from the score's engraving, but some music-font glyphs used by "
                "this score are outside the decoder's calibrated vocabulary"
            )
        else:
            confidence = (
                "high - decoded directly from the notehead/stem/flag/beam/dot glyphs in the score's "
                "own engraving"
            )

    # Surface the concrete per-staff reasons behind any downgrade, capped so
    # a long score can't turn the warning list into a wall of text.
    for prov in (PROV_SPACING, PROV_GLYPHS_DEGRADED):
        seen = details.get(prov) or []
        for detail in seen[:3]:
            warnings.append(f"rhythm source: {detail}")
        if len(seen) > 3:
            warnings.append(f"rhythm source: and {len(seen) - 3} further distinct reason(s)")
    return warnings, confidence


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
    """measures: list of (beats, measure_ts) where beats is a list of
    (duration_code, dots, notes). measure_ts may be None (meaning "whatever
    is already in effect"); where it differs from the meter currently in
    effect, a `\\ts` is emitted on that bar, so a score that changes meter
    part-way through is transcribed with the change in it rather than having
    every later bar measured against the opening meter.

    A plain list of beats per measure is also accepted, for callers that
    have no per-measure meter to carry.
    """
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
    in_effect = tuple(ts) if ts else None
    for measure in measures:
        if measure and isinstance(measure, tuple) and len(measure) == 2:
            beats_in, measure_ts = measure
        else:
            beats_in, measure_ts = measure, None
        prefix = ""
        if measure_ts and tuple(measure_ts) != in_effect:
            in_effect = tuple(measure_ts)
            prefix = f"\\ts {in_effect[0]} {in_effect[1]} "
        beats = " ".join(_fmt_beat(dur, dots, notes) for dur, dots, notes in beats_in)
        body_lines.append(prefix + beats + " |")
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


def _ts_at(timeline, page_idx, y):
    """The time signature in effect at (page, vertical position), from the
    per-system timeline. Engravers print a meter once, at the point it
    changes, so the value in effect is the last one printed at or before
    this position - not the first one found in the document."""
    best = None
    for entry_page, entry_y, ts in timeline:
        if (entry_page, entry_y) <= (page_idx, y):
            best = ts
        else:
            break
    return best


def _build_time_signature_timeline(pages_with_tab):
    """Read the printed time signature at the start of every notation staff
    on every page that has tab, in document order.

    Returns (timeline, reasons) where timeline is a list of
    (page_index, staff_top, (num, den)) with consecutive duplicates
    collapsed, and reasons is the decoder's own explanation for the staves
    where nothing was found - surfaced to the user instead of being dropped,
    so "assumed 4/4" can say what was actually looked at.
    """
    timeline = []
    reasons = []
    for page_idx, page, _tab_staves, std_staves in pages_with_tab:
        for s in std_staves:
            ts, reason = glyph.decode_time_signature(page, s.top, s.bottom, s.x0, s.spacing)
            if ts is None:
                reasons.append(reason)
                continue
            if timeline and timeline[-1][2] == ts:
                continue  # same meter still in force - not a change
            timeline.append((page_idx, s.top, ts))
    return timeline, reasons


def _extract(doc, pdf_path, time_signature: tuple[int, int] | None) -> ExtractionResult:
    if doc.page_count == 0:
        return ExtractionResult(extractable=False, reason="pdf has no pages")

    warnings: list[str] = []
    override = tuple(time_signature) if time_signature else None
    if override and not glyph.time_signature_is_valid(override):
        warnings.append(
            f"the supplied time signature {override[0]}/{override[1]} is not a usable meter - "
            "ignored in favour of detection"
        )
        override = None
    ts = override
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
    pages_with_tab = []  # (page_index, page, tab_staves, std_staves) in page order

    # Pass 1: staff census plus tempo/tuning hints. Deliberately does NO
    # glyph work: time-signature decoding costs a font parse plus a
    # get_texttrace() walk per page, and a notation-only document (no tab
    # staves anywhere) used to pay all of it on every page before returning
    # extractable: false. The census tells us which pages are even
    # candidates first - see the time-signature pass below.
    for page_no in range(doc.page_count):
        page = doc[page_no]
        text = page.get_text("text")
        if not page.get_fonts(full=True) and not glyph.page_drawings(page) and not text.strip():
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

        if tempo is None:
            q_match = re.search(r"=\s*(\d{2,3})", text)
            if q_match:
                tempo = int(q_match.group(1))

        if tuning_label is None and "Drop D" in text:
            tuning_label = "Drop D"
            tuning = list(DROP_D_TUNING)

        if tab_staves:
            pages_with_tab.append((page_no, page, tab_staves, std_staves))

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

    # Time signature, now that we know there is tab worth extracting. Read
    # per notation system so a meter CHANGE part-way through the score is
    # seen, instead of taking whichever staff answered first and measuring
    # every later bar against it (the library demonstrably contains pieces
    # that change meter mid-score - e.g. 4/4 -> 12/16 -> 4/4).
    ts_timeline = []
    ts_reasons = []
    if override is None:
        ts_timeline, ts_reasons = _build_time_signature_timeline(pages_with_tab)
        if ts_timeline:
            ts = ts_timeline[0][2]
            ts_source = "glyph-decoded"
        else:
            for page_idx, page, _t, std_staves in pages_with_tab:
                if not std_staves:
                    continue
                detected = _detect_time_signature(page, std_staves[0])
                if detected and glyph.time_signature_is_valid(detected):
                    ts = detected
                    ts_source = "auto-detected"
                    break

    if ts is None:
        ts = (4, 4)
        ts_source = "not detected (assumed 4/4)"
        warnings.append(
            "time signature not detected - glyphs live in a subsetted music font at remapped "
            "codepoints; assumed 4/4 for bar/beat grouping, pass time_signature to override"
        )
        # The decoder's own account of what it looked at, rather than only a
        # generic "assumed 4/4".
        for reason in list(dict.fromkeys(ts_reasons))[:3]:
            warnings.append(f"time signature: {reason}")

    if len(ts_timeline) > 1:
        changes = ", ".join(f"{n}/{d}" for _, _, (n, d) in ts_timeline[:6])
        warnings.append(
            f"this score changes time signature part-way through ({changes}) - the change is "
            "carried into the transcription, but bar grouping around a meter change is lower "
            "confidence than a single-meter score"
        )

    # Pass 2: real extraction, now that ts/tempo/tuning are settled.
    all_measures = []  # list of (beats, measure_ts)
    unmatched_total = 0
    rejected_merges_total = 0
    suspicious_frets_total = 0
    # Which rhythm source each tab staff resolved to. The document's rhythm
    # warnings and confidence are derived from these counts in ONE place
    # (see _rhythm_report) rather than from a branch here.
    prov_counts = collections.Counter()
    prov_details = collections.defaultdict(list)
    unmatched_columns_glyph = 0
    unmatched_glyph_notes_total = 0
    font_warnings_seen = []
    for page_idx, page, tab_staves, std_staves in pages_with_tab:
        tokens = _extract_digit_tokens(page)
        by_staff, unmatched = _assign_tokens_to_tab_staves(tokens, tab_staves)
        unmatched_total += len(unmatched)
        # Computed once per page and reused for every staff on it - see
        # _detect_barlines docstring.
        vseg = _vertical_segments(page)
        # Pair every tab staff on the page to a notation staff inside its
        # own system, exclusively, before decoding anything - see
        # _pair_standard_staves.
        pairs, pair_reasons = _pair_standard_staves(tab_staves + std_staves)
        decoded = {}
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

            std_staff = pairs.get(id(staff))
            source = _resolve_rhythm_source(
                page, std_staff, pair_reasons.get(id(staff)), decoded)
            prov_counts[source.provenance] += 1
            if source.detail and source.detail not in prov_details[source.provenance]:
                prov_details[source.provenance].append(source.detail)
            for fw in source.stats.get("font_warnings") or []:
                if fw not in font_warnings_seen:
                    font_warnings_seen.append(fw)

            # The meter in effect for this staff: from the notation staff it
            # reads, or from its own position when it reads none.
            anchor_y = std_staff.top if std_staff is not None else staff.top
            staff_ts = _ts_at(ts_timeline, page_idx, anchor_y) or ts
            measure_quarter_len = _measure_quarter_length(staff_ts)

            # Two-and-a-half staff-line-spacings is comfortably wider than
            # normal engraving jitter between a notehead and its tab digit,
            # but tight enough to actually reject a mismatch in a dense
            # passage.
            x_tol = staff.spacing * 2.5
            if source.uses_glyphs:
                # col["x"] is a fret digit's LEFT edge; glyph note events'
                # x is the notehead bbox CENTER - comparing them directly
                # is a systematic offset of about half a digit's width,
                # enough to pick the wrong neighbor in a dense passage.
                # Approximate each column's center with this staff's own
                # measured average digit width.
                avg_digit_w = (sum(t.width for t in toks) / len(toks)) if toks else 5.0
                for col in columns:
                    col["xc"] = col["x"] + avg_digit_w / 2

            for i, m_cols in enumerate(measures_for_staff):
                m_lo, m_hi = bounds[i], bounds[i + 1]
                if source.uses_glyphs:
                    beats, unmatched_cols, unmatched_notes = _build_measure_beats_glyph(
                        m_cols, m_lo, m_hi, source.note_events, source.note_xs,
                        x_tol, staff.spacing,
                    )
                    unmatched_columns_glyph += unmatched_cols
                    unmatched_glyph_notes_total += unmatched_notes
                    if not beats:
                        # Nothing decoded and no tab columns either - still
                        # emit an explicit rest bar rather than dropping the
                        # measure (see the non-glyph branch below for why).
                        beats = [(_snap_duration(measure_quarter_len), 0, [])]
                    all_measures.append((beats, staff_ts))
                    continue
                if not m_cols:
                    # No digit columns landed in this bar - emit an explicit
                    # rest bar instead of dropping it. Skipping it entirely
                    # would omit its "|" separator and shift every later
                    # bar's number one position earlier than the PDF, which
                    # breaks side-by-side comparison against the original.
                    all_measures.append(([(_snap_duration(measure_quarter_len), 0, [])], staff_ts))
                    continue
                durations_q = _infer_measure_rhythm(m_cols, measure_quarter_len, m_hi)
                beats = [(_snap_duration(dq), 0, col["notes"]) for col, dq in zip(m_cols, durations_q)]
                all_measures.append((beats, staff_ts))

    # Rhythm warnings and confidence: derived once, from what the staves
    # actually resolved to.
    rhythm_warnings, rhythm_confidence = _rhythm_report(prov_counts, prov_details)
    warnings.extend(rhythm_warnings)
    # Font-level problems that weren't already the reason a staff degraded
    # (those are reported by _rhythm_report as "rhythm source: ..."): a page
    # can carry an unreadable music font without it being what decided any
    # staff's provenance.
    already = "\n".join(warnings)
    for fw in font_warnings_seen[:3]:
        if fw not in already:
            warnings.append(f"music font: {fw}")

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
    beats_total = sum(len(beats) for beats, _ in all_measures)
    notes_total = sum(len(notes) for beats, _ in all_measures for _, _, notes in beats)

    ts_confidence = {
        "manual override": "n/a - caller supplied",
        "glyph-decoded": "high - read directly from the time-signature digit glyphs printed on the score",
        "auto-detected": "medium - read from page text",
    }.get(ts_source, "low - not detected, assumed 4/4")
    if len(ts_timeline) > 1 and ts_source == "glyph-decoded":
        ts_confidence = (
            "medium - read directly from the time-signature digit glyphs, but the score changes "
            "meter part-way through"
        )

    confidence = {
        "frets": "high - read directly from vector text spans positioned against detected tab staff lines",
        "rhythm": rhythm_confidence,
        "time_signature": ts_confidence,
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
        rhythm_provenance=dict(prov_counts),
    )
