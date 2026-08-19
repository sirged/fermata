"""
Prototype: extract guitar tab directly from vector-engraved PDF pages.

Approach (see report for full honesty disclaimers):
  1. Staff detection from vector line primitives (page.get_drawings()) - a run of
     6 evenly spaced long horizontal lines is a tab staff, 5 is a standard staff.
  2. Fret-number extraction from text spans that are pure ASCII digits, assigned
     to a tab staff + string by nearest-line-to-vertical-center.
  3. Column/chord grouping by x-proximity, barline detection from vertical line
     primitives to split measures.
  4. Rhythm: best-effort relative-spacing heuristic, snapped to a time signature
     read directly off the page when we can find one. This is the weakest part
     of the prototype and is reported as such, not oversold.
  5. alphaTex emission + honest text dump of the extracted (string, fret, x) table
     for visual cross-checking against the source page image.

No mention of AI/assistant anywhere in here (project mandate) - this is plain
signal-processing over PDF vector primitives.
"""
import sys
import json
import math
import collections
import argparse
from pathlib import Path

import fitz  # pymupdf


# ---------------------------------------------------------------------------
# Staff detection
# ---------------------------------------------------------------------------

class Staff:
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
        return best_i + 1  # string numbers are 1-based, top line = string 1


def _long_horizontal_segments(page, min_len_ratio=0.25):
    """Collect near-horizontal vector primitives long enough to plausibly be
    staff lines (as opposed to beams, ledger lines, stems)."""
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


def detect_staves(page):
    """Cluster long horizontal line primitives into staff systems.

    Returns (staves, anomalies) where anomalies records line-groups whose size
    was neither 5 nor 6 (so we're honest about what we threw away).
    """
    segs = _long_horizontal_segments(page)
    if not segs:
        return [], []

    # Dedup by rounded y (multiple drawing calls can restate the same line).
    by_y = {}
    for y, x0, x1 in segs:
        key = round(y, 1)
        if key not in by_y:
            by_y[key] = [x0, x1]
        else:
            by_y[key][0] = min(by_y[key][0], x0)
            by_y[key][1] = max(by_y[key][1], x1)
    ys = sorted(by_y.keys())

    # Split into clusters wherever the gap to the next line jumps well past
    # the local staff-line spacing (staff lines are ~4-9pt apart; system-to-
    # system gaps are 20-60pt).
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
            staves.append(Staff("tab", c, x0, x1))
        elif n == 5:
            staves.append(Staff("standard", c, x0, x1))
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


def detect_barlines(page, staff):
    """Vertical segments whose y-span covers most of this staff's height."""
    segs = _vertical_segments(page)
    xs = []
    span = staff.bottom - staff.top
    for x, y0, y1 in segs:
        # must cover most of the staff vertically and be within its x-range
        if y0 <= staff.top + span * 0.3 and y1 >= staff.bottom - span * 0.3:
            if staff.x0 - 2 <= x <= staff.x1 + 2:
                xs.append(round(x, 1))
    xs = sorted(set(xs))
    # merge near-duplicates (double barlines / repeat marks draw two strokes)
    merged = []
    for x in xs:
        if merged and x - merged[-1] < 2.0:
            continue
        merged.append(x)
    return merged


# ---------------------------------------------------------------------------
# Digit (fret number) extraction
# ---------------------------------------------------------------------------

class DigitToken:
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


def extract_digit_tokens(page):
    """All spans that are purely ASCII digits, 1-2 chars, any font.
    We deliberately do NOT filter by font name: Finale exports use
    Arial-BoldMT for fret numbers, Sibelius/Opus exports use
    TimesNewRomanPSMT - font choice is exporter-specific, position relative
    to a detected tab staff is what actually identifies a fret number.
    """
    tokens = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if text.isdigit() and 1 <= len(text) <= 2:
                    tokens.append(DigitToken(text, span["bbox"], span.get("font"), span.get("size")))
    return tokens


def assign_tokens_to_tab_staves(tokens, tab_staves):
    """Return {staff_index: [tokens within that staff's y-band]}."""
    by_staff = collections.defaultdict(list)
    unmatched = []
    for tok in tokens:
        best = None
        best_d = None
        for i, st in enumerate(tab_staves):
            pad = st.spacing * 0.75
            if st.top - pad <= tok.yc <= st.bottom + pad and st.x0 - 5 <= tok.x0 <= st.x1 + 5:
                d = min(abs(tok.yc - st.top), abs(tok.yc - st.bottom))
                # distance to staff band center as tie-breaker
                center = (st.top + st.bottom) / 2
                d = abs(tok.yc - center)
                if best is None or d < best_d:
                    best, best_d = i, d
        if best is None:
            unmatched.append(tok)
        else:
            by_staff[best].append(tok)
    return by_staff, unmatched


