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
from fermata import musicxml
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


def _load_musicxml_with_alphatab(xml: str, onsets: bool = False) -> dict:
    """Load `xml` with the real alphaTab MusicXML importer the web player uses,
    via tools/tab_extract/verify_musicxml.mjs.

    A stored transcription IS MusicXML and the player imports it with this
    exact loader, so nothing about how the file is written can be called safe
    on the strength of the emitter agreeing with itself. Rule 14's `<forward>`
    is the sharp case: a loader that ignored it would still load the file, and
    every voice that entered late would collapse onto the downbeat. Skips
    (rather than fails) when node or the web project's installed alphaTab build
    aren't available, since neither is present in the production server's own
    runtime image.
    """
    if shutil.which("node") is None:
        pytest.skip("node not available")
    repo_root = Path(__file__).resolve().parents[2]
    alphatab = repo_root / "web" / "node_modules" / "@coderline" / "alphatab" / "dist" / "alphaTab.mjs"
    if not alphatab.is_file():
        pytest.skip("alphaTab.mjs not found - run `npm ci` in web/ first")
    script = Path(__file__).resolve().parents[1] / "tools" / "tab_extract" / "verify_musicxml.mjs"

    with tempfile.NamedTemporaryFile("w", suffix=".musicxml", delete=False,
                                     encoding="utf-8") as f:
        f.write(xml)
        xml_path = f.name
    try:
        args = ["node", str(script), str(alphatab)]
        if onsets:
            args.append("--onsets")
        proc = subprocess.run(args + [xml_path], capture_output=True, text=True, timeout=60)
    finally:
        Path(xml_path).unlink(missing_ok=True)

    parsed = json.loads(proc.stdout.strip().splitlines()[-1])
    assert parsed.get("ok"), f"MusicXML failed to load: {parsed.get('error')}"
    return parsed


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
    import xml.etree.ElementTree as ET

    result = tabextract.extract(zanarkand_pdf, time_signature=(3, 4))
    assert result.extractable
    parsed = _parse_with_alphatab(result.alphatex)
    assert parsed["bars"] == result.bars
    # `beats` is what the CANONICAL output holds, and the two formats no longer
    # hold the same number: inferred silence is a plain rest in alphaTex (which
    # has nothing to mark it with) and a `<forward>` in the MusicXML, which is
    # not a beat of the score. So the alphaTex has exactly one beat more per
    # `<forward>`, and stating the relationship pins both artefacts instead of
    # letting `beats` drift towards whichever one is checked.
    forwards = len(ET.fromstring(result.musicxml).findall("./part/measure/forward"))
    assert forwards > 0, "this score does have inferred silence in it"
    assert parsed["beats"] == result.beats + forwards
    assert parsed["notes"] == result.notes
    assert parsed["dottedBeats"] > 0
    assert parsed["firstNoteMidi"] == 64
    # This piece is two-voice fingerstyle writing, so the emitted `\voice`
    # separators must actually have landed their beats in a SECOND concurrent
    # voice - more sounding voices than bars - rather than merely parsing.
    assert parsed["voices"] > parsed["bars"], parsed


