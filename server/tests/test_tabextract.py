import collections
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import fitz
import pytest

from fermata import glyph_rhythm
from fermata import tabextract


def _parse_with_alphatab(tex: str) -> dict:
    """Parse `tex` with the real alphaTab JS importer the web player uses -
    not a Python re-implementation of alphaTex's grammar - via
    tools/tab_extract/verify_tex.mjs. This is the actual regression check
    for the dotted-duration bug that blocked this PR: a duration code with
    a trailing dot (":8.") looks like plausible alphaTex but alphaTab's
    parser rejects it outright; the correct spelling is a `{d}`/`{dd}` beat
    effect. Skips (rather than fails) when node or the web project's
    installed alphaTab build aren't available, since neither is present in
    the production server's own runtime image.
    """
    if shutil.which("node") is None:
        pytest.skip("node not available")
    repo_root = Path(__file__).resolve().parents[2]
    alphatab = repo_root / "web" / "node_modules" / "@coderline" / "alphatab" / "dist" / "alphaTab.mjs"
    if not alphatab.is_file():
        pytest.skip("alphaTab.mjs not found - run `npm ci` in web/ first")
    script = Path(__file__).resolve().parents[1] / "tools" / "tab_extract" / "verify_tex.mjs"

    with tempfile.NamedTemporaryFile("w", suffix=".tex", delete=False, encoding="utf-8") as f:
        f.write(tex)
        tex_path = f.name
    try:
        proc = subprocess.run(
            ["node", str(script), str(alphatab), tex_path],
            capture_output=True, text=True, timeout=60,
        )
    finally:
        Path(tex_path).unlink(missing_ok=True)

    assert proc.returncode == 0, f"alphaTex failed to parse:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _make_tab_pdf(path, pages):
    """Build a minimal PDF with one 6-line tab staff per page - good enough
    to exercise _detect_staves / _detect_barlines / _extract_digit_tokens
    without a real engraved score.

    `pages` is a list of dicts, one per page:
        notes: [(x, string, fret_text), ...]
        barline_xs: [x, ...] vertical barline positions (include the
            staff's own left/right edges to bound the first/last measure)
        staff_x0, staff_x1, staff_top, spacing: staff geometry (optional)
    """
    doc = fitz.open()
    for spec in pages:
        staff_x0 = spec.get("staff_x0", 50)
        staff_x1 = spec.get("staff_x1", 350)
        staff_top = spec.get("staff_top", 50)
        spacing = spec.get("spacing", 10)
        page = doc.new_page(width=600, height=200)
        ys = [staff_top + i * spacing for i in range(6)]
        for y in ys:
            page.draw_line((staff_x0, y), (staff_x1, y))
        for x in spec.get("barline_xs", [staff_x0, staff_x1]):
            page.draw_line((x, staff_top - 10), (x, ys[-1] + 10))
        for x, string, fret in spec.get("notes", []):
            page.insert_text((x, ys[string - 1] + 3), fret, fontsize=7)
    doc.save(path)
    doc.close()
    return path


def test_finale_tab_pdf_extracts_notes_and_bars(zanarkand_pdf):
    result = tabextract.extract(zanarkand_pdf, time_signature=(3, 4))
    assert result.extractable
    assert result.reason is None
    # tab_staff_count / standard_staff_count are summed across pages (see
    # ExtractionResult) - the file has 2 pages, so use a floor rather than
    # pinning an exact count.
    assert result.tab_staff_count >= 5
    assert result.standard_staff_count >= 5
    # Validated against this exact file: page 1 alone extracts 185 notes
    # across 24 bars. The score is 2 pages, so the combined total can only
    # be more - use these as a floor rather than pinning an exact count.
    assert result.bars >= 24
    assert result.notes >= 185
    assert result.pages_processed == 2
    assert result.tuning_label == "Drop D"
    assert result.tuning == ["D2", "A2", "D3", "G3", "B3", "E4"]
    assert result.time_signature == (3, 4)
    assert result.time_signature_source == "manual override"
    assert result.tempo == 88
    assert result.alphatex is not None
    # alphaTex binds the FIRST \tuning entry to string 1, and string 1 is
    # the top tab line (the high string) - see string_for_y and the
    # \tuning-order test below. The emitted line is therefore high-to-low,
    # the reverse of result.tuning (which stays low-to-high).
    assert '\\tuning E4 B3 G3 D3 A2 D2' in result.alphatex
    assert '\\ts 3 4' in result.alphatex
    # Rhythm was decoded from the engraving's own glyphs for this file (see
    # test_glyph_decoded_time_signature_and_dots_without_override) - the old
    # unconditional "inferred from spacing... low confidence" claim would
    # now be false and must not be present.
    assert not any("inferred from horizontal spacing" in w for w in result.warnings)
    # Assert where the durations came from, not the adjective in front of it:
    # this file's bars don't add up (merged voices), which legitimately caps
    # the overall claim even though every duration was glyph-decoded.
    assert result.rhythm_provenance == {tabextract.PROV_GLYPHS: 10}
    assert "own engraving" in result.confidence["rhythm"]


def test_finale_tab_pdf_analyze(zanarkand_pdf):
    info = tabextract.analyze(zanarkand_pdf)
    assert info["extractable"] is True
    assert info["vector"] is True
    assert info["tab_staff_count"] >= 5
    assert info["standard_staff_count"] >= 5
    assert info["page_count"] == 2


def test_glyph_decoded_time_signature_and_dots_without_override(zanarkand_pdf):
    """The engraved time signature (3/4 for this piece) and dotted note
    durations must be read straight from the score's own notehead/flag/dot
    glyphs - no manual time_signature override, and no reliance on the
    old plain-text digit scan (which practically never finds a time
    signature - see module docstring)."""
    result = tabextract.extract(zanarkand_pdf)
    assert result.extractable
    assert result.time_signature == (3, 4)
    assert result.time_signature_source == "glyph-decoded"
    assert not any("time signature not detected" in w for w in result.warnings)
    assert "{d}" in result.alphatex or "{dd}" in result.alphatex