def merge_multidigit(tokens_for_staff, staff):
    """Merge adjacent 1-digit tokens on the same string line into 2-digit
    fret numbers (e.g. "1" then "2" immediately to its right -> "12").
    Tokens that already arrived as 2-char spans are left alone.
    """
    # group by assigned string first (based on individual token y before merge)
    per_string = collections.defaultdict(list)
    for tok in tokens_for_staff:
        s = staff.string_for_y(tok.yc)
        per_string[s].append(tok)

    merged_notes = []  # (x0, string, fret_text, yc)
    for s, toks in per_string.items():
        toks.sort(key=lambda t: t.x0)
        i = 0
        avg_w = sum(t.width for t in toks) / len(toks) if toks else 5.0
        while i < len(toks):
            t = toks[i]
            if (
                len(t.text) == 1
                and i + 1 < len(toks)
                and len(toks[i + 1].text) == 1
                and (toks[i + 1].x0 - t.x1) < avg_w * 0.5
                and abs(toks[i + 1].yc - t.yc) < 1.5
            ):
                nxt = toks[i + 1]
                merged_notes.append((t.x0, s, t.text + nxt.text, (t.yc + nxt.yc) / 2))
                i += 2
            else:
                merged_notes.append((t.x0, s, t.text, t.yc))
                i += 1
    merged_notes.sort(key=lambda n: n[0])
    return merged_notes


# ---------------------------------------------------------------------------
# Column / chord grouping
# ---------------------------------------------------------------------------

