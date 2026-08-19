"""
Guitar tab extractor - glyph-based rhythm variant.

This is extract_tab_baseline.py's pipeline (staff detection, fret-number
extraction, column/barline grouping, alphaTex emission all unchanged) with
its two weakest parts replaced:

  - Rhythm: was a heuristic that treated inter-column x-spacing as
    proportional to duration (no dotted notes, no ties, wrong whenever
    engraving spacing doesn't scale linearly with duration). Now each
    measure's beats are read directly off the paired standard-notation
    staff by decoding the actual notehead/flag/beam/dot music-font glyphs
    (see glyph_rhythm.py for the full method and its validation notes),
    including rest beats (emitted as real alphaTex `r` beats, not dropped)
    and dotted durations (emitted as the `{d}`/`{dd}` beat effect - a
    trailing dot on the duration code itself is not valid alphaTex).
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
import argparse
import collections
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


def _cluster_pitched_events(events, cluster_x_tol=1.5):
    """Group note_events sharing (almost) the same x into one cluster - the
    members of a chord (measured: chord noteheads land at IDENTICAL x, a
    beat apart from its neighbors by a staff's full note-spacing, ~13pt in
    a typical library file - so a tight tolerance cleanly separates the two
    without any ambiguity), or of two overlapping voices notated at the
    same onset. A cluster becomes ONE beat, not one beat per member -
    matching every member independently against tab columns let a chord's
    2nd/3rd/4th notehead each go looking for its own "nearest unused
    column" once the first member had already claimed the correct one, and
    in a dense passage the nearest still-unused column can easily be the
    NEXT beat's column - silently stealing it and giving that neighboring
    beat the wrong duration. This showed up as real, systematically
    over-counted per-measure quarter-note totals (checked against the
    page's own time signature), not just a cosmetic "extra unmatched
    events" number."""
    events = sorted(events, key=lambda n: n["x"])
    clusters = []
    for ev in events:
        if clusters and abs(ev["x"] - clusters[-1][0]["x"]) <= cluster_x_tol:
            clusters[-1].append(ev)
        else:
            clusters.append([ev])
    return clusters


def _cluster_duration(cluster):
    """Pick one representative (duration_code, dots) for a cluster. Usually
    every member agrees (chord noteheads share one stem, so one duration);
    when they don't - genuinely overlapping voices with different notated
    values at the same onset (e.g. a sustained melody quarter over a
    plucked bass eighth - confirmed on real decoded events, not
    speculation) - take the most common reading, breaking ties toward the
    LONGER value. This is a judgment call, not a definitively "correct"
    answer: true polyphony isn't modeled here (one beat, one duration), so
    when two voices genuinely disagree, something is picked rather than
    fragmenting the beat."""
    counts = collections.Counter((ev["duration"], ev["dots"]) for ev in cluster)
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0][0]))[0]