def _measure_voice_quarters(xml: str, number: int) -> dict:
    """{voice: quarter notes} for one emitted measure, chord members counted
    once because they sound with the note they hang off."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    for part in root.findall("part"):
        for m in part.findall("measure"):
            if int(m.get("number")) != number:
                continue
            divisions = None
            for attrs in part.iter("attributes"):
                d = attrs.findtext("divisions")
                if d:
                    divisions = int(d)
                    break
            out = collections.defaultdict(float)
            for note in m.findall("note"):
                if note.find("chord") is not None:
                    continue
                out[(note.findtext("voice") or "1").strip()] += (
                    int(note.findtext("duration") or 0) / divisions)
            return dict(out)
    raise AssertionError(f"measure {number} is not in the emitted score")


def test_a_melody_over_a_chord_stays_a_separate_voice(dalza_pdf):
    """End-to-end for the notehead-to-stem attachment this file is full of: a
    melody eighth one staff space above a stem-down half-note chord, sharing
    its beat. Attaching the melody note to the CHORD's stem folds the two
    voices into one, and the bar then reports 6 half-note units of a single
    voice in 2/2 - a fourfold duration error with the second voice gone, not a
    near miss. Every one of these bars carries the figure."""
    result = tabextract.extract(dalza_pdf)
    assert result.extractable
    assert result.time_signature == (2, 2)
    for bar in (7, 11, 12, 13, 14):
        voices = _measure_voice_quarters(result.musicxml, bar)
        assert len(voices) == 2, f"bar {bar} lost a voice: {voices}"
        assert voices["1"] == 4.0, f"bar {bar} voice 1: {voices}"


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

    # ...and that bar is REPORTED as one nothing was read from. Its rests add up
    # to the meter, so Rule 8 passes and it is not a defect - which is exactly
    # why it has to be said some other way, or a score read as nothing at all
    # reports every bar conformant. The rest is left unmarked deliberately: a
    # whole voice of `<forward>` would carry no notes and no rests, so no
    # consumer enumerating voices from <note> would see the bar at all.
    assert result.bars_unread == 1
    assert result.unread_bars == [2]
    assert result.bars_defective == 0, "a bar of rests does add up"
    assert result.bars_padded == 0
    assert "<forward>" not in result.musicxml
    unread = next(w for w in result.warnings if "hold nothing that was read" in w)
    assert "1 of 3 bar(s)" in unread
    assert "The bars are: 2." in unread


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


def _inferred_quarters(voices):
    """Quarter notes of silence the builder MARKED as inferred, read off the
    beats it produced.

    Deliberately not a number the builder returns alongside them: what has to
    be true is that the beats themselves carry the mark, because the mark is
    what the conformance count and both emitters read. A returned total can be
    right while the beats it describes are unmarked - which is the whole bug
    this pins - so measuring it here would be measuring the wrong artifact.
    """
    return sum(tabextract._beat_quarters(code, dots)
               for voice in voices for code, dots, notes in voice
               if musicxml.is_inferred_rest(notes))


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
    voices, unmatched_cols, unmatched_notes = _measure(cols, events)
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
    voices, _, _ = _measure(cols, events)
    assert len(voices) == 1, voices
    rests = [b for b in voices[0] if not b[2]]
    assert len(rests) == 1, voices


def test_a_genuinely_separate_rest_is_still_its_own_beat():
    """The dedupe must not swallow a real rest that sits on its own onset."""
    cols = _cols(100.0)
    events = [_note_event(102.0, 4), _note_event(140.0, 4, rest=True)]
    voices, _, _ = _measure(cols, events)
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
    voices, _, unmatched_notes = _measure(cols, events, budget=3.0)

    assert len(voices) == 2, voices
    assert unmatched_notes == 0
    assert _quarters(voices[0]) == 3.0
    assert _quarters(voices[1]) == 3.0
    assert _inferred_quarters(voices) == 0.0, "both voices already account for the bar"
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
    voices, _, _ = _measure(cols, events, budget=3.0)
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
    voices, unmatched_cols, unmatched_notes = _measure(cols, events, budget=3.0)
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
    voices, _, _ = _measure(cols, events)
    assert [(c, d) for c, d, _n in voices[0]] == [(8, 0)]


def test_a_chord_with_no_stem_found_is_not_mistaken_for_two_voices():
    """Some scores engrave chords whose stem the vector pass cannot see at
    all. Stems are what two-voice writing is notated WITH, so an onset with
    no stem evidence is a chord - splitting it by pitch invented a second
    voice out of one chord and left both halves short."""
    events = [_note_event(100.0, 4, y=y) for y in (60.0, 70.0, 80.0, 95.0)]
    cols = [_col(100.0, (2, "5"), (3, "5"), (4, "5"), (5, "8"))]
    voices, _, _ = _measure(cols, events, budget=4.0)
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
    voices, _, _ = _measure(cols, events, budget=3.0)
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
    voices, _, _ = _measure(cols, events, budget=3.0)
    assert len(voices) == 1, voices
    assert len(voices[0]) == 3
    assert _inferred_quarters(voices) == 0.0, "a monophonic bar is never padded"


def test_a_whole_note_with_no_stem_joins_the_voice_below_the_melody():
    """A whole note never takes a stem in any notation, so the only signal
    left is where it sits relative to the voice that does have one."""
    events = [_note_event(x, 4, y=60.0, stem="up") for x in (100.0, 140.0, 180.0, 220.0)]
    events.append(_note_event(100.0, 1, y=130.0, kind="notehead_whole"))
    cols = [_col(100.0, (1, "7"), (6, "3")), _col(140.0, (1, "8")),
            _col(180.0, (1, "9")), _col(220.0, (1, "10"))]
    voices, _, _ = _measure(cols, events, budget=4.0)
    assert len(voices) == 2, voices
    assert _quarters(voices[0]) == 4.0
    assert _quarters(voices[1]) == 4.0
    assert voices[1][0][2] == [(6, "3")], "the whole note is the lower voice"


def test_a_silent_voice_is_padded_with_rests_so_it_fills_the_bar():
    """The padding is what keeps the voices in time with each other: without it
    every voice but the busiest reads as a short bar and drifts against it."""
    events = [_note_event(100.0, 4, y=60.0, stem="up")]
    events += [_note_event(x, 4, y=130.0, stem="down") for x in (100.0, 140.0, 180.0)]
    cols = [_col(100.0, (1, "7"), (5, "3")), _col(140.0, (5, "5")), _col(180.0, (5, "7"))]
    voices, _, _ = _measure(cols, events, budget=3.0)
    assert len(voices) == 2
    assert _quarters(voices[0]) == 3.0
    assert _quarters(voices[1]) == 3.0
    assert _inferred_quarters(voices) == 2.0, "the upper voice was silent for two of three beats"
    # the note sounds first, then the inferred silence - spelled as the one
    # half rest that covers it, not two quarter rests
    assert voices[0] == [(4, 0, [(1, "7")]), (2, 0, [])], voices[0]


def test_every_padding_rest_is_marked_as_inferred_not_just_counted():
    """The mark has to be on the BEATS, because the beats are what the
    conformance count and both emitters read. A padded voice whose filling
    rests look like engraved ones is exactly how a score missing sixty-five
    notes came to report every bar as adding up at high confidence."""
    events = [_note_event(100.0, 4, y=60.0, stem="up")]
    events += [_note_event(x, 4, y=130.0, stem="down") for x in (100.0, 140.0, 180.0)]
    cols = [_col(100.0, (1, "7"), (5, "3")), _col(140.0, (5, "5")), _col(180.0, (5, "7"))]
    voices, _, _ = _measure(cols, events, budget=3.0)

    marked = [(c, d) for v in voices for c, d, n in v if musicxml.is_inferred_rest(n)]
    assert marked == [(2, 0)], voices
    # the notes that WERE read carry no such mark, and neither would a rest
    # decoded from a glyph on the page
    assert not any(musicxml.is_inferred_rest(n) for v in voices for _c, _d, n in v if n)

    # ...and the bar it fills still reports SHORT by what was missing, which is
    # the whole point: counting the padding made min(voices) == budget by
    # construction, so no padded bar could ever be reported short.
    counts = tabextract._bar_conformance([(voices, (3, 4))])
    assert counts.short == 1, counts
    assert counts.defective == 1, counts
    assert counts.padded == 1 and counts.padded_bars == (1,), counts
    assert counts.inferred_quarters == 2.0, counts


def test_a_voice_that_enters_late_is_padded_at_the_front():
    events = [_note_event(180.0, 4, y=60.0, stem="up")]
    events += [_note_event(x, 4, y=130.0, stem="down") for x in (100.0, 140.0, 180.0)]
    cols = [_col(100.0, (5, "3")), _col(140.0, (5, "5")),
            _col(180.0, (1, "7"), (5, "7"))]
    voices, _, _ = _measure(cols, events, budget=3.0)
    assert len(voices) == 2
    assert _quarters(voices[0]) == 3.0
    assert voices[0][0][2] == [], "silence first"
    assert voices[0][-1][2] == [(1, "7")], "then the note it enters on"
    # The leading silence is why the padding is KEPT rather than dropped: the
    # note it precedes enters on beat three, and a voice written without it
    # enters on beat one instead - two beats early against the other voice.
    assert musicxml.is_inferred_rest(voices[0][0][2]), "and it is marked as inferred"


def test_rest_beats_for_fills_a_meter_exactly():
    assert tabextract._rest_beats_for(3.0) == [(2, 0, []), (4, 0, [])]
    assert tabextract._rest_beats_for(4.0) == [(1, 0, [])]
    assert tabextract._rest_beats_for(1.5) == [(4, 0, []), (8, 0, [])]


def test_rest_beats_are_only_marked_inferred_when_asked():
    """An inferred rest compares equal to a plain one on purpose - every reader
    of the beats model that only asks "is this a rest" must keep working - so
    equality cannot be what distinguishes them, and a caller that forgot to ask
    for the mark would look identical in a diff. Assert the mark directly."""
    plain = tabextract._rest_beats_for(3.0)
    assert not any(musicxml.is_inferred_rest(n) for _c, _d, n in plain)

    marked = tabextract._rest_beats_for(3.0, inferred=True)
    assert marked == plain, "same rests, spelled the same way"
    assert all(musicxml.is_inferred_rest(n) for _c, _d, n in marked)


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


def test_an_onset_short_of_digits_does_not_eat_the_next_onsets_column():
    """An onset can legitimately need two columns (engravers offset a bass tab
    number a few points right of a treble one in the same chord), but the
    search for the second one has to stay beside the first. Searching the full
    notehead-to-digit window instead let an onset with more noteheads than its
    own column had digits consume the NEXT onset's column - sounding those
    frets a beat early and dropping the notes that column belonged to."""
    events = [
        _note_event(100.0, 4, y=60.0, stem="up"),
        _note_event(100.0, 4, y=120.0, stem="down"),
        _note_event(110.0, 4, y=122.0, stem="down"),
    ]
    cols = [_col(100.0, (1, "7")), _col(110.0, (5, "3"))]
    voices, _, unmatched_notes = _measure(cols, events, budget=3.0)
    lower = [b for b in voices[1] if b[2]]
    assert lower == [(4, 0, [(5, "3")])], voices
    # and it is the SECOND beat of that voice, not the first
    assert voices[1].index(lower[0]) == 1, voices[1]
    assert unmatched_notes == 1, "the notehead with no digit is reported, not hidden"


def test_one_stem_shared_across_two_onsets_is_not_one_chord():
    """Sharing a stem is necessary but not sufficient to be one beat - the
    noteheads also have to sound together. A notehead whose own stem was
    missed can be threaded onto a neighbour's, and keying on the stem alone
    welded two consecutive onsets into a chord positioned between them,
    losing an onset and its duration."""
    events = [
        _note_event(100.0, 4, y=60.0, stem="up", stem_id="A"),
        _note_event(106.0, 4, y=80.0, stem="up", stem_id="A"),
        _note_event(140.0, 4, y=62.0, stem="up"),
    ]
    cols = [_col(100.0, (2, "5")), _col(106.0, (5, "3")), _col(140.0, (5, "7"))]
    voices, _, _ = _measure(cols, events, budget=3.0)
    assert len(voices) == 1, voices
    assert len(voices[0]) == 3, "three onsets, three beats"
    assert tabextract._bar_quarters(voices) == 3.0


def test_a_rest_matching_no_onset_is_dropped_not_added_to_a_sounding_voice():
    """A rest glyph further from every decoded onset than the merge tolerance
    says nothing about which voice it belongs to. Adding it anyway put a beat
    into a voice that was already sounding there, taking that voice over its
    meter and pushing everything after it late."""
    events = [_note_event(x, 4, y=60.0, stem="up") for x in (100.0, 140.0, 180.0, 220.0)]
    events += [_note_event(x, 4, y=120.0, stem="down") for x in (100.0, 180.0)]
    events += [_note_event(145.4, 4, y=62.0, rest=True)]  # 5.4pt from any onset
    cols = [_col(100.0, (1, "7"), (5, "3")), _col(140.0, (1, "8")),
            _col(180.0, (1, "9"), (5, "5")), _col(220.0, (1, "10"))]
    voices, _, _ = _measure(cols, events, budget=4.0)
    assert len(voices) == 2
    assert _quarters(voices[0]) == 4.0, voices[0]
    assert _quarters(voices[1]) == 4.0, voices[1]
    assert [b[2] for b in voices[0]] == [[(1, "7")], [(1, "8")], [(1, "9")], [(1, "10")]]


def test_a_voice_whose_notes_all_lost_their_digits_is_not_emitted():
    """Padding a voice that decoded no notes at all filled a whole bar with
    inferred silence: a phantom voice that counted as polyphony, overstated
    how much rest was deduced from the meter, and wrote a meaningless
    `\\voice :2 r :4 r` into the stored transcription."""
    events = [
        _note_event(100.0, 4, y=60.0, stem="up"),
        _note_event(100.0, 4, y=120.0, stem="down"),
    ]
    cols = [_col(100.0, (1, "7"))]  # one digit for two noteheads
    voices, _, unmatched_notes = _measure(cols, events, budget=3.0)
    assert len(voices) == 1, voices
    assert voices[0] == [(4, 0, [(1, "7")])]
    assert _inferred_quarters(voices) == 0.0, "no silence is inferred for a voice that never played"
    assert unmatched_notes == 1


def test_a_bar_of_nothing_but_rests_still_keeps_them():
    """The phantom-voice filter must not throw away a genuinely silent bar."""
    events = [_note_event(100.0, 4, rest=True)]
    voices, _, _ = _measure([], events, budget=3.0)
    assert voices == [[(4, 0, [])]], voices


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


def test_reference_score_reports_the_bars_it_invented_silence_in(zanarkand_pdf):
    """The emitted alphaTex bars mostly add up (above) BECAUSE some of them
    were filled out from the meter. Both facts have to be reported, and the
    second one by bar number: the padding is why a score with notes missing
    could report every bar conformant at high confidence.
    """
    result = tabextract.extract(zanarkand_pdf)
    assert result.bars_padded > 0
    assert len(result.padded_bars) == result.bars_padded
    assert result.padded_bars == sorted(result.padded_bars)
    assert all(1 <= n <= result.bars for n in result.padded_bars)
    assert result.inferred_rest_quarters > 0

    padded = [w for w in result.warnings if "deduced from the time signature" in w]
    assert len(padded) == 1, result.warnings
    assert f"{result.bars_padded} of {result.bars_measured} bar(s)" in padded[0]
    assert str(result.padded_bars[0]) in padded[0]
    # every padded bar is a bar whose reading came up short of its meter, so
    # the short count cannot be smaller than nothing while padding happened
    assert result.bars_short > 0


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


def test_a_rest_read_as_the_likelier_of_two_values_is_not_reported_as_read(monkeypatch):
    """Maestro and Opus draw the half and the whole rest with ONE glyph, so a
    rest whose position says neither (no detected staff lines to measure it
    against, or an outline that could not be read) is read as the commoner of
    the two. That is a twofold difference in one rest's duration, which is the
    whole bar's arithmetic - so it caps what may be claimed about this staff
    rather than sitting in the stats unread."""
    src = _resolve_with_stats(monkeypatch, _stats(undecided_rests=1))
    assert src.provenance == tabextract.PROV_GLYPHS_DEGRADED
    assert src.uses_glyphs, "every other duration on the staff was still read"
    assert "1 rest(s)" in src.detail
    assert "read as a half rest" in src.detail


def test_a_notehead_whose_duration_was_floored_is_not_reported_as_read(monkeypatch):
    """A filled notehead with no stem has no flag or beam to count, so it is
    emitted at the longest value its head alone allows. That is a guess, and
    one that always errs long, so it caps what may be claimed about the staff
    the same way an unreadable rest does - rather than sitting in the stats
    while the staff reports its rhythm as read from the glyphs."""
    src = _resolve_with_stats(monkeypatch, _stats(no_stem_noteheads=3))
    assert src.provenance == tabextract.PROV_GLYPHS_DEGRADED
    assert src.uses_glyphs, "every notehead that did find its stem was still read"
    assert "3 notehead(s)" in src.detail
    assert "no stem" in src.detail


def test_a_staff_whose_heads_all_found_a_stem_is_still_fully_read(monkeypatch):
    """The gate must not fire on zero, or every staff in the library degrades
    and the distinction it exists to draw is gone."""
    src = _resolve_with_stats(monkeypatch, _stats(no_stem_noteheads=0))
    assert src.provenance == tabextract.PROV_GLYPHS


def test_the_no_stem_gate_sits_at_the_end_of_the_resolution_ladder(monkeypatch):
    """`no_stem_noteheads` is checked LAST, after every unknown-vocabulary
    branch, and that ordering is deliberate rather than incidental: a staff
    whose glyphs are mostly unrecognised has nothing this decoder can trust at
    all, so it must fall back to the spacing heuristic outright rather than
    being reported as `glyphs-degraded` - which still claims the glyphs
    themselves were read - just because it also happens to carry a few
    stemless noteheads. Pinned here so a reordering that moved the no-stem
    check ahead of the ratio fallback would be caught by a single assertion
    rather than only by a drop in some library-wide count nobody is watching."""
    src = _resolve_with_stats(monkeypatch, _stats(
        unknown_glyphs=36, unknown_ratio=0.9, band_glyphs=40, no_stem_noteheads=3))
    assert src.provenance == tabextract.PROV_SPACING
    assert not src.uses_glyphs


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
    w, c = tabextract._rhythm_report(
        all_glyph, {}, tabextract._BarConformance(37, 0, 37, 50))
    assert any("37 of 50 bar(s) hold more than their time signature" in x for x in w)
    assert any("two voices" in x for x in w)
    assert not c.startswith("high")
    assert "37 of 50" in c

    # A stray overfull bar is worth reporting but shouldn't condemn the score.
    w2, c2 = tabextract._rhythm_report(
        all_glyph, {}, tabextract._BarConformance(1, 0, 1, 50))
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


# ---------------------------------------------------------------------------
# MusicXML as the canonical output
# ---------------------------------------------------------------------------


def test_bar_conformance_counts_both_directions():
    """The MusicXML profile's Rule 8 measured on the beats model. A bar can be
    wrong in either direction and both have to be visible - only the overfull
    count existed before, so a bar with a note missing from it looked clean.

    The fields are (overfull, short, defective, counted, padded, padded_bars,
    inferred_quarters, defective_bars)."""
    exact = [[(4, 0, [(1, 0)])] * 3]
    over = [[(4, 0, [(1, 0)])] * 4]
    short = [[(4, 0, [(1, 0)])] * 2]
    assert tabextract._bar_conformance([(exact, (3, 4))]) == (0, 0, 0, 1, 0, (), 0.0, ())
    assert tabextract._bar_conformance([(over, (3, 4))]) == (1, 0, 1, 1, 0, (), 0.0, (1,))
    assert tabextract._bar_conformance([(short, (3, 4))]) == (0, 1, 1, 1, 0, (), 0.0, (1,))
    # A bar with one voice over its meter and another under it is wrong ONCE.
    # overfull + short would count it twice and can exceed the bar count, which
    # is why `defective` is a field of its own.
    both = [[(4, 0, [(1, 0)])] * 4, [(4, 0, [(2, 0)])] * 2]
    assert tabextract._bar_conformance([(both, (3, 4))]) == (1, 1, 1, 1, 0, (), 0.0, (1,))
    # A voice padded out to its meter with inferred silence is still SHORT by
    # what was missing, and the padding is reported separately: bar 2 here.
    padded = [[(4, 0, [(1, 0)])] * 3,
              [(4, 0, [(2, 0)]), (2, 0, musicxml.inferred_rest())]]
    assert tabextract._bar_conformance([(exact, (3, 4)), (padded, (3, 4))]) == (
        0, 1, 1, 2, 1, (2,), 2.0, (2,))
    # A bar with no meter is not measured against one, but its padding is still
    # counted: it carries a <forward> into the file either way, and a padded
    # count the file disagrees with is what this whole mechanism prevents.
    assert tabextract._bar_conformance([(padded, None)]) == (
        0, 0, 0, 0, 1, (1,), 2.0, ())
    # _overfull_bars keeps its old shape for the callers that want just that
    assert tabextract._overfull_bars([(over, (3, 4))]) == (1, 1)


def test_short_bars_are_reported_not_padded():
    short = [([[(4, 0, [(1, 0)])] * 2], (3, 4))]
    warnings, _confidence = tabextract._rhythm_report(
        collections.Counter({tabextract.PROV_GLYPHS: 1}), {},
        tabextract._BarConformance(overfull=0, short=1, defective=1, counted=1))
    assert any("hold less than their time signature" in w for w in warnings)
    # and the emitted score really does carry the short bar as-is
    from fermata import musicxml
    assert musicxml.voice_durations(short[0][0]) == [2 * musicxml.DIVISIONS]


def test_the_padded_bars_are_named_not_just_counted():
    """A total says a score is partly invented; the bar numbers say WHERE,
    which is the only form of the fact a reader can check against the PDF."""
    counts = collections.Counter({tabextract.PROV_GLYPHS: 1})
    warnings, _c = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 3, 3, 20, 3, (2, 7, 13), 6.5))
    padded = [w for w in warnings if "deduced from the time signature" in w]
    assert len(padded) == 1, warnings
    assert "3 of 20 bar(s)" in padded[0]
    assert "The bars are: 2, 7, 13." in padded[0]
    assert "6.5 quarter note(s) of it in total" in padded[0]

    # Nothing is said at all when nothing was padded - a warning that fires on
    # a clean score teaches a reader to ignore it.
    quiet, _c = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 0, 0, 20))
    assert not any("deduced from the time signature" in w for w in quiet)


def test_floored_durations_are_counted_and_disclosed_on_a_real_score(hymn_of_the_fayth_pdf):
    """The end-to-end half of #115, and the reason it needs a real score.

    Every notation staff in this file carries filled noteheads whose stems the
    vector pass never sees, so their durations are floored at a quarter. That
    state does not arise in anything engraved in this repository - MuseScore
    draws every stem as a clean vector line and all twelve committed fixtures
    report zero - so a test built only on those would have exercised a branch
    no real input reaches, which is the mistake #108 shipped and had to have
    removed.

    The numbers are exact on purpose. A counter that fires on the wrong branch,
    or one wired to the notehead count rather than to the stemless ones, still
    produces a non-zero figure and a plausible-looking sentence."""
    result = tabextract.extract(str(hymn_of_the_fayth_pdf))
    assert result.notes_no_stem == 73
    assert result.staves_no_stem == 4
    # Every staff on the file is degraded by it, so nothing here can claim to
    # have been read from the glyphs.
    assert result.rhythm_provenance == {tabextract.PROV_GLYPHS_DEGRADED: 4}
    assert not result.confidence["rhythm"].startswith("high")
    # The headline itself must not contradict the disclosure: every staff on
    # this file is floored and the defective-bar ratio crosses the gate, so
    # the confidence string used to be REPLACED with "durations were read
    # from the score's own engraving" - the exact claim this file's own
    # warnings withdraw. Composing onto the existing disclosure instead means
    # the stem caveat has to survive into the final headline.
    assert "stem" in result.confidence["rhythm"], result.confidence["rhythm"]
    # ...and it is SAID, not just counted. The interface loops over the warning
    # strings; a field on its own reaches nobody.
    said = [w for w in result.warnings if "no stem this decoder could find" in w
            and "notehead(s) across" in w]
    assert len(said) == 1, result.warnings
    assert "73 notehead(s) across 4 staff system(s)" in said[0]
    # The per-staff reasons are surfaced too, and they name the mechanism
    # rather than the symptom.
    assert any("no stem this decoder could find" in w for w in result.warnings)


def test_spacing_derived_rhythm_names_the_bars_it_produced():
    """A count of staff systems says how much of a score's rhythm came out of
    the gaps between noteheads instead of the noteheads. It does not say WHICH
    music that was, so "treat those sections as low confidence" was an
    instruction a reader had no way to follow - the padded-bars message names
    its bars for exactly this reason, and this is the same fact about the same
    score."""
    counts = collections.Counter({tabextract.PROV_GLYPHS: 3, tabextract.PROV_SPACING: 2})
    warnings, confidence = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 0, 0, 12),
        prov_bars={tabextract.PROV_SPACING: [5, 6, 7, 11]})
    said = [w for w in warnings if "rougher estimate from note spacing" in w]
    assert len(said) == 1, warnings
    assert "2 staff system(s) could not be read that way" in said[0]
    assert "The bars they produced are: 5, 6, 7, 11." in said[0]
    # ...and a score with any spacing-derived staff cannot present as read.
    assert not confidence.startswith("high")


def test_a_degraded_staff_names_its_bars_and_does_not_claim_one_cause():
    """A degraded staff was read from the engraving with something on it left
    unread, and the cause is no longer only an uncalibrated glyph - a notehead
    with no stem lands here too. Naming one cause in the summary sentence
    described the wrong problem for most of the staves it covered."""
    counts = collections.Counter({tabextract.PROV_GLYPHS: 1,
                                  tabextract.PROV_GLYPHS_DEGRADED: 2})
    warnings, confidence = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 0, 0, 8),
        prov_bars={tabextract.PROV_GLYPHS_DEGRADED: [3, 4]})
    said = [w for w in warnings if "2 staff system(s) were read from the engraved" in w]
    assert len(said) == 1, warnings
    assert "no stem" in said[0]
    assert "not been calibrated" in said[0]
    assert "The bars they produced are: 3, 4." in said[0]
    assert confidence.startswith("medium")


def test_bar_numbers_are_not_invented_when_none_were_collected():
    """Absent bar numbers must produce no sentence about bar numbers, rather
    than an empty list that reads as "no bars were affected"."""
    counts = collections.Counter({tabextract.PROV_GLYPHS: 3, tabextract.PROV_SPACING: 2})
    warnings, _c = tabextract._rhythm_report(counts, {})
    said = next(w for w in warnings if "rougher estimate from note spacing" in w)
    assert "The bars they produced" not in said


def test_floored_note_durations_are_said_out_loud_with_their_count():
    """The interface consumes warnings as strings and loops over them, so prose
    reaches a reader on its own; a count field does not. This count therefore
    has to exist as a SENTENCE, and it has to be the number of notes - the
    staff counts beside it cannot be turned back into it, because one stemless
    notehead on a staff and forty of them read identically there."""
    counts = collections.Counter({tabextract.PROV_GLYPHS_DEGRADED: 4})
    warnings, _c = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 0, 0, 20),
        no_stem_notes=37, no_stem_staves=4)
    said = [w for w in warnings if "notehead(s) across" in w and "no stem this decoder" in w]
    assert len(said) == 1, warnings
    assert "37 notehead(s) across 4 staff system(s)" in said[0]
    assert "plain quarter" in said[0]

    # Nothing is said when every notehead found its stem.
    quiet, _c = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 0, 0, 20))
    assert not any("notehead(s) across" in w and "no stem this decoder" in w for w in quiet)


def test_a_quarter_note_count_is_exact_not_rounded():
    """The sentence's whole purpose is to say how much of a score was invented,
    so the number in it has to be the number. A %.4g format printed 43.875 as
    "43.88" and 104.25 as "104.2" - wrong, in ten of the library's scores."""
    counts = collections.Counter({tabextract.PROV_GLYPHS: 1})
    for quarters, text in ((43.875, "43.875"), (104.25, "104.25"), (126.0, "126"),
                           (0.125, "0.125"), (2.5, "2.5")):
        warnings, _c = tabextract._rhythm_report(
            counts, {}, tabextract._BarConformance(0, 1, 1, 20, 1, (2,), quarters))
        padded = next(w for w in warnings if "deduced from the time signature" in w)
        assert f"{text} quarter note(s)" in padded, padded


