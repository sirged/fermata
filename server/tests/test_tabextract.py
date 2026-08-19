import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import fitz
import pytest

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
    assert result.confidence["rhythm"].startswith("high")


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
