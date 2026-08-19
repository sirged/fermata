"""
Guitar tab extractor - glyph-based rhythm variant.

This is extract_tab_baseline.py's pipeline (staff detection, fret-number
extraction, column/barline grouping, alphaTex emission all unchanged) with
its two weakest parts replaced:

  - Rhythm: was a heuristic that treated inter-column x-spacing as
    proportional to duration (no dotted notes, no ties, wrong whenever
    engraving spacing doesn't scale linearly with duration). Now each tab
    column's duration is read directly off the paired standard-notation
    staff by decoding the actual notehead/flag/beam/dot music-font glyphs
    at that x position - see glyph_rhythm.py for the full method and its
    validation notes.
  - Time signature: was a best-effort scan for plain ASCII digit spans,
    which fails outright because time-signature digits are drawn in the
    same subsetted music font as everything else and extract as garbage
    codepoints. Now decoded the same way as noteheads: by classifying the
    actual digit/common-time/cut-time glyphs.

Both changes only apply when a standard-notation staff is paired with the
tab staff (score+tab layout, which is what this library's PDFs use). If no
standard staff is found, rhythm/time-signature fall back to the prior
behavior so tab-only pages still extract fret numbers.
"""
import sys
import json
import collections
import argparse
from pathlib import Path

import fitz  # pymupdf

sys.path.insert(0, str(Path(__file__).parent))
import extract_tab_baseline as base
import glyph_rhythm as gr


DEFAULT_TUNING = base.DEFAULT_TUNING


def pair_standard_staff(tab_staff, std_staves):
    """The standard-notation staff for a tab staff is the nearest standard
    staff directly above it (score+tab systems always draw notation above
    its tab line)."""
    above = [s for s in std_staves if s.bottom <= tab_staff.top + 2]
    if not above:
        return None
    return min(above, key=lambda s: tab_staff.top - s.bottom)


def match_duration(col_x, note_events, x_tol=None):
    """Find the note event (glyph-decoded) closest in x to a tab column and
    return (duration_code, dots, quarter_units) or None if nothing is close
    enough to trust."""
    if not note_events:
        return None
    pitched = [n for n in note_events if not n["is_rest"]]
    if not pitched:
        return None
    best = min(pitched, key=lambda n: abs(n["x"] - col_x))
    if x_tol is not None and abs(best["x"] - col_x) > x_tol:
        return None
    return best["duration"], best["dots"], best["quarter_units"]