def test_a_hundred_padded_bars_do_not_become_a_wall_of_numbers():
    """The list is capped and says how many it left out; the COUNT beside it is
    always the whole truth, and ExtractionResult carries every number as data.

    The cap VALUE is asserted, not just that some cap exists: at twelve it bound
    131 of the library's 271 affected scores, so the prose lost the fact for half
    of them, and a test that only checks "some numbers are missing" would have
    called that healthy.
    """
    counts = collections.Counter({tabextract.PROV_GLYPHS: 1})
    cap = tabextract._BARS_LISTED
    assert cap == 60, "the cap is a measured choice - see _BARS_LISTED"
    bars = tuple(range(1, cap + 41))
    warnings, _c = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(
            0, len(bars), len(bars), len(bars), len(bars), bars, 200.0))
    padded = next(w for w in warnings if "deduced from the time signature" in w)
    assert f"{len(bars)} of {len(bars)} bar(s)" in padded
    listed = padded.split("The bars are: ")[1].split(" and ")[0]
    assert [int(n) for n in listed.split(", ")] == list(bars[:cap])
    assert f"and {len(bars) - cap} more" in padded

    # A list that fits is not truncated and says nothing about "more"
    short_list = tuple(range(1, cap + 1))
    warnings, _c = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(
            0, cap, cap, cap, cap, short_list, 1.0))
    padded = next(w for w in warnings if "deduced from the time signature" in w)
    assert "more." not in padded
    assert str(cap) in padded