def test_glyph_decoded_alphatex_parses_with_dotted_beats(zanarkand_pdf):
    """The emitted alphaTex must actually parse with alphaTab, and the
    dotted-duration beat effects must survive the round trip as real dotted
    beats (not just a `{d}` substring that happens to be present but sits
    somewhere a parser chokes on). Also confirms tuning is emitted high
    string first: the piece is Drop D, and its first written note (`0.1`)
    must sound MIDI 64 (high E untouched by the drop), not 38/40 (the
    mirrored low string a low-to-high tuning emission would produce)."""
    result = tabextract.extract(zanarkand_pdf, time_signature=(3, 4))
    assert result.extractable
    parsed = _parse_with_alphatab(result.alphatex)
    assert parsed["bars"] == result.bars
    assert parsed["beats"] == result.beats
    assert parsed["notes"] == result.notes
    assert parsed["dottedBeats"] > 0
    assert parsed["firstNoteMidi"] == 64
    # This piece is two-voice fingerstyle writing, so the emitted `\voice`
    # separators must actually have landed their beats in a SECOND concurrent
    # voice - more sounding voices than bars - rather than merely parsing.
    assert parsed["voices"] > parsed["bars"], parsed


def test_notation_only_pdf_has_no_tab_staves(tarrega_pdf):
    info = tabextract.analyze(tarrega_pdf)
    assert info["extractable"] is False
    assert info["vector"] is True
    assert info["tab_staff_count"] == 0
    assert info["standard_staff_count"] > 0

    result = tabextract.extract(tarrega_pdf)
    assert result.extractable is False
    assert result.alphatex is None
    assert "standard-notation only" in result.reason


def test_raster_pdf_is_not_extractable(claire_de_lune_pdf):
    info = tabextract.analyze(claire_de_lune_pdf)
    assert info["extractable"] is False
    assert info["vector"] is False
    assert "raster" in info["reason"]

    result = tabextract.extract(claire_de_lune_pdf)
    assert result.extractable is False
    assert "raster" in result.reason


def test_malformed_pdf_never_raises(tmp_path):
    bogus = tmp_path / "not_a_pdf.pdf"
    bogus.write_bytes(b"this is not a pdf file, just garbage bytes")
    info = tabextract.analyze(bogus)
    assert info["extractable"] is False
    assert info["reason"]
    result = tabextract.extract(bogus)
    assert result.extractable is False
    assert result.reason