def process_page(pdf_path, page_no, out_dir, title_hint=None, ts_override=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(pdf_path).stem.replace(" ", "_")

    doc = fitz.open(pdf_path)
    page = doc[page_no]

    report = {"path": str(pdf_path), "page": page_no, "rhythm_method": "glyph"}

    fonts = page.get_fonts(full=True)
    drawings = page.get_drawings()
    text = page.get_text("text")
    if not fonts and not drawings and not text.strip():
        report["extractable"] = False
        report["reason"] = "no fonts, no vector drawings, no text - page is a raster scan"
        base._save_report(out_dir, stem, report)
        base._render_png(page, out_dir, stem)
        print(json.dumps(report, indent=2))
        return report

    staves, anomalies = base.detect_staves(page)
    tab_staves = [s for s in staves if s.kind == "tab"]
    std_staves = [s for s in staves if s.kind == "standard"]

    report["extractable"] = bool(tab_staves)
    report["standard_staff_count"] = len(std_staves)
    report["tab_staff_count"] = len(tab_staves)
    report["line_group_anomalies"] = anomalies

    if not tab_staves:
        report["reason"] = (
            "no 6-line staff groups found - either a raster scan, or "
            "a standard-notation-only score with fingering numbers rather "
            "than a real tab staff"
        )
        base._save_report(out_dir, stem, report)
        base._render_png(page, out_dir, stem)
        print(json.dumps(report, indent=2))
        return report

    tokens = base.extract_digit_tokens(page)
    by_staff, unmatched = base.assign_tokens_to_tab_staves(tokens, tab_staves)
    report["digit_token_count"] = len(tokens)
    report["unmatched_digit_tokens"] = len(unmatched)

    # --- Glyph-based decode per paired standard staff -----------------------
    # decode once per standard staff (a page can have several systems); each
    # tab staff below is paired to the nearest standard staff above it.
    glyph_by_std_top = {}
    ts_by_std_top = {}
    ts_reason_by_std_top = {}
    for s in std_staves:
        notes, stats = gr.decode_note_events(page, s.top, s.bottom, s.x0, s.x1, s.line_ys)
        ts, ts_reason = gr.decode_time_signature(page, s.top, s.bottom, s.x0)
        glyph_by_std_top[s.top] = {
            "notes": [
                {
                    "x": n.x, "duration": n.duration_code, "dots": n.dotted,
                    "is_rest": n.is_rest, "quarter_units": n.quarter_units,
                    "tied_next": n.tied_next, "category": n.category,
                }
                for n in notes
            ],
            "stats": stats,
        }
        ts_by_std_top[s.top] = ts
        ts_reason_by_std_top[s.top] = ts_reason

    # time signature is usually only printed once, at the first system -
    # propagate the last-seen one forward for systems that don't restate it.
    ts_sorted_tops = sorted(ts_by_std_top)
    running_ts = None
    ts_first_reason = None
    for top in ts_sorted_tops:
        if ts_by_std_top[top] is not None:
            running_ts = ts_by_std_top[top]
            if ts_first_reason is None:
                ts_first_reason = ts_by_std_top[top], ts_reason_by_std_top[top]
        ts_by_std_top[top] = ts_by_std_top[top] or running_ts

    ts = ts_override if ts_override is not None else (
        ts_first_reason[0] if ts_first_reason else None
    )
    report["time_signature_auto_detected"] = ts_first_reason[0] if ts_first_reason else None
    report["time_signature_auto_detect_reason"] = (
        ts_first_reason[1] if ts_first_reason else "no time-signature glyphs decoded on this page"
    )
    report["time_signature_source"] = (
        "manual override" if ts_override is not None
        else ("glyph-decoded" if ts_first_reason else "not detected (assumed 4/4 for rhythm budget)")
    )

    tuning = DEFAULT_TUNING
    tuning_label = None
    full_text = page.get_text("text")
    if "Drop D" in full_text:
        tuning_label = "Drop D"
        tuning = ["D2", "A2", "D3", "G3", "B3", "E4"]
    report["tuning_label"] = tuning_label
    report["tuning"] = tuning

    all_rows = []
    all_measures = []
    unmatched_columns = 0
    total_columns = 0
    for si, staff in enumerate(sorted(tab_staves, key=lambda s: s.top)):
        toks = by_staff.get(si, [])
        if not toks:
            continue
        notes = base.merge_multidigit(toks, staff)
        for x0, s, fret, yc in notes:
            all_rows.append({
                "staff_index": si, "staff_top_y": staff.top, "x": round(x0, 2),
                "string": s, "fret": fret, "yc": round(yc, 2),
            })
        columns = base.group_into_columns(notes)
        barline_xs = base.detect_barlines(page, staff)
        col_xs = [c["x"] for c in columns]
        lo = min(col_xs) - 5
        hi = max(col_xs) + 5
        bars = [x for x in barline_xs if lo <= x <= hi]
        bounds = sorted(set([staff.x0] + bars + [staff.x1]))

        measure_idx = 0
        measures_for_staff = [[] for _ in range(len(bounds) - 1)]
        for col in columns:
            while measure_idx < len(bounds) - 2 and col["x"] >= bounds[measure_idx + 1]:
                measure_idx += 1
            measures_for_staff[measure_idx].append(col)

        std_staff = pair_standard_staff(staff, std_staves)
        note_events = glyph_by_std_top.get(std_staff.top, {}).get("notes", []) if std_staff else []
        # a note x-tolerance wider than typical column spacing keeps us from
        # matching a column to a glyph note that belongs to a totally
        # different beat when a staff has no standard-notation pairing.
        x_tol = staff.spacing * 8.0

        beats_per_measure = ts[0] if ts else 4
        for m_cols in measures_for_staff:
            if not m_cols:
                continue
            beats = []
            for col in m_cols:
                total_columns += 1
                match = match_duration(col["x"], note_events, x_tol=x_tol) if note_events else None
                if match is None:
                    unmatched_columns += 1
                    code, dots = 8, 0  # conservative fallback, matches old heuristic's typical case
                else:
                    code, dots, _qu = match
                beats.append((_fmt_duration(code, dots), col["notes"]))
            all_measures.append(beats)

    report["extracted_note_count"] = len(all_rows)
    report["measure_count"] = len(all_measures)
    report["barline_detection"] = "vector vertical-line primitives spanning staff height"
    report["glyph_decoded_column_count"] = total_columns - unmatched_columns
    report["unmatched_column_count"] = unmatched_columns
    report["rhythm_method_detail"] = (
        "glyph decode: each tab column's duration is read from the nearest "
        "note/rest event decoded off the paired standard staff's actual "
        "notehead+stem+flag+beam+dot glyphs (see glyph_rhythm.py), not "
        "guessed from spacing. Falls back to an eighth-note placeholder only "
        "when no standard staff is paired or no glyph note is within a "
        "generous x tolerance."
    )

    title = title_hint or Path(pdf_path).stem
    tempo = None
    import re
    q_match = re.search(r"=\s*(\d{2,3})", full_text)
    if q_match:
        tempo = int(q_match.group(1))
    tex = _build_alphatex_with_dots(title, tempo, tuning, ts, all_measures)

    (out_dir / f"{stem}_p{page_no}.tex").write_text(tex, encoding="utf-8")
    base._write_table(out_dir, stem, page_no, all_rows)
    base._save_report(out_dir, stem, report, page_no)
    base._render_png(page, out_dir, stem, page_no)

    print(json.dumps(report, indent=2))
    print("\n--- alphaTex (first 15 lines) ---")
    print("\n".join(tex.splitlines()[:15]))
    return report


def _fmt_duration(code, dots):
    return f"{code}" + ("." * dots)


def _build_alphatex_with_dots(title, tempo, tuning, ts, measures):
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
        beats = " ".join(_fmt_beat(dur, notes) for dur, notes in measure)
        body_lines.append(beats + " |")
    lines.append("\n".join(body_lines))
    return "\n".join(lines)


def _fmt_beat(duration_code, notes):
    body = " ".join(base.fmt_note(s, f) for s, f in notes) if len(notes) == 1 else \
        "(" + " ".join(base.fmt_note(s, f) for s, f in notes) + ")"
    return f":{duration_code} {body}"


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