def test_short_bars_downgrade_confidence_the_same_way_overfull_ones_do():
    """Rule 8 treats both directions as defective, so the confidence string has
    to as well. A score whose bars are mostly short because notes were dropped
    used to report "high - decoded directly from the ... engraving" over a
    warning list saying most of its bars do not add up."""
    counts = collections.Counter({tabextract.PROV_GLYPHS: 1})
    _w, high = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 1, 1, 10))
    assert high.startswith("high"), high

    _w, low = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 6, 6, 10))
    assert low.startswith("low overall"), low
    assert "6 of 10" in low

    # and overfull still downgrades exactly as it did
    _w, low_over = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(6, 0, 6, 10))
    assert low_over.startswith("low overall"), low_over

    # a bar wrong in both directions is one defective bar, not two - a 10-bar
    # score with 2 such bars is 20% defective and must NOT be downgraded
    _w, still_high = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(2, 2, 2, 10))
    assert still_high.startswith("high"), still_high


def test_a_confidence_below_the_threshold_still_says_what_is_wrong():
    """The threshold decides the LABEL, not whether anything is said. Sixteen of
    the nineteen library scores that still rated "high" sat just under the
    quarter - one with invented silence in 7 of its 33 bars - and read
    "decoded directly from the ... engraving" with nothing qualifying it, which
    makes a score with known defective bars indistinguishable from a clean
    one at the single figure the application uses to summarise a
    transcription."""
    counts = collections.Counter({tabextract.PROV_GLYPHS: 1})
    _w, high = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 2, 2, 10))
    assert high.startswith("high"), "still high: two of ten is under the quarter"
    assert "2 of 10 bar(s) do not add up" in high, high

    # A genuinely clean score says nothing extra - the qualifier has to mean
    # something, and one that appears on every score would not.
    _w, clean = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 0, 0, 10))
    assert clean.startswith("high")
    assert "bar(s)" not in clean, clean