def test_missing_pdf_never_raises(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    info = tabextract.analyze(missing)
    assert info["extractable"] is False
    result = tabextract.extract(missing)
    assert result.extractable is False


# ---------------------------------------------------------------------------
# Regression tests for code-review findings on the tab-extraction PR
# ---------------------------------------------------------------------------


def test_tuning_emitted_high_string_first_for_alphatex_binding():
    """alphaTex binds the FIRST \\tuning entry to string 1, and
    _Staff.string_for_y assigns string 1 to the top tab line (the highest
    string). tabextract keeps `tuning` low-to-high everywhere else (see
    DEFAULT_TUNING, DROP_D_TUNING, ExtractionResult.tuning), so the emitted
    line must be the reverse of that list - emitting it as-is would put
    every note on its mirrored string (e.g. Drop D would land on the wrong
    end of the neck). Independently confirmed against the real alphaTab
    importer: with this emission order, "0.1" parses to MIDI 64 (high E)
    and "0.6" parses to MIDI 40 (low E); the pre-fix low-to-high order gave
    "0.1" a MIDI pitch of 40, the mirrored string - see PR verification
    notes."""
    tex = tabextract._build_alphatex(
        "T", None, ["E2", "A2", "D3", "G3", "B3", "E4"], None, []
    )
    assert "\\tuning E4 B3 G3 D3 A2 E2" in tex


def test_measure_quarter_length_scales_by_denominator():
    """6/8 and 3/4 both budget 3 quarters per measure - the denominator
    must scale the numerator, or 6/8 gets a budget of 6.0 (double every
    inferred duration)."""
    assert tabextract._measure_quarter_length((4, 4)) == 4.0
    assert tabextract._measure_quarter_length((3, 4)) == 3.0
    assert tabextract._measure_quarter_length((6, 8)) == 3.0
    assert tabextract._measure_quarter_length((9, 8)) == 4.5


def test_six_eight_and_three_four_extract_identically(zanarkand_pdf):
    """6/8 and 3/4 share the same 3-quarter measure budget, so rhythm
    inference (driven purely by that budget) must produce the same bars,
    notes, and duration codes for either - before the fix, 6/8 doubled the
    budget and every duration diverged."""
    three_four = tabextract.extract(zanarkand_pdf, time_signature=(3, 4))
    six_eight = tabextract.extract(zanarkand_pdf, time_signature=(6, 8))
    assert six_eight.extractable
    assert six_eight.bars == three_four.bars
    assert six_eight.notes == three_four.notes
    body_3_4 = three_four.alphatex.split(".\n", 1)[1]
    body_6_8 = six_eight.alphatex.split(".\n", 1)[1]
    assert body_3_4 == body_6_8


def test_escape_tex_string_handles_quotes_and_backslashes():
    assert tabextract._escape_tex_string('say "hi"') == 'say \\"hi\\"'
    assert tabextract._escape_tex_string("back\\slash") == "back\\\\slash"
    assert tabextract._escape_tex_string('mix "a"\\b') == 'mix \\"a\\"\\\\b'


def test_hostile_title_is_escaped_in_alphatex():
    """A filename stem containing a double quote or backslash used to be
    interpolated straight into \\title "...", producing alphaTex that
    alphaTab's parser rejects outright."""
    hostile_title = 'she said "hi"\\bye'
    tex = tabextract._build_alphatex(hostile_title, None, [], None, [])
    assert '\\title "she said \\"hi\\"\\\\bye"' in tex
    # The line still opens and closes with an unescaped quote - no stray
    # unescaped quote from the title broke out of the string early.
    first_line = tex.splitlines()[0]
    assert first_line == '\\title "she said \\"hi\\"\\\\bye"'


def test_rest_only_bar_is_emitted_not_skipped(tmp_path):
    """A bar with no digit columns must still produce a rest beat and a
    '|' separator - skipping it would shift every later bar's number one
    position earlier than the PDF, breaking side-by-side comparison."""
    pdf = _make_tab_pdf(
        tmp_path / "rest_bar.pdf",
        [
            {
                "notes": [(70, 1, "3"), (270, 5, "5")],
                "barline_xs": [50, 150, 250, 350],  # 3 measures; middle one empty
            }
        ],
    )
    result = tabextract.extract(pdf, time_signature=(4, 4))
    assert result.extractable
    assert result.bars == 3
    lines = result.alphatex.split(".\n", 1)[1].strip().splitlines()
    assert len(lines) == 3
    assert "3.1" in lines[0]
    assert lines[1].strip() == ":1 r |"
    assert "5.5" in lines[2]


def test_last_column_duration_uses_barline_not_average_gap():
    """The final column's duration must come from the gap to the measure's
    own barline, not the mean of the preceding gaps - averaging
    systematically shortened the last note of any bar ending on a long
    note."""
    columns = [{"x": 0.0}, {"x": 1.0}, {"x": 2.0}]
    durations = tabextract._infer_measure_rhythm(columns, measure_quarter_len=10.0, bar_end_x=10.0)
    # gaps: 1.0, 1.0, (10.0 - 2.0) = 8.0, total 10.0 -> proportional shares.
    assert durations == pytest.approx([1.0, 1.0, 8.0])


def test_tab_staff_count_is_summed_across_pages(tmp_path):
    """tab_staff_count (and standard_staff_count) must mean the same thing
    in analyze() and extract(): the total number of staff systems found
    across the whole document, not the maximum found on any single page."""
    pdf = _make_tab_pdf(
        tmp_path / "two_pages.pdf",
        [
            {"notes": [(70, 1, "3")], "barline_xs": [50, 150]},
            {"notes": [(70, 1, "4")], "barline_xs": [50, 150]},
        ],
    )
    info = tabextract.analyze(pdf)
    assert info["tab_staff_count"] == 2
    result = tabextract.extract(pdf, time_signature=(4, 4))
    assert result.tab_staff_count == 2
    assert result.pages_processed == 2


def test_digit_merge_rejects_impossible_frets():
    """Two adjacent single-digit notes close enough to look like one
    two-digit fret must not be merged if the result is above any real
    guitar's fret count - they stay as two separate notes instead of
    silently emitting nonsense like fret 59."""
    staff = tabextract._Staff("tab", [50, 60, 70, 80, 90, 100], 50, 350)

    def tok(text, x0, yc, width=5.0):
        return tabextract._DigitToken(text, (x0, yc - 3, x0 + width, yc + 3), "Helvetica", 7)

    impossible = [tok("5", 100.0, 50), tok("9", 101.5, 50)]
    merged, rejected, suspicious = tabextract._merge_multidigit(impossible, staff)
    assert rejected == 1
    assert suspicious == 0
    assert [n[2] for n in merged] == ["5", "9"]

    valid = [tok("2", 100.0, 50), tok("4", 101.5, 50)]
    merged2, rejected2, suspicious2 = tabextract._merge_multidigit(valid, staff)
    assert rejected2 == 0
    assert suspicious2 == 0
    assert [n[2] for n in merged2] == ["24"]


def test_digit_merge_flags_suspicious_native_two_digit_span():
    """A two-character span straight from the PDF's own text (not produced
    by our merge heuristic - e.g. Finale can emit a two-digit fret as one
    span, or two adjacent notes can end up rendered as one span) that's
    still above the sane fret ceiling must be flagged, not silently
    trusted. There's no safe way to split an already-single span back into
    two notes, so it's kept and counted rather than rejected."""
    staff = tabextract._Staff("tab", [50, 60, 70, 80, 90, 100], 50, 350)

    def tok(text, x0, yc, width=8.0):
        return tabextract._DigitToken(text, (x0, yc - 3, x0 + width, yc + 3), "Helvetica", 7)

    native_high = [tok("26", 100.0, 50)]
    merged, rejected, suspicious = tabextract._merge_multidigit(native_high, staff)
    assert rejected == 0
    assert suspicious == 1
    assert [n[2] for n in merged] == ["26"]

    native_valid = [tok("12", 100.0, 50)]
    merged2, rejected2, suspicious2 = tabextract._merge_multidigit(native_valid, staff)
    assert rejected2 == 0
    assert suspicious2 == 0
    assert [n[2] for n in merged2] == ["12"]


def test_tab_staff_found_but_no_digits_is_not_extractable(tmp_path):
    """Staves can be detected from vector staff lines even when no fret
    numbers could be read as text (e.g. an outlined-text export) - that
    must not be reported as a successful, empty extraction."""
    pdf = _make_tab_pdf(tmp_path / "no_digits.pdf", [{"notes": [], "barline_xs": [50, 150]}])
    info = tabextract.analyze(pdf)
    assert info["extractable"] is True  # staff lines alone look extractable
    result = tabextract.extract(pdf)
    assert result.extractable is False
    assert result.bars == 0
    assert result.notes == 0
    assert "no fret-number digits" in result.reason


# ---------------------------------------------------------------------------
# Rhythm-source resolution: staff pairing, provenance, and honest fallback
# ---------------------------------------------------------------------------


def _staff(kind, top, spacing=5.125, x0=50.0, x1=550.0):
    n = 6 if kind == "tab" else 5
    return tabextract._Staff(kind, [top + i * spacing for i in range(n)], x0, x1)


def test_tab_staff_pairs_with_the_notation_staff_in_its_own_system():
    """The ordinary score+tab layout: notation above, its tab below."""
    std = _staff("standard", 100.0)
    tab = _staff("tab", 100.0 + 4 * 5.125 + 27.0)
    pairs, reasons = tabextract._pair_standard_staves([std, tab])
    assert pairs[id(tab)] is std
    assert id(tab) not in reasons


def test_tab_staff_does_not_pair_across_systems():
    """A tab-only system below a notation-only system must NOT read that
    system's rhythm. Pairing on "nearest standard staff above, at any
    distance" x-matched a different line's notes onto this staff's fret
    columns - phantom rests and all - and reported high confidence."""
    std = _staff("standard", 100.0)          # notation-only system
    tab = _staff("tab", 400.0)               # tab-only system, far below
    pairs, reasons = tabextract._pair_standard_staves([std, tab])
    assert id(tab) not in pairs
    assert "own system" in reasons[id(tab)]


def test_tab_staff_does_not_pair_with_a_different_instruments_column():
    """A multi-instrument layout puts staves side by side; a notation staff
    that doesn't span the same horizontal extent isn't this staff's."""
    std = _staff("standard", 100.0, x0=50.0, x1=250.0)
    tab = _staff("tab", 100.0 + 4 * 5.125 + 27.0, x0=350.0, x1=550.0)
    pairs, reasons = tabextract._pair_standard_staves([std, tab])
    assert id(tab) not in pairs
    assert id(tab) in reasons


def test_one_notation_staff_is_read_by_only_one_tab_staff():
    """The lead+bass case. Letting two tab staves share one notation staff
    meant each rebuilt its measures from a fresh view of the same events, so
    every rest was emitted twice and each staff's pitched clusters went
    hunting through the other staff's fret columns - tagging bass columns
    with treble durations."""
    std = _staff("standard", 100.0)
    tab_a = _staff("tab", 100.0 + 4 * 5.125 + 27.0)
    tab_b = _staff("tab", 100.0 + 4 * 5.125 + 27.0 + 5 * 5.125 + 12.0)
    pairs, reasons = tabextract._pair_standard_staves([std, tab_a, tab_b])
    claimed = [t for t in (tab_a, tab_b) if id(t) in pairs]
    assert len(claimed) == 1, "a notation staff must not be read twice"
    assert claimed[0] is tab_a, "the nearer tab staff gets the notation staff"
    assert id(tab_b) in reasons
    assert "already read" in reasons[id(tab_b)] or "own system" in reasons[id(tab_b)]


def test_ambiguous_pairing_returns_no_notation_staff():
    """Two notation staves equally plausible above one tab staff is a guess,
    and a guess must degrade to the spacing heuristic rather than read a
    possibly-wrong line at high confidence."""
    gap = 27.0
    tab_top = 200.0
    std_a = _staff("standard", tab_top - 4 * 5.125 - gap)
    std_b = _staff("standard", tab_top - 4 * 5.125 - gap - 6.0)
    tab = _staff("tab", tab_top)
    pairs, reasons = tabextract._pair_standard_staves([std_a, std_b, tab])
    assert id(tab) not in pairs
    assert "guess" in reasons[id(tab)]


# ---------------------------------------------------------------------------
# Rest handling in a decoded measure (B1)
# ---------------------------------------------------------------------------


def _note_event(x, code, dots=0, rest=False, y=100.0, stem=None, stem_id=None,
                kind="notehead_filled", flags=0):
    """One decoded glyph event. `stem` is the stem direction ("up"/"down") and
    `stem_id` the identity of the stem it hangs off - two noteheads sharing a
    stem_id are a chord on one stem, which is what makes them one beat.

    `flags` is how many flag hooks or beam levels the decoder counted at the
    stem, which is how it actually shortens a filled notehead: base stays a
    quarter and each flag halves it. Passing `code` alone gives an unflagged
    notehead of that value.
    """
    base = {1: 4.0, 2: 2.0, 4: 1.0, 8: 0.5, 16: 0.25}[code] * (2 ** flags)
    key = stem_id if stem_id is not None else (None if stem is None else (stem, round(x, 1)))
    ev = glyph_rhythm.NoteEvent(
        x, y, base, flags, dots, rest,
        "rest_quarter" if rest else kind,
        notehead_kind=None if rest else kind,
        stem_key=key,
    )
    ev.stem_dir = stem
    return ev


def _cols(*xs):
    return [{"x": x, "xc": x + 2.0, "notes": [(1, "3")]} for x in xs]


def _col(x, *notes):
    return {"x": x, "xc": x + 2.0, "notes": list(notes)}


def _measure(cols, events, budget=None, x_tol=12.0, spacing=5.125):
    events = sorted(events, key=lambda n: n.x)
    return tabextract._build_measure_beats_glyph(
        cols, 90.0, 600.0, events, [n.x for n in events],
        x_tol=x_tol, notation_spacing=spacing, budget=budget)


def test_second_voice_rest_under_a_note_is_not_an_extra_beat():
    """Standard two-voice fingerstyle engraving - the library's core content.
    A voice-2 quarter rest engraved under a voice-1 note is the same beat,
    not an extra one: emitting both made a 3/4 bar hold four beats' worth and
    shifted the whole bar's playback while still reporting high confidence."""
    cols = _cols(100.0, 120.0, 140.0)
    events = [
        _note_event(102.0, 4), _note_event(122.0, 4), _note_event(142.0, 4),
        _note_event(102.3, 4, rest=True, y=118.0),  # voice 2 rest, same onset
    ]
    voices, unmatched_cols, unmatched_notes, _ = _measure(cols, events)
    assert len(voices) == 1, voices
    beats = voices[0]
    assert len(beats) == 3, beats
    assert all(notes for _, _, notes in beats), "no phantom rest beat"


def test_simultaneous_rests_in_two_voices_collapse_to_one_beat():
    cols = _cols(140.0)
    events = [
        _note_event(100.0, 4, rest=True, y=100.0),
        _note_event(100.4, 4, rest=True, y=118.0),
        _note_event(142.0, 4),
    ]
    voices, _, _, _ = _measure(cols, events)
    assert len(voices) == 1, voices
    rests = [b for b in voices[0] if not b[2]]
    assert len(rests) == 1, voices


def test_a_genuinely_separate_rest_is_still_its_own_beat():
    """The dedupe must not swallow a real rest that sits on its own onset."""
    cols = _cols(100.0)
    events = [_note_event(102.0, 4), _note_event(140.0, 4, rest=True)]
    voices, _, _, _ = _measure(cols, events)
    assert len(voices) == 1
    assert [bool(n) for _, _, n in voices[0]] == [True, False]


# ---------------------------------------------------------------------------
# Voice separation
# ---------------------------------------------------------------------------


def _quarters(beats):
    return sum(tabextract._beat_quarters(code, dots) for code, dots, _n in beats)


def test_stem_direction_splits_a_bar_into_concurrent_voices():
    """The library's core content: a melody in quarters, stems up, over an
    accompaniment in eighths, stems down. Assembled into one sequence the 3/4
    bar holds 6 quarters; as two voices each holds exactly 3."""
    xs_mel = [100.0, 140.0, 180.0]
    xs_bass = [100.0, 120.0, 140.0, 160.0, 180.0, 200.0]
    events = [_note_event(x, 4, y=60.0, stem="up") for x in xs_mel]
    events += [_note_event(x, 8, y=120.0, stem="down") for x in xs_bass]
    cols = [_col(100.0, (1, "7"), (5, "3")), _col(120.0, (4, "5")),
            _col(140.0, (1, "7"), (2, "3")), _col(160.0, (3, "5")),
            _col(180.0, (1, "7"), (2, "5")), _col(200.0, (2, "7"))]
    voices, _, unmatched_notes, inferred = _measure(cols, events, budget=3.0)

    assert len(voices) == 2, voices
    assert unmatched_notes == 0
    assert _quarters(voices[0]) == 3.0
    assert _quarters(voices[1]) == 3.0
    assert inferred == 0.0, "both voices already account for the bar"
    # The upper voice is the melody: three quarters, each one note.
    assert [(c, d) for c, d, _n in voices[0]] == [(4, 0)] * 3
    assert [(c, d) for c, d, _n in voices[1]] == [(8, 0)] * 6


def test_a_shared_onset_splits_its_tab_digits_by_pitch_between_the_voices():
    """Two simultaneous notes in different voices are two tab digits on
    different strings - which is also exactly what a chord looks like. The
    split cannot come from the tab, so it comes from the notation: noteheads
    ordered by pitch against digits ordered by string."""
    events = [
        _note_event(100.0, 4, y=60.0, stem="up"),     # melody, high
        _note_event(100.0, 8, y=120.0, stem="down"),  # bass, low
    ]
    cols = [_col(100.0, (1, "7"), (5, "3"))]
    voices, _, _, _ = _measure(cols, events, budget=3.0)
    assert len(voices) == 2
    assert voices[0][0][2] == [(1, "7")], "the melody takes the high string"
    assert voices[1][0][2] == [(5, "3")], "the bass takes the low string"


def test_a_chord_on_one_stem_stays_one_beat_in_one_voice():
    """Several noteheads on ONE stem is a chord: one beat, however many tab
    digits are stacked under it. Matching each notehead separately let a
    chord's members go hunting for their own columns."""
    events = [
        _note_event(100.0, 4, y=60.0, stem="down", stem_id="A"),
        _note_event(100.0, 4, y=70.0, stem="down", stem_id="A"),
        _note_event(100.0, 4, y=80.0, stem="down", stem_id="A"),
    ]
    cols = [_col(100.0, (1, "7"), (2, "8"), (3, "5"))]
    voices, unmatched_cols, unmatched_notes, _ = _measure(cols, events, budget=3.0)
    assert len(voices) == 1, "a chord is not two voices"
    assert len(voices[0]) == 1, voices
    assert voices[0][0][2] == [(1, "7"), (2, "8"), (3, "5")]
    assert unmatched_cols == 0 and unmatched_notes == 0


def test_a_chord_takes_the_duration_of_the_notehead_that_saw_the_stem():
    """Only the notehead at a stem's end can see the flag or beam that
    shortens the chord; the inner members read a plain quarter and would
    outvote it."""
    events = [
        # the notehead at the stem's end: a quarter head with one beam level
        _note_event(100.0, 8, y=60.0, stem="up", stem_id="A", flags=1),
        _note_event(100.0, 4, y=70.0, stem="up", stem_id="A"),
        _note_event(100.0, 4, y=80.0, stem="up", stem_id="A"),
    ]
    cols = [_col(100.0, (1, "7"), (2, "8"), (3, "5"))]
    voices, _, _, _ = _measure(cols, events)
    assert [(c, d) for c, d, _n in voices[0]] == [(8, 0)]


def test_a_chord_with_no_stem_found_is_not_mistaken_for_two_voices():
    """Some scores engrave chords whose stem the vector pass cannot see at
    all. Stems are what two-voice writing is notated WITH, so an onset with
    no stem evidence is a chord - splitting it by pitch invented a second
    voice out of one chord and left both halves short."""
    events = [_note_event(100.0, 4, y=y) for y in (60.0, 70.0, 80.0, 95.0)]
    cols = [_col(100.0, (2, "5"), (3, "5"), (4, "5"), (5, "8"))]
    voices, _, _, _ = _measure(cols, events, budget=4.0)
    assert len(voices) == 1, voices
    assert len(voices[0]) == 1, "one chord, one beat"
    assert len(voices[0][0][2]) == 4


def test_two_stems_the_same_way_up_at_one_onset_are_one_chord():
    """Not three-voice writing - one chord whose stem came through the vector
    pass as two separate strokes."""
    events = [
        _note_event(100.0, 4, y=60.0, stem="down", stem_id="A"),
        _note_event(100.0, 4, y=75.0, stem="down", stem_id="B"),
    ]
    cols = [_col(100.0, (1, "7"), (3, "5"))]
    voices, _, _, _ = _measure(cols, events, budget=3.0)
    assert len(voices) == 1, voices
    assert len(voices[0]) == 1


def test_a_monophonic_bar_stays_one_voice_when_its_stems_flip():
    """Single-voice writing flips stem direction with pitch around the middle
    line, so direction alone would shred a melody crossing it into two
    voices. Nothing sounds together here, so nothing is polyphonic."""
    events = [
        _note_event(100.0, 4, y=60.0, stem="down"),
        _note_event(140.0, 4, y=130.0, stem="up"),
        _note_event(180.0, 4, y=55.0, stem="down"),
    ]
    cols = [_col(100.0, (1, "5")), _col(140.0, (5, "3")), _col(180.0, (1, "7"))]
    voices, _, _, inferred = _measure(cols, events, budget=3.0)
    assert len(voices) == 1, voices
    assert len(voices[0]) == 3
    assert inferred == 0.0, "a monophonic bar is never padded with inferred rests"


def test_a_whole_note_with_no_stem_joins_the_voice_below_the_melody():
    """A whole note never takes a stem in any notation, so the only signal
    left is where it sits relative to the voice that does have one."""
    events = [_note_event(x, 4, y=60.0, stem="up") for x in (100.0, 140.0, 180.0, 220.0)]
    events.append(_note_event(100.0, 1, y=130.0, kind="notehead_whole"))
    cols = [_col(100.0, (1, "7"), (6, "3")), _col(140.0, (1, "8")),
            _col(180.0, (1, "9")), _col(220.0, (1, "10"))]
    voices, _, _, _ = _measure(cols, events, budget=4.0)
    assert len(voices) == 2, voices
    assert _quarters(voices[0]) == 4.0
    assert _quarters(voices[1]) == 4.0
    assert voices[1][0][2] == [(6, "3")], "the whole note is the lower voice"


def test_a_silent_voice_is_padded_with_rests_so_it_fills_the_bar():
    """This is what actually makes the arithmetic work: without it every
    voice but the busiest reads as a short bar and drifts against it."""
    events = [_note_event(100.0, 4, y=60.0, stem="up")]
    events += [_note_event(x, 4, y=130.0, stem="down") for x in (100.0, 140.0, 180.0)]
    cols = [_col(100.0, (1, "7"), (5, "3")), _col(140.0, (5, "5")), _col(180.0, (5, "7"))]
    voices, _, _, inferred = _measure(cols, events, budget=3.0)
    assert len(voices) == 2
    assert _quarters(voices[0]) == 3.0
    assert _quarters(voices[1]) == 3.0
    assert inferred == 2.0, "the upper voice was silent for two of the three beats"
    # the note sounds first, then the inferred silence - spelled as the one
    # half rest that covers it, not two quarter rests
    assert voices[0] == [(4, 0, [(1, "7")]), (2, 0, [])], voices[0]


def test_a_voice_that_enters_late_is_padded_at_the_front():
    events = [_note_event(180.0, 4, y=60.0, stem="up")]
    events += [_note_event(x, 4, y=130.0, stem="down") for x in (100.0, 140.0, 180.0)]
    cols = [_col(100.0, (5, "3")), _col(140.0, (5, "5")),
            _col(180.0, (1, "7"), (5, "7"))]
    voices, _, _, _ = _measure(cols, events, budget=3.0)
    assert len(voices) == 2
    assert _quarters(voices[0]) == 3.0
    assert voices[0][0][2] == [], "silence first"
    assert voices[0][-1][2] == [(1, "7")], "then the note it enters on"


def test_rest_beats_for_fills_a_meter_exactly():
    assert tabextract._rest_beats_for(3.0) == [(2, 0, []), (4, 0, [])]
    assert tabextract._rest_beats_for(4.0) == [(1, 0, [])]
    assert tabextract._rest_beats_for(1.5) == [(4, 0, []), (8, 0, [])]


def test_an_empty_bar_is_filled_with_rests_that_add_up():
    """A 3/4 bar's 3.0 quarters snap to a WHOLE rest, which is a third longer
    than the bar - an empty bar has to be spelled with rests that sum to the
    meter instead."""
    tex = tabextract._build_alphatex(
        "T", None, [], (3, 4), [(tabextract._rest_beats_for(3.0), (3, 4))])
    body = tex.split("\n.\n", 1)[1].strip()
    assert body == ":2 r :4 r |", body


def test_concurrent_voices_are_emitted_with_the_voice_separator():
    """alphaTex spells concurrent voices inside a bar with `\\voice`, which
    only means that with `\\voicemode barwise` in the header - the default
    reads it as "restart the staff for the next voice"."""
    measures = [
        ([[(4, 0, [(1, "7")])] * 3, [(8, 0, [(5, "3")])] * 6], (3, 4)),
        ([[(4, 0, [(1, "7")])] * 3], (3, 4)),
    ]
    tex = tabextract._build_alphatex("T", None, [], (3, 4), measures)
    head, body = tex.split("\n.\n", 1)
    assert "\\voicemode barwise" in head
    lines = body.strip().splitlines()
    assert lines[0].count("\\voice") == 1, lines[0]
    assert lines[1].count("\\voice") == 0, "a monophonic bar emits no second voice"


def test_a_monophonic_score_does_not_declare_a_voice_mode():
    """The directive only appears where it does something."""
    tex = tabextract._build_alphatex(
        "T", None, [], (3, 4), [([[(4, 0, [(1, "7")])] * 3], (3, 4))])
    assert "\\voicemode" not in tex


def test_bar_quarters_is_the_longest_voice_not_the_sum():
    """Voices sound CONCURRENTLY. Summing them is exactly the mistake that
    made a bar of two-voice writing read as double its meter."""
    bar = [[(4, 0, [])] * 3, [(8, 0, [])] * 6]
    assert tabextract._bar_quarters(bar) == 3.0
    assert tabextract._overfull_bars([(bar, (3, 4))]) == (0, 1)
    # a voice that really does overflow is still caught
    over = [[(4, 0, [])] * 3, [(4, 0, [])] * 4]
    assert tabextract._overfull_bars([(over, (3, 4))]) == (1, 1)


# ---------------------------------------------------------------------------
# Bar fullness on a real reference score
# ---------------------------------------------------------------------------

_DUR_TOKEN = re.compile(r"^:(\d+)$")


def _emitted_voice_quarters(segment):
    """Quarter notes in one emitted voice of one bar, read back out of the
    alphaTex itself - so this measures the artifact that gets stored and
    rendered, not an intermediate the emitter might not agree with."""
    total = 0.0
    dur = None
    dots = 0
    for tok in segment.split():
        m = _DUR_TOKEN.match(tok)
        if m:
            if dur:
                total += tabextract._beat_quarters(dur, dots)
            dur, dots = int(m.group(1)), 0
            continue
        if "{dd}" in tok:
            dots = 2
        elif "{d}" in tok:
            dots = 1
    if dur:
        total += tabextract._beat_quarters(dur, dots)
    return total


def _emitted_bars(alphatex):
    """[(budget, [voice quarters, ...]), ...] for every emitted bar."""
    header, body = alphatex.split("\n.\n", 1)
    head = re.search(r"\\ts\s+(\d+)\s+(\d+)", header)
    ts = (int(head.group(1)), int(head.group(2))) if head else (4, 4)
    out = []
    for line in body.strip().splitlines():
        m = re.match(r"\s*\\ts\s+(\d+)\s+(\d+)\s*", line)
        if m:
            ts = (int(m.group(1)), int(m.group(2)))
            line = line[m.end():]
        bar = line.rstrip().rstrip("|").strip()
        voices = [s.strip() for s in bar.split("\\voice")]
        out.append((tabextract._measure_quarter_length(ts),
                    [_emitted_voice_quarters(v) for v in voices]))
    return out


def test_reference_score_bars_mostly_add_up(zanarkand_pdf):
    """To Zanarkand is two-voice fingerstyle writing throughout: a melody over
    an independent bass line. Assembled into one voice per bar, 37 of its 50
    bars held more than their meter (only 24% summed exactly, 72.5 quarters of
    total error) with every individual duration decoded correctly. Separating
    the voices is what fixes the arithmetic, so pin it: this is the metric the
    feature exists to move.
    """
    result = tabextract.extract(zanarkand_pdf)
    assert result.extractable
    bars = _emitted_bars(result.alphatex)
    assert len(bars) == 50, len(bars)

    exact = sum(1 for budget, vs in bars
                if all(abs(v - budget) < 1e-6 for v in vs))
    overfull = sum(1 for budget, vs in bars if any(v > budget + 1e-6 for v in vs))
    error = sum(abs(v - budget) for budget, vs in bars for v in vs)
    multivoice = sum(1 for _b, vs in bars if len(vs) > 1)

    assert exact >= 44, f"only {exact} of 50 bars sum exactly (was 12 before voices)"
    assert overfull <= 6, f"{overfull} bars overfull (was 37 before voices)"
    assert error <= 12.0, f"{error} quarters of error (was 72.5 before voices)"
    assert multivoice >= 30, f"only {multivoice} bars came out as two voices"


def test_reference_score_still_reports_the_bars_that_do_not_add_up(zanarkand_pdf):
    """A much smaller number, but it must still be reported rather than
    disappearing - the remaining bars really do play wrong."""
    result = tabextract.extract(zanarkand_pdf)
    fullness = [w for w in result.warnings if "hold more than their time signature" in w]
    assert len(fullness) == 1, result.warnings
    assert re.search(r"\b\d+ of 50 bar\(s\)", fullness[0]), fullness[0]
    assert any("concurrent voices" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Decoder honesty stats drive confidence (finding 3)
# ---------------------------------------------------------------------------


def _resolve_with_stats(monkeypatch, stats, notes=None):
    """Run the rhythm-source resolver against a stubbed decoder result."""
    if notes is None:
        notes = [_note_event(100.0, 8), _note_event(120.0, 8)]
    monkeypatch.setattr(
        tabextract.glyph, "decode_note_events",
        lambda *a, **k: (notes, stats),
    )
    return tabextract._resolve_rhythm_source(
        object(), _staff("standard", 100.0), None, {})


def _stats(**over):
    base = {
        "unknown_glyphs": 0, "unknown_ratio": 0.0, "unknown_at_flag_position": 0,
        "unknown_gid_or_name_sample": [], "band_glyphs": 40, "note_events": 2,
        "font_warnings": [],
    }
    base.update(over)
    return base


def test_clean_decode_is_reported_as_glyph_sourced(monkeypatch):
    src = _resolve_with_stats(monkeypatch, _stats())
    assert src.provenance == tabextract.PROV_GLYPHS
    assert src.uses_glyphs


def test_unknown_glyphs_at_flag_positions_downgrade_confidence(monkeypatch):
    """The decoder tracks glyphs outside its calibrated vocabulary precisely
    because an unmapped flag decodes as a systematically wrong duration while
    every other signal still looks healthy. The caller must ACT on that
    instead of binding it to a throwaway variable: a piece using 32nd flags
    or grace notes decoded with wrong durations while reporting "high"."""
    src = _resolve_with_stats(monkeypatch, _stats(
        unknown_glyphs=2, unknown_ratio=0.05, unknown_at_flag_position=2,
        unknown_gid_or_name_sample=[("Maestro", 222)]))
    assert src.provenance == tabextract.PROV_GLYPHS_DEGRADED
    assert src.uses_glyphs, "still usable, just not high confidence"
    assert "flag attaches" in src.detail
    # the unrecognised glyphs are named so they can be calibrated later
    assert "222" in src.detail


def test_mostly_unrecognised_vocabulary_falls_back_entirely(monkeypatch):
    src = _resolve_with_stats(monkeypatch, _stats(
        unknown_glyphs=30, unknown_ratio=0.75, band_glyphs=40,
        unknown_gid_or_name_sample=[("Maestro", 222)]))
    assert src.provenance == tabextract.PROV_SPACING
    assert not src.uses_glyphs
    assert "cannot be trusted" in src.detail


def test_high_unknown_ratio_alone_downgrades(monkeypatch):
    src = _resolve_with_stats(monkeypatch, _stats(
        unknown_glyphs=8, unknown_ratio=0.20, band_glyphs=40))
    assert src.provenance == tabextract.PROV_GLYPHS_DEGRADED


def test_no_decoded_events_falls_back_with_the_font_reason(monkeypatch):
    src = _resolve_with_stats(
        monkeypatch, _stats(font_warnings=["Opus is embedded with 'cff' outlines"]), notes=[])
    assert src.provenance == tabextract.PROV_SPACING
    assert "cff" in src.detail


def test_unpaired_tab_staff_falls_back_without_decoding():
    src = tabextract._resolve_rhythm_source(
        object(), None, "no notation staff in this tab staff's own system", {})
    assert src.provenance == tabextract.PROV_SPACING
    assert "own system" in src.detail


def test_rhythm_report_downgrades_on_degraded_staves():
    counts = collections.Counter({tabextract.PROV_GLYPHS: 4,
                                  tabextract.PROV_GLYPHS_DEGRADED: 2})
    warnings, confidence = tabextract._rhythm_report(counts, {})
    assert confidence.startswith("medium")
    assert any("not been calibrated" in w for w in warnings)


def test_overfull_bars_are_reported_and_cap_the_confidence():
    """Every duration can be glyph-decoded correctly and the bar still be
    wrong, because merged voices overfill it. That must not read as high
    confidence, and the count must reach the user."""
    all_glyph = collections.Counter({tabextract.PROV_GLYPHS: 5})
    w, c = tabextract._rhythm_report(all_glyph, {}, overfull=37, bars=50)
    assert any("37 of 50 bar(s) hold more than their time signature" in x for x in w)
    assert any("two voices" in x for x in w)
    assert not c.startswith("high")
    assert "37 of 50" in c

    # A stray overfull bar is worth reporting but shouldn't condemn the score.
    w2, c2 = tabextract._rhythm_report(all_glyph, {}, overfull=1, bars=50)
    assert any("1 of 50 bar(s) hold more" in x for x in w2)
    assert c2.startswith("high")


def test_bar_quarters_accounts_for_dots():
    # 4 = quarter; one dot is 1.5 quarters, two dots 1.75.
    assert tabextract._bar_quarters([(4, 0, [])]) == 1.0
    assert tabextract._bar_quarters([(4, 1, [])]) == 1.5
    assert tabextract._bar_quarters([(4, 2, [])]) == 1.75
    # A 3/4 bar filled by three quarters is not overfull; four quarters is.
    assert tabextract._overfull_bars([([(4, 0, [])] * 3, (3, 4))]) == (0, 1)
    assert tabextract._overfull_bars([([(4, 0, [])] * 4, (3, 4))]) == (1, 1)


def test_rhythm_report_is_the_single_source_of_warnings_and_confidence():
    """All-glyph, mixed and all-spacing must each produce a confidence string
    that agrees with the warnings beside it - they are derived together."""
    all_glyph = collections.Counter({tabextract.PROV_GLYPHS: 5})
    w, c = tabextract._rhythm_report(all_glyph, {})
    assert c.startswith("high")
    assert not any("inferred from horizontal spacing" in x for x in w)

    mixed = collections.Counter({tabextract.PROV_GLYPHS: 3, tabextract.PROV_SPACING: 2})
    w, c = tabextract._rhythm_report(mixed, {})
    assert c.startswith("mixed")
    assert any("rougher estimate from note spacing" in x for x in w)

    none = collections.Counter({tabextract.PROV_SPACING: 4})
    w, c = tabextract._rhythm_report(none, {})
    assert c.startswith("low")
    assert any("inferred from horizontal spacing" in x for x in w)


# ---------------------------------------------------------------------------
# Time signature: validation and the per-measure timeline (A1, finding 4)
# ---------------------------------------------------------------------------


def test_emitted_time_signature_is_always_usable(zanarkand_pdf):
    """A glyph-decoded signature is written into \\ts and STORED, and alphaTab
    throws outright on something like `\\ts 3 12`, so an unvalidated decode
    produces a saved transcription that can never be rendered again."""
    result = tabextract.extract(zanarkand_pdf)
    assert glyph_rhythm.time_signature_is_valid(result.time_signature)
    for line in result.alphatex.splitlines():
        m = re.search(r"\\ts\s+(\d+)\s+(\d+)", line)
        if m:
            assert glyph_rhythm.time_signature_is_valid((int(m.group(1)), int(m.group(2)))), line


def test_unusable_manual_override_is_ignored_not_emitted(zanarkand_pdf):
    result = tabextract.extract(zanarkand_pdf, time_signature=(3, 12))
    assert result.extractable
    assert glyph_rhythm.time_signature_is_valid(result.time_signature)
    assert result.time_signature_source != "manual override"
    assert any("not a usable meter" in w for w in result.warnings)


def test_zero_denominator_signature_never_reaches_the_measure_budget():
    """A denominator of 0 out of the plain-text digit scan used to reach
    _measure_quarter_length and raise ZeroDivisionError, which extract()'s
    blanket handler turned into `extractable: false` for a PDF whose fret
    digits were perfectly readable."""
    assert not glyph_rhythm.time_signature_is_valid((4, 0))
    # and the budget helper is only ever reached with a validated signature
    assert tabextract._measure_quarter_length((4, 4)) == 4.0


def test_mid_score_meter_change_is_carried_into_the_transcription():
    """Bar-level meter is taken from a timeline, so a change part-way through
    is emitted where it happens instead of every later bar being measured
    against the opening meter."""
    measures = [
        ([(4, 0, [(1, "3")])], (4, 4)),
        ([(4, 0, [(1, "3")])], (4, 4)),
        ([(4, 0, [(1, "3")])], (7, 8)),
        ([(4, 0, [(1, "3")])], (7, 8)),
        ([(4, 0, [(1, "3")])], (4, 4)),
    ]
    tex = tabextract._build_alphatex("T", None, [], (4, 4), measures)
    body = tex.split("\n.\n", 1)[1].strip().splitlines()
    assert len(body) == 5
    assert not body[0].startswith("\\ts")   # already in effect from the header
    assert not body[1].startswith("\\ts")
    assert body[2].startswith("\\ts 7 8")   # change emitted exactly here
    assert not body[3].startswith("\\ts")   # ...and not repeated
    assert body[4].startswith("\\ts 4 4")   # change back


def test_ts_timeline_lookup_uses_the_last_meter_printed_before_a_position():
    timeline = [(0, 100.0, (4, 4)), (1, 200.0, (7, 8)), (3, 50.0, (4, 4))]
    assert tabextract._ts_at(timeline, 0, 150.0) == (4, 4)
    assert tabextract._ts_at(timeline, 1, 100.0) == (4, 4)   # before the change
    assert tabextract._ts_at(timeline, 1, 250.0) == (7, 8)
    assert tabextract._ts_at(timeline, 2, 999.0) == (7, 8)   # carried forward
    assert tabextract._ts_at(timeline, 3, 60.0) == (4, 4)
    assert tabextract._ts_at(timeline, 0, 10.0) is None      # before anything


# ---------------------------------------------------------------------------
# Fingerprint rejection, end to end (finding 1)
# ---------------------------------------------------------------------------


def test_unrecognised_maestro_falls_back_honestly(zanarkand_pdf, monkeypatch):
    """An honest fallback with a warning beats confidently-wrong output. A
    font that keeps the name "Maestro" but not the calibrated outlines must
    take the whole document to the spacing heuristic AND say why - and must
    NOT emit a time signature read from digit GIDs it can no longer trust."""
    monkeypatch.setattr(
        glyph_rhythm, "MAESTRO_GLYF_DIGESTS",
        {gid: "0" * 32 for gid in glyph_rhythm.MAESTRO_GID_MAP},
    )
    glyph_rhythm.clear_caches()
    try:
        result = tabextract.extract(zanarkand_pdf)
    finally:
        glyph_rhythm.clear_caches()

    # fret extraction is untouched - it never needed the music font
    assert result.extractable
    assert result.notes > 300
    # ...but rhythm is honest about being an estimate
    assert result.rhythm_provenance == {tabextract.PROV_SPACING: 10}
    assert result.confidence["rhythm"].startswith("low")
    assert any("NOT the calibrated Maestro subset" in w for w in result.warnings)
    # and no confidently-wrong time signature from untrusted digit GIDs
    assert result.time_signature_source != "glyph-decoded"


def test_calibrated_maestro_still_decodes(zanarkand_pdf):
    """The other direction: the library's own files must keep decoding, or
    the fingerprint is just an outage."""
    glyph_rhythm.clear_caches()
    result = tabextract.extract(zanarkand_pdf)
    assert result.rhythm_provenance == {tabextract.PROV_GLYPHS: 10}
    assert "own engraving" in result.confidence["rhythm"]
    assert result.time_signature_source == "glyph-decoded"
    assert not any("NOT the calibrated Maestro subset" in w for w in result.warnings)