def build_measure_beats(m_cols, m_lo, m_hi, note_events, x_tol):
    """Build one measure's beat list, driven by the glyph-decoded note/rest
    events on the paired standard staff (when available) rather than by tab
    columns alone.

    Why driven by note_events: match_duration used to only ever look at
    PITCHED glyph events and get called once per tab column, which meant
    (a) rest events - which the glyph decoder finds correctly - were always
    thrown away and no rest beat was ever emitted, so a bar containing a
    rest came out short, and (b) a bar with NO tab digits at all (a
    rest-only bar) was skipped entirely by the caller, shifting every later
    bar's alignment against the source PDF. Walking note_events directly
    fixes both: every rest becomes its own beat with no tab column
    consumed, and a measure with zero tab columns but real rest events
    still emits them instead of vanishing.

    Returns (beats, unmatched_columns, unmatched_glyph_notes) where beats is
    a list of (duration_code, dots, notes) ready for _fmt_beat, in x order;
    unmatched_columns is how many tab columns had no glyph note within
    x_tol (an honest count now that x_tol is tight - see the caller); and
    unmatched_glyph_notes is how many PITCHED glyph notes had no tab column
    to match (reported for the same honesty reason, though this direction
    is expected to be rare - every played tab note should have a fret
    number).
    """
    if not note_events:
        # no standard-staff pairing to decode durations from at all - fall
        # back to one beat per tab column with an eighth-note placeholder
        # (matches the old heuristic's typical case); every column counts
        # as unmatched since there was nothing to match against.
        beats = sorted(((col["x"], 8, 0, col["notes"]) for col in m_cols), key=lambda b: b[0])
        return [(c, d, n) for _, c, d, n in beats], len(m_cols), 0

    measure_events = [n for n in note_events if m_lo <= n["x"] < m_hi]
    rest_events = [n for n in measure_events if n["is_rest"]]
    pitched_clusters = _cluster_pitched_events([n for n in measure_events if not n["is_rest"]])

    cols_sorted = sorted(m_cols, key=lambda c: c["x"])
    used = [False] * len(cols_sorted)

    tagged = []  # (x, duration_code, dots, notes)
    unmatched_glyph_notes = 0

    for ev in rest_events:
        tagged.append((ev["x"], ev["duration"], ev["dots"], []))

    for cluster in pitched_clusters:
        cx = sum(ev["x"] for ev in cluster) / len(cluster)
        code, dots = _cluster_duration(cluster)
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
            tagged.append((col["x"], 8, 0, col["notes"]))  # conservative fallback, matches old heuristic's typical case

    tagged.sort(key=lambda t: t[0])
    return [(code, dots, notes) for _, code, dots, notes in tagged], unmatched_columns, unmatched_glyph_notes


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
        base._save_report(out_dir, stem, report, page_no)
        base._render_png(page, out_dir, stem, page_no)
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
        base._save_report(out_dir, stem, report, page_no)
        base._render_png(page, out_dir, stem, page_no)
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

    # Time signature is usually only printed once, at the first system that
    # states it - use the first one found (in top-to-bottom system order) as
    # the single time signature for the whole page. KNOWN LIMITATION,
    # documented rather than silently ignored: alphaTex emission here only
    # supports one global `\ts` header, so a genuine mid-page meter change
    # is not modeled - the page's LATER systems still decode their own
    # correct rhythm from their own note glyphs regardless of what `\ts` was
    # printed, only the emitted header text itself would be wrong for bars
    # after the change.
    ts_first_reason = None
    for top in sorted(ts_by_std_top):
        if ts_by_std_top[top] is not None:
            ts_first_reason = ts_by_std_top[top], ts_reason_by_std_top[top]
            break

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
    unmatched_glyph_notes = 0
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

        # x tolerance for matching a tab column to the glyph-decoded note at
        # (approximately) the same x position. This used to be
        # staff.spacing * 8.0 (~50-60pt - wider than a whole measure of
        # 16ths), which meant match_duration always found SOME note and
        # "0/123 unmatched" was a vacuous metric: it could never reject a
        # wrong neighbor. Two staff-line-spacings is comfortably wider than
        # normal engraving jitter between a notehead and its tab digit, but
        # tight enough to actually reject a mismatch in a dense passage.
        x_tol = staff.spacing * 2.5

        # col["x"] (from group_into_columns) is a fret digit's LEFT edge;
        # note_events' "x" is the notehead bbox CENTER - comparing them
        # directly is a systematic offset of about half a digit's width,
        # which is enough to pick the wrong neighbor in dense passages.
        # Approximate each column's center using this staff's own measured
        # average digit width (not a guessed constant) and compare against
        # that instead; store it alongside "x" rather than replacing it so
        # bar-boundary assignment (which used "x" as a left-edge, order-only
        # reference) is unaffected.
        avg_digit_w = (sum(t.width for t in toks) / len(toks)) if toks else 5.0
        for col in columns:
            col["xc"] = col["x"] + avg_digit_w / 2

        for (m_lo, m_hi), m_cols in zip(zip(bounds[:-1], bounds[1:]), measures_for_staff):
            has_events_in_range = any(m_lo <= n["x"] < m_hi for n in note_events)
            if not m_cols and not has_events_in_range:
                continue
            beats, unmatched, glyph_unmatched = build_measure_beats(m_cols, m_lo, m_hi, note_events, x_tol)
            total_columns += len(m_cols)
            unmatched_columns += unmatched
            unmatched_glyph_notes += glyph_unmatched
            all_measures.append(beats)

    rest_beats_emitted = sum(1 for measure in all_measures for _c, _d, notes in measure if not notes)

    report["extracted_note_count"] = len(all_rows)
    report["measure_count"] = len(all_measures)
    report["barline_detection"] = "vector vertical-line primitives spanning staff height"
    report["glyph_decoded_column_count"] = total_columns - unmatched_columns
    report["unmatched_column_count"] = unmatched_columns
    report["unmatched_glyph_note_count"] = unmatched_glyph_notes
    report["rest_beats_emitted"] = rest_beats_emitted
    report["rhythm_method_detail"] = (
        "glyph decode: each measure's beats are driven by the note/rest "
        "events decoded off the paired standard staff's actual "
        "notehead+stem+flag+beam+dot glyphs (see glyph_rhythm.py), not "
        "guessed from spacing - rests are emitted as real 'r' beats (not "
        "dropped), and dotted durations are emitted as the {d}/{dd} beat "
        "effect (a trailing dot on the duration code is not valid "
        "alphaTex). A tab column only falls back to an eighth-note "
        "placeholder when no standard staff is paired, or when no glyph "
        "note is within a tolerance tight enough to actually reject a "
        "wrong neighbor (see unmatched_column_count for how often that "
        "happened - honestly, not tuned to read zero)."
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
        beats = " ".join(_fmt_beat(code, dots, notes) for code, dots, notes in measure)
        body_lines.append(beats + " |")
    lines.append("\n".join(body_lines))
    return "\n".join(lines)


def _fmt_beat(duration_code, dots, notes):
    # A dotted duration is NOT valid alphaTex as a trailing dot on the
    # duration code (":8." / ":8.." fail to parse) - it's a beat effect
    # appended to the note/chord body instead (":8 3.4{d}", or with a chord
    # ":2 (3.4 5.3){d}"). Confirmed against the repo's own alphaTab
    # importer; see verify_tex.mjs, which is the regression check for this.
    if not notes:
        body = "r"  # rest beat - no fret numbers, just the rest itself
    elif len(notes) == 1:
        body = base.fmt_note(*notes[0])
    else:
        body = "(" + " ".join(base.fmt_note(s, f) for s, f in notes) + ")"
    dot_effect = "{d}" if dots == 1 else "{dd}" if dots == 2 else ""
    return f":{duration_code} {body}{dot_effect}"


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