def test_bars_nothing_was_read_from_are_counted_and_named():
    """A bar holding a whole bar of rests nothing was read from adds up to its
    meter, so it passes Rule 8 and is NOT counted as defective - folding it in
    would make these figures disagree with the emitted file. It is still not a
    reading: a 40-bar score read as nothing at all was reporting 40 of 40 bars
    conformant at high confidence with no warning mentioning emptiness."""
    counts = collections.Counter({tabextract.PROV_GLYPHS: 1})
    warnings, confidence = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 0, 0, 40), tuple(range(1, 41)))
    unread = [w for w in warnings if "hold nothing that was read" in w]
    assert len(unread) == 1, warnings
    assert "40 of 40 bar(s)" in unread[0]
    assert "The bars are: 1, 2," in unread[0]
    assert confidence.startswith("low overall"), confidence
    assert "hold nothing that was read from the score (40)" in confidence

    # Below the threshold they are still named, and still qualify the string.
    warnings, confidence = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 0, 0, 40), (7, 9))
    assert any("2 of 40 bar(s) hold nothing" in w for w in warnings), warnings
    assert confidence.startswith("high")
    assert "(2)" in confidence, confidence

    # And a bar that is BOTH defective and unread counts once, or the ratio
    # could exceed 1 and a 4-bar score could report 5 unreliable bars.
    _w, both = tabextract._rhythm_report(
        counts, {}, tabextract._BarConformance(0, 4, 4, 4, 0, (), 0.0, (1, 2, 3, 4)),
        (1, 2, 3, 4))
    assert "4 of 4 bar(s) either" in both, both