def group_into_columns(notes, x_tol=1.5, wide_chord_ratio=0.35):
    """notes: list of (x0, string, fret_text, yc) sorted by x0.
    Returns list of columns: [{"x": float, "notes": [(string, fret_text), ...]}]

    Two-pass grouping:
      1. Tight x-proximity clustering (x_tol) catches chords engraved at
         exactly the same column.
      2. A second pass merges adjacent columns whose gap is small relative
         to the *local* column spacing. Engravers commonly offset a bass
         tab number a couple points right of a treble number in the same
         chord (to keep both legible when they're far apart vertically -
         e.g. string 1 and string 6), which the tight pass alone misses and
         which otherwise gets misread as a spurious very-short duration.
         This is a heuristic and can occasionally over-merge two genuinely
         consecutive fast notes; see report for how often that happened.
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
                # keep the leftmost x as the column's position
            else:
                merged.append(col)
        columns = merged

    # de-dup: if the same string appears twice in a column (shouldn't happen,
    # but be defensive), keep the first
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
# Time signature (best-effort): look for two stacked plain digits near the
# start of the first standard staff, above the first tab staff.
# ---------------------------------------------------------------------------

def detect_time_signature(page, standard_staff):
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
    # look for two entries at close-to-identical x, one above the staff
    # midline (numerator) and one below (denominator)
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
# Rhythm inference (honest best-effort, relative-spacing based)
# ---------------------------------------------------------------------------

DURATION_UNITS = [
    (4.0, 1), (3.0, 2), (2.0, 2), (1.5, 4), (1.0, 4),
    (0.75, 8), (0.5, 8), (0.25, 16), (0.125, 32),
]


def snap_duration(quarter_units):
    """Snap a duration expressed in quarter-note units to the nearest
    alphaTex duration code, ignoring dotted values (not modeled)."""
    plain = [(4.0, 1), (2.0, 2), (1.0, 4), (0.5, 8), (0.25, 16), (0.125, 32)]
    best = min(plain, key=lambda p: abs(p[0] - quarter_units))
    return best[1]


def infer_measure_rhythm(columns_in_measure, measure_quarter_len):
    """Best-effort duration assignment from column x-spacing.

    Method: treat the gap from each column to the next (or to the barline for
    the last column) as proportional to that column's duration. Normalize so
    the gaps sum to measure_quarter_len (quarter-note units), then snap each
    to the nearest plain duration. This is a heuristic, not a real rhythm
    decoder - it will get relative note lengths roughly right on simple
    passages and will be wrong wherever engraving spacing doesn't scale
    linearly with duration (grace notes, cramped chords, courtesy spacing).
    """
    if not columns_in_measure:
        return []
    xs = [c["x"] for c in columns_in_measure]
    if len(xs) == 1:
        gaps = [measure_quarter_len]
    else:
        gaps = [b - a for a, b in zip(xs, xs[1:])]
        # last note: assume same gap as the median of the others (no barline
        # x passed in here; caller trims to measure so this is an approximation)
        gaps.append(sum(gaps) / len(gaps))
    total = sum(gaps)
    if total <= 0:
        return [measure_quarter_len / len(xs)] * len(xs)
    scaled = [g / total * measure_quarter_len for g in gaps]
    return scaled


# ---------------------------------------------------------------------------
# alphaTex emission
# ---------------------------------------------------------------------------

def fmt_note(string, fret):
    return f"{fret}.{string}"


def fmt_beat(duration_code, notes):
    body = " ".join(fmt_note(s, f) for s, f in notes) if len(notes) == 1 else \
        "(" + " ".join(fmt_note(s, f) for s, f in notes) + ")"
    return f":{duration_code} {body}"


def build_alphatex(title, tempo, tuning, ts, measures):
    """measures: list of list of (duration_code, notes) beats."""
    lines = [f'\\title "{title}"']
    if tempo:
        lines.append(f"\\tempo {tempo}")
    if ts:
        lines.append(f"\\ts {ts[0]} {ts[1]}")
    if tuning:
        lines.append("\\tuning " + " ".join(tuning))
    lines.append(".")
    body_lines = []
    for measure in measures:
        beats = " ".join(fmt_beat(dur, notes) for dur, notes in measure)
        body_lines.append(beats + " |")
    lines.append("\n".join(body_lines))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level pipeline for one page
# ---------------------------------------------------------------------------

DEFAULT_TUNING = ["E2", "A2", "D3", "G3", "B3", "E4"]


def process_page(pdf_path, page_no, out_dir, title_hint=None, ts_override=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(pdf_path).stem.replace(" ", "_")

    doc = fitz.open(pdf_path)
    page = doc[page_no]

    report = {"path": str(pdf_path), "page": page_no}

    # --- Extractability gate -------------------------------------------------
    fonts = page.get_fonts(full=True)
    drawings = page.get_drawings()
    text = page.get_text("text")
    if not fonts and not drawings and not text.strip():
        report["extractable"] = False
        report["reason"] = "no fonts, no vector drawings, no text - page is a raster scan"
        _save_report(out_dir, stem, report)
        _render_png(page, out_dir, stem)
        print(json.dumps(report, indent=2))
        return report

    staves, anomalies = detect_staves(page)
    tab_staves = [s for s in staves if s.kind == "tab"]
    std_staves = [s for s in staves if s.kind == "standard"]

    report["extractable"] = bool(tab_staves)
    report["standard_staff_count"] = len(std_staves)
    report["tab_staff_count"] = len(tab_staves)
    report["line_group_anomalies"] = anomalies

    if not tab_staves:
        report["reason"] = (
            "no 6-line staff groups found - either a raster scan, or (as with "
            "the Tarrega file) a standard-notation-only score with fingering "
            "numbers rather than a real tab staff"
        )
        _save_report(out_dir, stem, report)
        _render_png(page, out_dir, stem)
        print(json.dumps(report, indent=2))
        return report

    # --- Digit extraction & assignment ---------------------------------------
    tokens = extract_digit_tokens(page)
    by_staff, unmatched = assign_tokens_to_tab_staves(tokens, tab_staves)
    report["digit_token_count"] = len(tokens)
    report["unmatched_digit_tokens"] = len(unmatched)

    # --- Time signature (best effort, from first system) ---------------------
    ts = None
    if std_staves:
        ts = detect_time_signature(page, std_staves[0])
    report["time_signature_auto_detected"] = ts
    report["time_signature_auto_detect_note"] = (
        "Time signature glyphs live inside the subsetted Maestro/Opus music "
        "font at remapped codepoints that do not correspond to their visual "
        "meaning (e.g. the '3' of a 3/4 signature can extract as an unrelated "
        "Unicode char). Reliable text-based time-signature reading was not "
        "achieved; use --ts to supply it by hand for evaluation."
    )
    if ts_override is not None:
        ts = ts_override
        report["time_signature_source"] = "manual override"
    else:
        report["time_signature_source"] = "auto-detected" if ts else "not detected (assumed 4/4 for rhythm budget)"

    # --- Tuning guess ----------------------------------------------------------
    # Look for a literal tuning label ("Drop D", "Open G", etc.) among text;
    # otherwise fall back to standard tuning. We do NOT attempt to derive
    # tuning from the fret/string data itself (would need pitch-class
    # knowledge of the tab, out of scope for this prototype).
    tuning = DEFAULT_TUNING
    tuning_label = None
    full_text = page.get_text("text")
    if "Drop D" in full_text:
        tuning_label = "Drop D"
        tuning = ["D2", "A2", "D3", "G3", "B3", "E4"]
    report["tuning_label"] = tuning_label
    report["tuning"] = tuning

    # --- Per-tab-staff extraction ---------------------------------------------
    all_rows = []  # for the human-readable dump
    all_measures = []  # list of measures (each list of beats) across all staves in reading order
    for si, staff in enumerate(sorted(tab_staves, key=lambda s: s.top)):
        toks = by_staff.get(si, [])
        if not toks:
            continue
        notes = merge_multidigit(toks, staff)  # (x0, string, fret_text, yc)
        for x0, s, fret, yc in notes:
            all_rows.append({
                "staff_index": si, "staff_top_y": staff.top, "x": round(x0, 2),
                "string": s, "fret": fret, "yc": round(yc, 2),
            })
        columns = group_into_columns(notes)
        barline_xs = detect_barlines(page, staff)
        # keep only barlines within the staff's note range, plus synthetic
        # start/end bounds
        col_xs = [c["x"] for c in columns]
        lo = min(col_xs) - 5
        hi = max(col_xs) + 5
        bars = [x for x in barline_xs if lo <= x <= hi]
        bounds = sorted(set([staff.x0] + bars + [staff.x1]))

        # split columns into measures using the barline bounds
        measure_idx = 0
        measures_for_staff = [[] for _ in range(len(bounds) - 1)]
        for col in columns:
            while measure_idx < len(bounds) - 2 and col["x"] >= bounds[measure_idx + 1]:
                measure_idx += 1
            measures_for_staff[measure_idx].append(col)

        beats_per_measure = (ts[0] if ts else 4)
        for m_cols in measures_for_staff:
            if not m_cols:
                continue
            durations_q = infer_measure_rhythm(m_cols, float(beats_per_measure))
            beats = []
            for col, dq in zip(m_cols, durations_q):
                code = snap_duration(dq)
                beats.append((code, col["notes"]))
            all_measures.append(beats)

    report["extracted_note_count"] = len(all_rows)
    report["measure_count"] = len(all_measures)
    report["barline_detection"] = "vector vertical-line primitives spanning staff height"
    report["rhythm_method"] = (
        "heuristic: inter-column x-spacing normalized to the detected/assumed "
        "time signature's quarter-note budget per measure, then snapped to "
        "nearest plain duration (no dotted-note or tie modeling). "
        "LOW CONFIDENCE - see report for honest accuracy assessment."
    )

    # --- Emit alphaTex -----------------------------------------------------
    title = title_hint or Path(pdf_path).stem
    tempo = None
    m = None
    import re
    tm = re.search(r"[qQ]\s*=\s*(\d+)", full_text) or re.search(r"(\d+)\s*=\s*(\d+)", full_text)
    tempo_match = re.search(r"(\d{2,3})\s*$", "")  # placeholder, replaced below
    q_match = re.search(r"=\s*(\d{2,3})", full_text)
    if q_match:
        tempo = int(q_match.group(1))
    tex = build_alphatex(title, tempo, tuning, ts, all_measures)

    # --- Write outputs -------------------------------------------------------
    (out_dir / f"{stem}_p{page_no}.tex").write_text(tex, encoding="utf-8")
    _write_table(out_dir, stem, page_no, all_rows)
    _save_report(out_dir, stem, report, page_no)
    _render_png(page, out_dir, stem, page_no)

    print(json.dumps(report, indent=2))
    print("\n--- alphaTex (first 15 lines) ---")
    print("\n".join(tex.splitlines()[:15]))
    return report


def _write_table(out_dir, stem, page_no, rows):
    path = Path(out_dir) / f"{stem}_p{page_no}_table.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{'staff':>5} {'x':>8} {'string':>6} {'fret':>5} {'yc':>8}\n")
        for r in sorted(rows, key=lambda r: (r["staff_index"], r["x"])):
            f.write(f"{r['staff_index']:>5} {r['x']:>8} {r['string']:>6} {r['fret']:>5} {r['yc']:>8}\n")
    return path


def _save_report(out_dir, stem, report, page_no=0):
    path = Path(out_dir) / f"{stem}_p{page_no}_report.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return path


def _render_png(page, out_dir, stem, page_no=0, dpi=200):
    pix = page.get_pixmap(dpi=dpi)
    path = Path(out_dir) / f"{stem}_p{page_no}.png"
    pix.save(str(path))
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--out", default="out")
    ap.add_argument("--title")
    ap.add_argument("--ts", nargs=2, type=int, metavar=("NUM", "DEN"),
                     help="manual time-signature override for rhythm-budget evaluation, e.g. --ts 3 4")
    args = ap.parse_args()
    process_page(args.pdf, args.page, args.out, args.title, tuple(args.ts) if args.ts else None)