def _onsets_by_voice(parsed):
    """{voice index: [(start, duration, is_rest), ...]} from an --onsets load."""
    out = collections.defaultdict(list)
    for _bar, voice, start, duration, is_rest in parsed["onsets"]:
        out[voice].append((start, duration, is_rest))
    for beats in out.values():
        beats.sort()
    return dict(out)


@pytest.mark.parametrize("where", ["leading", "trailing", "middle"])
def test_the_renderer_puts_a_note_after_inferred_silence_where_a_rest_would(where):
    """The one assumption the whole of Rule 14 rests on, checked against the
    real importer rather than against this emitter agreeing with itself.

    Inferred silence is written as `<forward>` instead of a rest so that it does
    not claim the source printed one. That is only safe if a consumer advances
    its position across it. If the renderer ever stopped doing so, every voice
    that enters late would collapse onto the downbeat and sound its notes early
    - a file that still loads, still validates and plays wrong. So build the
    same bar twice, once with the silence marked inferred and once as an
    ordinary rest, and require the onsets to be IDENTICAL. Comparing against
    the rest version rather than against a tick figure keeps this about the
    property and not about alphaTab's units.
    """
    note, silence = (4, 0, [(1, 5)]), (2, 0, None)
    shapes = {
        "leading": [silence, note],
        "trailing": [note, silence],
        "middle": [(4, 0, [(1, 5)]), (4, 0, None), (4, 0, [(1, 7)])],
    }

    def build(marked):
        def fill(beat):
            code, dots, notes = beat
            if notes is not None:
                return beat
            return (code, dots, musicxml.inferred_rest() if marked else [])
        upper = [fill(b) for b in shapes[where]]
        lower = [(4, 0, [(6, 0)]), (4, 0, [(6, 2)]), (4, 0, [(6, 3)])]
        return musicxml.build("T", None, tabextract.DEFAULT_TUNING, (3, 4),
                              [([upper, lower], (3, 4))])

    inferred = _load_musicxml_with_alphatab(build(True), onsets=True)
    engraved = _load_musicxml_with_alphatab(build(False), onsets=True)

    assert "<forward>" in build(True), "the inferred version really does write one"
    assert "<forward>" not in build(False)

    def sounding(parsed):
        return {v: [b for b in beats if not b[2]]
                for v, beats in _onsets_by_voice(parsed).items()}

    assert sounding(inferred) == sounding(engraved), (
        f"a {where} <forward> did not hold the position a rest holds - every note "
        "after it moved")

    if where == "trailing":
        # A trailing forward is the one case a consumer may legitimately drop:
        # this renderer ends the voice there rather than showing silence, and
        # nothing sounds after it, so no note moves. Recorded because it is the
        # only place the two spellings differ at all.
        assert _onsets_by_voice(inferred)[0] != _onsets_by_voice(engraved)[0]
        return

    # Otherwise the position really was held, rather than two voices merely both
    # starting on the downbeat: the padded voice covers the same span as the one
    # that plays throughout, and where it entered late its NOTE is not first.
    voices = _onsets_by_voice(inferred)
    upper, lower = voices[0], voices[1]

    def end(beats):
        return max(start + duration for start, duration, _rest in beats)

    assert end(upper) == end(lower), "the padded voice does not cover the bar"
    if where == "leading":
        starts = [start for start, _d, is_rest in upper if not is_rest]
        assert starts and min(starts) > upper[0][0], (
            "the late-entering voice sounds on the downbeat - the forward was ignored")


def test_reported_beat_count_matches_what_the_musicxml_holds():
    """`beats` is reported beside the MusicXML, so it has to be what the file
    contains. Inferred silence is written as `<forward>` and is not a beat of
    the score, so counting the beats model instead reported more beats than the
    canonical output holds - 6386 more across the library, exactly its number
    of `<forward>` elements, on 271 of 293 scores."""
    import xml.etree.ElementTree as ET

    from fermata import musicxml

    measures = [
        # one voice fully read, one padded out with inferred silence
        ([[(4, 0, [(1, 0)]), (4, 0, [(1, 2)]), (4, 0, [(1, 3)])],
          [(4, 0, [(5, 0)]), (2, 0, musicxml.inferred_rest())]], (3, 4)),
        # a chord is ONE beat however many notes it has (Rule 7), and a beat
        # whose notes have no writable pitch keeps its place as a rest
        ([[(4, 0, [(6, 0), (5, 2), (4, 2)]), (4, 0, [(1, 78)]), (4, 0, [(1, 1)])]], (3, 4)),
    ]
    assert musicxml.written_beats(measures) == 7
    root = ET.fromstring(
        musicxml.build("T", None, tabextract.DEFAULT_TUNING, (3, 4), measures))
    holds = len([n for n in root.findall("./part/measure/note") if n.find("chord") is None])
    assert holds == musicxml.written_beats(measures)
    assert len(root.findall("./part/measure/forward")) == 1


def test_reported_beat_count_matches_the_reference_scores_musicxml(zanarkand_pdf):
    """The same invariant on a real score, where the padding actually happens."""
    import xml.etree.ElementTree as ET

    result = tabextract.extract(zanarkand_pdf)
    root = ET.fromstring(result.musicxml)
    holds = len([n for n in root.findall("./part/measure/note") if n.find("chord") is None])
    assert holds == result.beats
    assert len(root.findall("./part/measure/forward")) > 0


def test_reported_note_count_matches_what_the_musicxml_holds():
    """`notes` is reported beside the MusicXML, so it has to be what the file
    contains - not what came off the page before the emitter dropped the notes
    it had no pitch for."""
    import xml.etree.ElementTree as ET

    from fermata import musicxml

    beats = [[(4, 0, [(1, 78)]), (4, 0, [(1, 0)]), (4, 0, [(1, 2)]), (4, 0, [(1, 3)])]]
    measures = [(beats, (4, 4))]
    extracted = sum(len(n) for v in beats for _c, _d, n in v)
    unwritable = musicxml.unrepresentable_notes(measures, tabextract.DEFAULT_TUNING)
    root = ET.fromstring(
        musicxml.build("T", None, tabextract.DEFAULT_TUNING, (4, 4), measures))
    written = len([n for n in root.findall("./part/measure/note") if n.find("rest") is None])
    assert extracted == 4
    assert unwritable == 1
    assert written == extracted - unwritable


def test_extract_emits_musicxml_alongside_alphatex(zanarkand_pdf):
    """Both emitters read the same measures, so their note counts must agree -
    a MusicXML document holding a different number of notes than the alphaTex
    for the same score means one of them is dropping beats."""
    import xml.etree.ElementTree as ET

    result = tabextract.extract(zanarkand_pdf)
    assert result.musicxml is not None
    root = ET.fromstring(result.musicxml)
    assert root.tag == "score-partwise"
    assert len(root.findall("./part/measure")) == result.bars
    pitched = [n for n in root.findall("./part/measure/note") if n.find("rest") is None]
    assert len(pitched) == result.notes


def test_extract_decodes_the_key_signature(zanarkand_pdf):
    result = tabextract.extract(zanarkand_pdf)
    assert result.key_signature_source == "glyph-decoded"
    assert -7 <= result.key_fifths <= 7
    assert "high" in result.confidence["key_signature"]


def test_an_impossible_fret_becomes_a_rest_and_the_bar_still_adds_up(tmp_path):
    """A fret read off a two-digit text span can be nonsense (78, 79 - two
    adjacent notes rendered as one span), and 78 semitones above a string is
    past the top of MusicXML's octave range. Writing it anyway made the whole
    document fail schema validation, which is far worse than one missing note,
    so the note is dropped - but its beat keeps its place as a rest, or the
    measure would stop adding up because of it."""
    import xml.etree.ElementTree as ET

    from fermata import musicxml

    beats = [[(4, 0, [(1, 78)]), (4, 0, [(1, 0)]), (4, 0, [(1, 2)]), (4, 0, [(1, 3)])]]
    measures = [(beats, (4, 4))]
    assert musicxml.unrepresentable_notes(measures, tabextract.DEFAULT_TUNING) == 1
    xml = musicxml.build("T", None, tabextract.DEFAULT_TUNING, (4, 4), measures)
    root = ET.fromstring(xml)
    notes = root.findall("./part/measure/note")
    assert len(notes) == 4
    assert notes[0].find("rest") is not None
    assert notes[0].findtext("duration") == str(musicxml.DIVISIONS)
    # no octave outside what MusicXML can express reached the document
    for octave in root.findall(".//pitch/octave"):
        assert musicxml.MIN_OCTAVE <= int(octave.text) <= musicxml.MAX_OCTAVE
    # and the bar still sums to its meter
    total = sum(int(n.findtext("duration")) for n in notes if n.find("chord") is None)
    assert total == musicxml.measure_divisions((4, 4))


def test_a_chord_with_one_impossible_fret_keeps_its_other_notes():
    import xml.etree.ElementTree as ET

    from fermata import musicxml

    beats = [[(4, 0, [(1, 78), (3, 2), (5, 0)])]]
    xml = musicxml.build("T", None, tabextract.DEFAULT_TUNING, (4, 4), [(beats, (4, 4))])
    root = ET.fromstring(xml)
    notes = root.findall("./part/measure/note")
    assert len(notes) == 2
    # the surviving notes re-form a chord: the first leads, the second follows
    assert notes[0].find("chord") is None
    assert notes[1].find("chord") is not None
    assert [n.findtext("notations/technical/fret") for n in notes] == ["2", "0"]


def test_reported_conformance_matches_the_emitted_measures(zanarkand_pdf):
    """The Rule 8 counts are reported as data as well as prose, so they have to
    agree with what a consumer reads out of the emitted document - which is the
    whole argument for emitting a standard format."""
    import xml.etree.ElementTree as ET

    result = tabextract.extract(zanarkand_pdf)
    root = ET.fromstring(result.musicxml)
    assert result.bars_measured == result.bars

    defective = 0
    divisions = int(root.findtext("./part/measure/attributes/divisions"))
    beats = beat_type = None
    for measure in root.findall("./part/measure"):
        time = measure.find("attributes/time")
        if time is not None:
            beats = int(time.findtext("beats"))
            beat_type = int(time.findtext("beat-type"))
        expected = round(divisions * 4 * beats / beat_type)
        sums = {}
        for note in measure.findall("note"):
            if note.find("chord") is not None:
                continue
            voice = note.findtext("voice")
            sums[voice] = sums.get(voice, 0) + int(note.findtext("duration"))
        if any(total != expected for total in sums.values()):
            defective += 1
    assert defective == result.bars_defective
    # a bar can be wrong in both directions at once, so these bound it rather
    # than summing to it
    assert result.bars_defective <= result.bars_overfull + result.bars_short
    assert max(result.bars_overfull, result.bars_short) <= result.bars_defective


def test_no_voice_is_written_as_inferred_silence_alone(zanarkand_pdf):
    """What makes the check above valid for a consumer as well as for us.

    Inferred silence is a `<forward>`, which is not counted by Rule 8 - so a
    voice consisting of NOTHING but inferred silence would contribute no notes
    and no rests to its measure at all, and a consumer enumerating voices from
    `<note>` elements would not see that voice, or its shortfall, at all. Every
    voice that reaches the emitter has at least one decoded note in it (see
    _build_measure_beats_glyph's phantom-voice filter), which is what keeps the
    two counts equal. Assert it on the reference score rather than trusting it.
    """
    import xml.etree.ElementTree as ET

    result = tabextract.extract(zanarkand_pdf)
    root = ET.fromstring(result.musicxml)
    forwards = 0
    for measure in root.findall("./part/measure"):
        sounding = {n.findtext("voice") for n in measure.findall("note")}
        for forward in measure.findall("forward"):
            forwards += 1
            assert forward.findtext("voice") in sounding, (
                f"measure {measure.get('number')} writes a voice as silence alone")
    assert forwards > 0, "this score does have inferred silence in it to check"
    assert result.bars_padded > 0


def test_a_bar_wrong_in_both_directions_is_counted_once():
    """The case the whole reporting contract rests on, which no real score in
    the library exhibits.

    Two voices sound concurrently: one runs over the meter, the other falls
    short of it. That is ONE bar that plays wrong, and `defective` says so -
    while `overfull` and `short` each count it, so their sum exceeds the
    number of bars there are. Anything comparing that sum against the total
    reports more defective bars than the music has, which is why `defective`
    is the only figure a reader may put over `measured`.

    Constructed rather than extracted on purpose: no score in the library is
    wrong in both directions at once, so this is the only place the behaviour
    is pinned. If it were deleted the fault would not be rediscovered.
    """
    from fermata.tabextract import _bar_conformance

    over = [(4, 0, None)] * 5  # five quarter notes
    under = [(4, 0, None)] * 3  # three quarter notes
    counts = _bar_conformance([([over, under], (4, 4))])  # a 4/4 bar wants four

    assert counts.counted == 1
    assert counts.defective == 1, "one bar plays wrong, however many ways it is wrong"
    assert counts.overfull == 1
    assert counts.short == 1
    assert counts.overfull + counts.short > counts.counted, (
        "the sum overstates the music - this is the arithmetic no reader may do"
    )


def test_a_printed_tuning_instruction_is_recognised_without_being_applied():
    """The narrower thing that has to be true before the interface may describe a
    tuning at all: knowing the page carries an instruction we did not read.

    Measured across the library, 41 of the 100 scores carrying a "Drop D" label
    also carry one of these - 9 saying to tune every string down a half step, so
    the recorded array is a semitone out, and 32 naming a capo, so every sounding
    pitch is out. That is 14% of the library, and until this existed all 41 were
    describable as a tuning that had been READ off the page. A text match on one
    tuning name is recognition of a label; it is not a reading of the tuning.

    Detection only. Nothing here changes `tuning`, the emitted MusicXML, or any
    sounding pitch - parsing these is issue #80's job.
    """
    from fermata.tabextract import unread_tuning_instructions as found

    for text in [
        "Tune down a half step",
        "tune all strings down 1/2 step",
        "Tuning: 1/2 step down",
        "TUNE DOWN ONE HALF STEP",
        "Lower a half tone",
    ]:
        assert found(text) == ["tune down a half step"], text

    # The number is for the message, so a reader can check it against the page.
    assert found("Capo 2") == ["capo 2"]
    assert found("capo at fret III") == ["capo III"]
    assert found("CAPO 3rd fret") == ["capo 3"]
    # A bare instruction still counts - what matters is that one is there.
    assert found("Play with capo") == ["capo"]
    # A capo at the end of a line must not adopt a number from the next one.
    assert found("Capo\n7 notes") == ["capo"]

    # Both, in the order they are stated to a reader.
    assert found("Tune down a half step, then Capo 2") == [
        "tune down a half step",
        "capo 2",
    ]

    # THE FALSE POSITIVE THAT MATTERS. "Da capo" is a repeat instruction - go
    # back to the beginning - and has nothing to do with the left hand. Classical
    # sheet music is full of them, and matching one would be its own false
    # statement: claiming the page carries a tuning instruction it does not, which
    # is the same fault as claiming a tuning was read.
    for text in [
        "Da Capo al Fine",
        "da capo",
        "Dal capo",
        "D.C. al Fine",
        "capotasto",
        "a whole step down",
        "Andante",
        "",
    ]:
        assert found(text) == [], text
