import collections
import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import fitz
import pytest

from fermata import glyph_rhythm
from fermata import musicxml
from fermata import tabextract

from conftest import skip_without_node_modules


def emitted_meters(musicxml_text):
    """The meter in force in each measure of emitted MusicXML - read out of
    the canonical output rather than off the ExtractionResult, because a
    consumer gets the meter of a measure from the file (see the identical
    helper in test_engraved_fixtures.py)."""
    import xml.etree.ElementTree as ET
    meters = []
    current = None
    for measure in ET.fromstring(musicxml_text).findall("./part/measure"):
        time = measure.find("./attributes/time")
        if time is not None:
            current = (int(time.findtext("beats")), int(time.findtext("beat-type")))
        meters.append(current)
    return meters


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
        skip_without_node_modules("node not available")
    repo_root = Path(__file__).resolve().parents[2]
    alphatab = repo_root / "web" / "node_modules" / "@coderline" / "alphatab" / "dist" / "alphaTab.mjs"
    if not alphatab.is_file():
        skip_without_node_modules("alphaTab.mjs not found - run `npm ci` in web/ first")
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


def _load_musicxml_with_alphatab(xml: str, onsets: bool = False, repeats: bool = False) -> dict:
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

    `repeats=True` adds `repeats` (per-master-bar isRepeatStart/isRepeatEnd/
    repeatCount/alternateEndings) and `tickLookup` (the playback bar order,
    1-based, repeats and all) - read from alphaTab's own MidiFileGenerator,
    the only thing that proves a repeat/ending file PLAYS right rather than
    merely parses right (issue #134 S4.2 / docs Rule 15).
    """
    if shutil.which("node") is None:
        skip_without_node_modules("node not available")
    repo_root = Path(__file__).resolve().parents[2]
    alphatab = repo_root / "web" / "node_modules" / "@coderline" / "alphatab" / "dist" / "alphaTab.mjs"
    if not alphatab.is_file():
        skip_without_node_modules("alphaTab.mjs not found - run `npm ci` in web/ first")
    script = Path(__file__).resolve().parents[1] / "tools" / "tab_extract" / "verify_musicxml.mjs"

    with tempfile.NamedTemporaryFile("w", suffix=".musicxml", delete=False,
                                     encoding="utf-8") as f:
        f.write(xml)
        xml_path = f.name
    try:
        args = ["node", str(script), str(alphatab)]
        if onsets:
            args.append("--onsets")
        if repeats:
            args.append("--repeats")
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


def test_born_a_stranger_unison_survives_as_two_notes_in_two_voices(born_a_stranger_pdf):
    """A regression guard for issue #116, on the score the research's
    guitarist checked against the printed page: two notes adjacent on the
    same row, the lower drawn stem-left and the higher swapped stem-right -
    standard engraving for a unison shared by two voices, and the guitarist
    called two emitted notes correct there. Binding both copies to the same
    stem (the pre-fix defect) would lose one of them; collapsing the
    coincident glyphs to one copy (the tempting wrong fix) would too. These
    figures are invariant under every variant tried during the research."""
    result = tabextract.extract(born_a_stranger_pdf)
    assert result.extractable
    # One fewer than before _detect_barlines merged a repeat pair's two
    # strokes into one boundary (see BARLINE_STROKE_MERGE_SPACES) - this
    # score carried one of the library's phantom sliver measures.
    assert result.bars == 39
    assert result.bars_defective == 38
    assert result.notes == 403


def test_carulli_moderato_unison_emits_one_note_per_voice_not_two_in_one(carulli_moderato_pdf):
    """The other half of the same guard: Carulli's flagged spots read as
    single notes on the page, but the content stream shows the SAME
    two-opposing-stem signature as a genuine unison (the research measured
    this - 'no unisons possible' reads the ink correctly and the content
    stream incorrectly). The correct fix gives each engraved stem its own
    note rather than stacking both duplicate copies into one voice as a
    doubled chord; these figures are unchanged from before the fix, and no
    beat anywhere in the piece repeats the same fret/string within one
    chord."""
    result = tabextract.extract(carulli_moderato_pdf)
    assert result.extractable
    assert result.bars == 42
    assert result.bars_defective == 0
    assert result.notes == 308
    for chord in re.finditer(r"\(([^)]+)\)", result.alphatex):
        notes_in_chord = chord.group(1).split()
        assert len(notes_in_chord) == len(set(notes_in_chord)), (
            f"a chord doubled the same note instead of splitting across voices: "
            f"{chord.group(1)}")


def test_a_coincident_pair_with_no_second_stem_is_disclosed_not_silently_doubled(ronfaure_pdf):
    """A coincident duplicate pair that cannot be told apart - either because
    only ONE candidate stem was found at all, or because every further
    candidate's x-column already belongs to a different, real note's own
    stem (see the onset guard in decode_note_events) - stays bound to the
    winner rather than being split. That must be COUNTED
    (coincident_unsplit_pairs) rather than silently leaving two same-voice
    notes stacked on one stem with no signal that anything is uncertain
    there. 10 of these 15 are onset-rejected rather than single-candidate:
    without the onset guard this score reads 5 unsplit / 4 staves (see the
    guard's own regression test), which is the smaller, geometry-only
    residue the #116 research first measured."""
    result = tabextract.extract(ronfaure_pdf)
    assert result.extractable
    assert result.coincident_unsplit_pairs == 15
    assert result.staves_coincident_unsplit == 4
    assert any("coincident duplicate notehead pair" in w for w in result.warnings)


def _onset_columns(heads_by_group, digits):
    """One onset, run through _match_onset_columns: `heads_by_group` is a list
    of (x, y) lists, one per stem group, and `digits` the (string, fret) pairs
    the tab column at that onset holds. Returns {group index: digits given},
    how many noteheads were left with none, and how many were given a digit
    read for their coincident twin (issue #137's disclosure)."""
    groups = []
    for positions in heads_by_group:
        members = [glyph_rhythm.NoteEvent(x, y, 1.0, 0, 0, False, "notehead_filled",
                                          notehead_kind="notehead_filled")
                   for x, y in positions]
        groups.append(tabextract._StemGroup(members))
    col = {"x": groups[0].x, "xc": groups[0].x, "notes": list(digits)}
    per_group, missing, shared = tabextract._match_onset_columns(
        groups, [col], [col["xc"]], [False], 6.0, 3.0)
    return {i: per_group[id(g)] for i, g in enumerate(groups)}, missing, shared


def test_a_unison_inside_a_chord_is_given_the_digit_its_twin_got():
    """Issue #137's decision, at the level it is made. An upper voice's
    two-note chord (100, 90) and (100, 100), and a lower voice whose own
    notehead is a coincident copy of the chord's lower member - three
    noteheads, two positions - over a tab column holding exactly two digits,
    one per position. The lower voice must come away with the chord's own
    lower digit rather than with nothing: the page names one string there and
    both stems sound it."""
    per_group, missing, shared = _onset_columns(
        [[(100.0, 90.0), (100.0, 100.0)], [(100.0, 100.0)]],
        [(2, "1"), (3, "00")])
    assert per_group[0] == [(2, "1"), (3, "00")], "the chord keeps both its own"
    assert per_group[1] == [(3, "00")], "and the lower voice sounds the shared string"
    assert missing == 0
    assert shared == 1, "and the inference is counted, not silent"


def test_a_coincident_pair_alone_at_an_onset_is_not_given_a_second_note():
    """The boundary, and the reason issue #137's fix is not simply "a
    coincident copy inherits its twin's digit". Where the WHOLE onset is the
    pair - one printed notehead over one printed digit - the page is
    self-consistent as a single note, and the only thing suggesting two is
    the two-opposing-stem signature that #116 measured to be unreliable on
    its own (Carulli-Moderato reads as single notes on the printed page and
    carries exactly that signature 74 times). Doubling it here would invent a
    sounding note; the copy stays without a digit and is reported as one.
    Library-wide this shape is 500 onsets against the chord shape's 65."""
    per_group, missing, shared = _onset_columns(
        [[(100.0, 100.0)], [(100.0, 100.0)]], [(3, "00")])
    assert per_group[0] == [(3, "00")]
    assert per_group[1] == [], "no evidence of a second sounding note"
    assert missing == 1, "and the notehead with no fret number is still counted"
    assert shared == 0


def test_a_coincident_pair_that_stayed_in_one_voice_is_not_doubled_into_it():
    """The other refusal: where the two copies could not be split across two
    stems they sit in ONE group (glyph.decode_note_events's
    coincident_unsplit_pairs), and handing the second copy the same digit
    would put the note in that voice twice rather than give another voice its
    own. The chord's shape is otherwise exactly the sharing case above."""
    per_group, missing, shared = _onset_columns(
        [[(100.0, 90.0), (100.0, 100.0), (100.0, 100.0)]],
        [(2, "1"), (3, "00")])
    assert per_group[0] == [(2, "1"), (3, "00")], "one note per sounding string"
    assert missing == 1
    assert shared == 0


def test_a_chord_the_tab_never_fully_named_is_not_patched_from_a_twin():
    """A column short of a digit for a position that has no twin at all is
    not this defect and must not be papered over: with three positions and
    only two digits the tab is genuinely missing information, so the third
    notehead goes without and says so, coincident copy elsewhere or not."""
    per_group, missing, shared = _onset_columns(
        [[(100.0, 80.0), (100.0, 90.0), (100.0, 100.0)], [(100.0, 100.0)]],
        [(1, "5"), (2, "1")])
    assert missing == 2
    assert shared == 0
    assert per_group[1] == []


def test_the_cosmic_wheel_chord_unison_stops_costing_it_twelve_bars(cosmic_wheel_pdf):
    """Issue #137's whole subject, on the score it was filed for. Twelve
    onsets across four pages write an upper voice's two-note chord whose
    LOWER member is the lower voice's own eighth: three noteheads at two
    positions, and two tab digits, because the unison is one plucked string.

    Before the shared digit (see tabextract._share_unison_digits) the chord's
    own two positions took both digits, the third notehead got none, and the
    lower voice lost its note on each of those twelve beats - twelve bars
    read an eighth short of 4/4 and were padded with silence nothing on the
    page prints. Measured on this score: 35 defective bars, of which 31
    short, 902 notes. With it: 23 / 19 / 914, and the twelve recovered notes
    are the point rather than the bar count alone - 23 is also what this
    score read BEFORE #116's stem split, but with the shared note in only
    one of its two voices.

    The 8 OVERFULL bars are deliberately still 8 and are not this defect.
    One of them, bar 13, is the whole note issue #137 named in its text
    (`:16 3.1 :1 5.1`, 4.25 quarters in 4/4): a harmonic's hollow notehead
    read as a whole note. It predates #116 entirely - the same string is
    emitted at d07387d - and is tracked as issue #140."""
    result = tabextract.extract(cosmic_wheel_pdf)
    assert result.extractable
    assert result.bars == 78
    assert result.notes == 914
    assert result.bars_overfull == 8
    assert result.bars_short == 19
    assert result.bars_defective == 23
    # The twelve recovered notes are an INFERENCE about which string they are
    # on - the tablature printed no number for those noteheads - so they are
    # disclosed rather than folded into the note count silently, and the
    # count has to be exactly the twelve.
    assert result.unison_digits_shared == 12
    assert any("coincident notehead at the same position" in w for w in result.warnings)


def test_a_unison_on_a_chords_top_member_is_left_exactly_as_it_was(spanish_romance_pdf):
    """The known limitation, pinned so it cannot drift unnoticed. Sharing can
    only reach a coincident copy that sorts LAST in pitch order - i.e. the
    unison is the chord's LOWEST member - because the rank match consumes
    heads from the top and only then is a leftover head's twin among the
    matched ones. Spanish Romance is the library's largest population of the
    other arrangement (34 of the 48 onsets where the leftover head has no
    twin at its own position), and it must read exactly as it did before this
    change: nothing shared, and its conformance figures untouched.

    This is a DIFFERENT defect, not one this refuses on evidence - see
    issue #141 - and the assertion here is that #137 neither fixed nor
    worsened it."""
    result = tabextract.extract(spanish_romance_pdf)
    assert result.extractable
    assert result.unison_digits_shared == 0
    assert result.bars == 32
    assert result.notes == 312
    assert (result.bars_overfull, result.bars_short, result.bars_defective) == (1, 1, 1)


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


def _measure_full(cols, events, budget=None, x_tol=12.0, spacing=5.125):
    """The builder's whole answer: (voices, unmatched_columns,
    unmatched_glyph_notes, unison_digits_shared)."""
    events = sorted(events, key=lambda n: n.x)
    return tabextract._build_measure_beats_glyph(
        cols, 90.0, 600.0, events, [n.x for n in events],
        x_tol=x_tol, notation_spacing=spacing, budget=budget)


def _measure(cols, events, budget=None, x_tol=12.0, spacing=5.125):
    """The first three of the builder's four return values - what almost every
    test here asks it. Use _measure_full for the shared-unison disclosure
    count (issue #137)."""
    return _measure_full(cols, events, budget, x_tol, spacing)[:3]


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
    # One fewer than before _detect_barlines merged a repeat pair's two
    # strokes into one boundary (see BARLINE_STROKE_MERGE_SPACES) - this
    # score carried one of the library's phantom sliver measures.
    assert len(bars) == 49, len(bars)

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
    # 49, not 50 - see test_reference_score_bars_mostly_add_up.
    assert re.search(r"\b\d+ of 49 bar\(s\)", fullness[0]), fullness[0]
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
    start = tabextract._SYSTEM_START_X
    timeline = [(0, 100.0, start, (4, 4)), (1, 200.0, start, (7, 8)),
                (3, 50.0, start, (4, 4))]
    assert tabextract._ts_at(timeline, 0, 150.0, 60.0) == (4, 4)
    assert tabextract._ts_at(timeline, 1, 100.0, 60.0) == (4, 4)   # before the change
    assert tabextract._ts_at(timeline, 1, 250.0, 60.0) == (7, 8)
    assert tabextract._ts_at(timeline, 2, 999.0, 60.0) == (7, 8)   # carried forward
    assert tabextract._ts_at(timeline, 3, 60.0, 60.0) == (4, 4)
    assert tabextract._ts_at(timeline, 0, 10.0, 60.0) is None      # before anything
    # A meter printed at the START of a system governs its first bar, whose
    # left boundary is measured off the tab staff and can land left of the
    # notation staff's own x0.
    assert tabextract._ts_at(timeline, 0, 100.0, -1e6) == (4, 4)


def test_a_meter_further_along_a_bar_is_not_a_meter_at_this_barline(mitsuha_pdf):
    """What is offered as a barline is every vertical that spans the staff,
    and on a notation staff most of those are stems. So the mid-system meter
    reader is asked about positions a bar ahead of the meter it can see, and
    a meter accepted at one of those starts a bar too early.

    A meter is printed before the music it governs, which is the test: a
    notehead between the position asked about and the digits proves the
    digits belong to a later barline. This score is engraved densely enough
    for that to happen - 10 of the library's 21,680 candidate positions, none
    of them reproducible by the engraver the committed fixtures use.

    Three things are checked for each refusal, not only that it happened: the
    exact meter its own window would have read with the guard bypassed (the
    value being refused, not merely a refusal), that the NEAREST accepted
    candidate to its right - not just some later one - reads a meter at all,
    and that it is the SAME meter. That is what proves the refusal loses
    nothing: the digits belong to the barline actually printed just after
    them, one bar later, and reading them there gives the identical answer.

    Refusals from the end-of-system guard are excluded here on purpose - a
    courtesy signature is not recovered at a nearby LATER barline candidate
    the way a stem hazard is; it is recovered at the NEXT system's own
    opening, which is a different reader entirely and has its own coverage
    (see test_a_courtesy_meter_at_the_end_of_a_system_is_not_applied_early).
    """
    page = fitz.open(mitsuha_pdf)[0]
    staves, _ = tabextract._detect_staves(page)
    std = sorted((s for s in staves if s.kind == "standard"), key=lambda s: s.top)[0]
    vseg = tabextract._vertical_segments(page)
    opening = std.x0 + std.spacing * glyph_rhythm.TS_LEAD_SPACINGS
    tol = glyph_rhythm._Tol(std.spacing)
    glyphs = glyph_rhythm.extract_glyph_events(page)
    mid = (std.top + std.bottom) / 2

    refused, accepted = [], []
    for bl in tabextract._detect_barlines(vseg, std):
        bx = bl.x
        if bx <= opening or bx >= std.x1 - std.spacing:
            continue
        ts, why = glyph_rhythm.decode_meter_after_barline(
            page, std.top, std.bottom, bx, std.x1, std.spacing)
        if ts is not None:
            accepted.append((bx, ts))
        elif "courtesy signature" not in why:
            refused.append((bx, why))

    assert refused, "this page has a stem within reading distance of a printed meter"
    for bx, _why in refused:
        # The value the guard is refusing: the same window, read without the
        # notehead-or-rest check (or the clamp that pre-empts it) that
        # decode_meter_after_barline applies.
        window = [e for e in glyphs.events
                  if std.top - tol.spacing <= e.yc <= std.bottom + tol.spacing
                  and bx < e.x0
                  <= bx + tol.spacing * glyph_rhythm._MID_SYSTEM_MAX_LEAD_SPACINGS]
        refused_ts, why_no_ts = glyph_rhythm._signature_from_window(window, mid)
        assert refused_ts is not None, (bx, "nothing for the guard to be refusing", why_no_ts)

        later = sorted((x, t) for x, t in accepted if x > bx)
        assert later, (bx, "nothing accepted after this refused position")
        nearest_x, nearest_ts = later[0]
        assert nearest_ts == refused_ts, (
            bx, refused_ts, "nearest accepted position", nearest_x, nearest_ts)


def test_a_key_change_at_a_mid_system_barline_does_not_hide_the_meter(wild_arms_pdf):
    """Issue #90's window defect, again, but for a meter change printed
    part-way ALONG a system instead of at its start: three flats behind the
    barline push the numerator's left edge to 6.18 staff spaces past it, past
    the flat 5.0-space reach the mid-system reader used to allow, so the
    printed 6/4 was dropped and bars 27-28 were barred in whatever meter
    carried over instead.

    `emitted_meters` reads the sequence back out of the emitted MusicXML, not
    off the ExtractionResult, so a consumer's own view of the file is what is
    being checked."""
    result = tabextract.extract(wild_arms_pdf)
    meters = emitted_meters(result.musicxml)
    # Two of this score's bars used to be the phantom sliver a repeat pair's
    # two strokes left behind before _detect_barlines merged them (see
    # BARLINE_STROKE_MERGE_SPACES) - one inside the (6, 4) run, one in the
    # trailing (4, 4) run - so the run lengths are two shorter than they were.
    assert meters == [(4, 4)] * 25 + [(2, 4)] * 1 + [(6, 4)] * 1 + [(4, 4)] * 22
    assert any("changes time signature part-way through" in w for w in result.warnings), (
        result.warnings)


def test_a_courtesy_meter_at_the_end_of_a_system_is_not_applied_early(kaine_salvation_pdf):
    """The shape that forbids naive widening: a courtesy 6/8 is printed
    behind four sharps at the end of a system, about 7 staff spaces past that
    system's LAST barline, to preview the meter the NEXT system opens in. It
    is well within reach of the accidental-anchored window a key change at a
    mid-system barline needs (see the previous test) - reading it AT that
    barline would start the 6/8 a whole system early.

    The real 6/8 is still read correctly: at the NEXT system's own opening,
    by decode_time_signature, which is a different reader with no
    end-of-system guard to apply (there is no "later system" for an opening
    meter to be mistaken for). So the fix here is checked both ways: the
    courtesy signature produces no premature mid-system entry, AND the
    timeline still carries exactly one real change, to (6, 8), at a system's
    own start."""
    # The courtesy signature is on the score's SECOND page, verified once by
    # inspecting the page directly: its first system's own opening meter is
    # 3/4, and the courtesy signature behind four sharps sits near that
    # system's right edge, previewing the second system's 6/8.
    page = fitz.open(kaine_salvation_pdf)[1]
    staves, _ = tabextract._detect_staves(page)
    stds = sorted((s for s in staves if s.kind == "standard"), key=lambda s: s.top)
    vseg = tabextract._vertical_segments(page)
    courtesy_staff = stds[0]

    # No ACCEPTED mid-system entry may read (6, 8) here at all.
    for x, ts in tabextract._mid_system_meters(page, courtesy_staff, vseg):
        assert ts != (6, 8), (
            "the courtesy signature at the end of this system was read as a change here", x)

    # That is not merely "nothing was found": the courtesy signature is a
    # real, refused candidate, not an untested one. At least one barline
    # candidate on this system decodes to (6, 8) with the guard bypassed
    # (the bare window, no end-of-system check) and is refused by the real
    # reader specifically as a courtesy signature.
    tol = glyph_rhythm._Tol(courtesy_staff.spacing)
    glyphs = glyph_rhythm.extract_glyph_events(page)
    mid = (courtesy_staff.top + courtesy_staff.bottom) / 2
    opening = courtesy_staff.x0 + courtesy_staff.spacing * glyph_rhythm.TS_LEAD_SPACINGS
    found_and_refused = False
    for bl in tabextract._detect_barlines(vseg, courtesy_staff):
        bx = bl.x
        if bx <= opening or bx >= courtesy_staff.x1 - courtesy_staff.spacing:
            continue
        window = [e for e in glyphs.events
                  if courtesy_staff.top - tol.spacing <= e.yc <= courtesy_staff.bottom + tol.spacing
                  and bx < e.x0
                  <= bx + tol.spacing * glyph_rhythm._MID_SYSTEM_MAX_LEAD_SPACINGS]
        bare_ts, _why = glyph_rhythm._signature_from_window(window, mid)
        if bare_ts != (6, 8):
            continue
        ts, why = glyph_rhythm.decode_meter_after_barline(
            page, courtesy_staff.top, courtesy_staff.bottom, bx, courtesy_staff.x1,
            courtesy_staff.spacing)
        assert ts is None and "courtesy signature" in why, (bx, ts, why)
        found_and_refused = True
    assert found_and_refused, "this page's first system carries no (6, 8) candidate to refuse"

    result = tabextract.extract(kaine_salvation_pdf)
    meters = emitted_meters(result.musicxml)
    changes = [(i + 1, m) for i, m in enumerate(meters) if i == 0 or m != meters[i - 1]]
    # One bar earlier than before _detect_barlines merged a repeat pair's two
    # strokes into one boundary (see BARLINE_STROKE_MERGE_SPACES) - this
    # score's first system carried one phantom sliver measure ahead of the
    # (6, 8) change.
    assert changes == [(1, (3, 4)), (27, (6, 8))], changes
    assert any("changes time signature part-way through" in w for w in result.warnings), (
        result.warnings)


def test_ts_timeline_lookup_is_per_bar_not_per_system():
    """A meter printed part-way along a system governs the bars after it and
    not the ones before it - the whole of issue #104."""
    timeline = [(0, 100.0, tabextract._SYSTEM_START_X, (4, 4)),
                (0, 100.0, 300.0, (3, 4))]
    assert tabextract._ts_at(timeline, 0, 100.0, 80.0) == (4, 4)    # first bar
    assert tabextract._ts_at(timeline, 0, 100.0, 299.0) == (4, 4)   # bar before it
    assert tabextract._ts_at(timeline, 0, 100.0, 300.0) == (3, 4)   # the bar itself
    assert tabextract._ts_at(timeline, 0, 100.0, 480.0) == (3, 4)   # after it
    assert tabextract._ts_at(timeline, 1, 60.0, 80.0) == (3, 4)     # next page


def test_ts_at_sorts_an_unsorted_timeline_before_reading_it():
    """_ts_at's own reading depends on the timeline being in (page, y, x)
    order - the loop stops at the first entry it finds "later" than the
    query, so one entry out of order can hide a genuinely earlier one that
    comes after it in the list. Fed out of order on purpose here; the answer
    must be the same as for the correctly-ordered timeline in the previous
    test, not "whatever the input order happened to produce"."""
    unsorted_timeline = [(0, 100.0, 300.0, (3, 4)),
                          (0, 100.0, tabextract._SYSTEM_START_X, (4, 4))]
    assert tabextract._ts_at(unsorted_timeline, 0, 100.0, 80.0) == (4, 4)
    assert tabextract._ts_at(unsorted_timeline, 0, 100.0, 300.0) == (3, 4)


def test_ts_at_anchor_must_match_a_recorded_system_top_exactly():
    """A documented hazard, not a fixed one (see _ts_at's docstring): an
    anchor `y` even a hair GREATER than the system's own recorded top makes
    the 3-tuple comparison decide on `y` alone, before `x` is ever looked
    at, and every entry in that system compares as "at or before" the query
    regardless of its own x. A bar asking about its own early position gets
    back the system's LAST meter instead.

    This is exactly the shape of the `anchor_y` fallback in `_extract`: a
    tab staff with no paired notation staff anchors on its OWN top, which is
    not guaranteed to equal any notation system's recorded y."""
    timeline = [(0, 100.0, tabextract._SYSTEM_START_X, (4, 4)),
                (0, 100.0, 300.0, (3, 4))]
    # The correctly-anchored reading: the first bar (x=80) is still in the
    # opening meter, matching test_ts_timeline_lookup_is_per_bar_not_per_system.
    assert tabextract._ts_at(timeline, 0, 100.0, 80.0) == (4, 4)
    # A hair-high anchor collapses that distinction: x=80 now reads back the
    # system's LAST meter, not its first.
    assert tabextract._ts_at(timeline, 0, 100.0 + 1e-9, 80.0) == (3, 4)


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


# ---------------------------------------------------------------------------
# Repeat barlines and volta brackets (issue #134, phase 1)
# ---------------------------------------------------------------------------


def test_volta_number_regex_rejects_zero_and_leading_zeros():
    """`ending-number` in the MusicXML schema is
    `[1-9][0-9]*(, ?[1-9][0-9]*)*` - positive integers without a leading
    zero. `_VOLTA_NUMBER_RE` used to be `\\d+`, which also matches "0" and
    "01" - neither a valid ending number, and either would have been read
    here and either rejected downstream by the schema or silently accepted
    by a laxer consumer as an ending that does not exist."""
    ok = ["1", "2", "1,2", "10", "1, 2", "12,3"]
    bad = ["0", "00", "01", "1,0", "0,1", "10,02"]
    for text in ok:
        assert tabextract._VOLTA_NUMBER_RE.match(text), text
    for text in bad:
        assert not tabextract._VOLTA_NUMBER_RE.match(text), text


def test_anchor_mark_case_4_is_reachable():
    """Item 4 (issue #134 adversarial review): _anchor_mark's three
    documented branches - on a bar boundary, left of the first fret column,
    right of the last - are exhaustive over the reals (every x is either in
    [lo, hi], less than lo, or greater than hi), so the trailing
    `return None, None` used to be dead code: 0 hits in 200k randomized
    probes, regardless of how far x sat from any boundary. Case 1 now
    requires x to land within ANCHOR_MARK_SNAP_SPACES of the NEAREST bounds
    entry, so an x that is technically inside [lo, hi] but nowhere near a
    real boundary falls through to case 4 instead of snapping to whatever
    happens to be nearest.
    """
    bounds = [0.0, 100.0, 200.0, 300.0]
    lo, hi = 0.0, 300.0
    spacing = 10.0  # ANCHOR_MARK_SNAP_SPACES (1.5) * 10 = 15pt tolerance

    # Exactly on a boundary: case 1, trivially within tolerance.
    assert tabextract._anchor_mark(100.0, bounds, lo, hi, spacing) == (0, 1)
    # Within tolerance of the nearest boundary: still case 1.
    assert tabextract._anchor_mark(112.0, bounds, lo, hi, spacing) == (0, 1)
    # Inside [lo, hi], but farther than the tolerance from EVERY boundary
    # (150.0 sits exactly midway between 100 and 200, 50pt from each,
    # nowhere near the 15pt window around either) - case 4, not a guess.
    assert tabextract._anchor_mark(150.0, bounds, lo, hi, spacing) == (None, None)
    # Left of lo and right of hi still resolve via case 2/3 regardless of
    # distance - those are not proximity-gated, only case 1 is.
    assert tabextract._anchor_mark(-500.0, bounds, lo, hi, spacing) == (None, 0)
    assert tabextract._anchor_mark(800.0, bounds, lo, hi, spacing) == (2, None)


def test_a_thick_stroke_read_as_thin_still_finds_its_repeat_dots(monkeypatch):
    """Item 5 (issue #134 adversarial review): _vertical_segments substitutes
    0.0 for a missing stroke `width` (34 such strokes measured in the
    library - see its own docstring), which reads a genuinely thick stroke
    as thin: shape "t" gets written where "H" belongs. Searching for repeat
    dots used to be gated on "H" in shape, so a group corrupted this way was
    never searched at all - the repeat was dropped with NO disclosure
    whatsoever, not even repeats_unread. Dots are now searched regardless of
    shape, so a stroke whose thickness was lost to this bug can still find
    its own dots and resolve a direction.
    """
    staff = tabextract._Staff("tab", [50, 60, 70, 80, 90, 100], 50, 350)
    # One stroke at x=200, full staff height, width 0.0 - exactly what a
    # missing `width` key substitutes (see _vertical_segments) for what was
    # actually a thick (repeat) stroke on the page.
    vseg = [(200.0, 50.0, 100.0, 0.0)]

    class _FakeEvent:
        def __init__(self, xc, yc):
            self.xc = xc
            self.yc = yc

    # A clean repeat-dot pair to the RIGHT of the stroke (forward repeat):
    # centre line is (50+100)/2=75, REPEAT_DOT_OFFSET_TAB=1.0 spaces * this
    # staff's 10pt spacing = 10pt off centre each way.
    dot_events = [_FakeEvent(210.0, 65.0), _FakeEvent(210.0, 85.0)]

    def fake_dot_events(page, y0, y1, x0, x1):
        return [e for e in dot_events if y0 <= e.yc <= y1 and x0 <= e.xc <= x1]

    monkeypatch.setattr(tabextract.glyph, "dot_like_glyph_events", fake_dot_events)

    barlines = tabextract._detect_barlines(vseg, staff, page=object())
    assert len(barlines) == 1
    bl = barlines[0]
    assert bl.shape == "t", "the width-loss bug this test targets: a thick stroke reads thin"
    assert bl.repeat == "forward"
    assert bl.repeat_unread is False


def test_zeldas_lullaby_reads_the_repeat_and_both_endings(zelda_lullaby_pdf):
    """The phase-1 acceptance case: the score whose exact structure the
    project's one human tester established by hand (issue #134). 23 bars -
    not 24, the phantom sliver a repeat pair's two strokes used to leave
    between them (see BARLINE_STROKE_MERGE_SPACES) - a forward repeat opening
    at measure 1, ending 1 over measures 7-8 closed by a backward repeat,
    ending 2 at measure 9 left open (no closing hook drawn), a double barline
    at measures 10 and 18, and a final barline at measure 23.

    The page's navigation marks are read too, and are asserted separately -
    see test_zeldas_lullaby_reads_its_navigation_marks.
    """
    result = tabextract.extract(zelda_lullaby_pdf)
    assert result.extractable
    assert result.bars == 23
    assert result.bars_unread == 0
    assert result.unread_bars == []

    from test_engraved_fixtures import _barline_structure
    structure = _barline_structure(result.musicxml)
    assert structure[1]["left"] == {"bar_style": "heavy-light", "repeat": "forward"}
    assert structure[7]["left"] == {"ending": ("1", "start")}
    assert structure[8]["right"] == {
        "bar_style": "light-heavy", "ending": ("1", "stop"), "repeat": "backward"}
    assert structure[9]["left"] == {"ending": ("2", "start")}
    assert structure[9]["right"] == {"ending": ("2", "discontinue")}
    assert structure[10]["right"] == {"bar_style": "light-light"}
    assert structure[18]["right"] == {"bar_style": "light-light"}
    assert structure[23]["right"] == {"bar_style": "light-heavy"}
    # No other measure carries a <barline> at all.
    assert set(structure) == {1, 7, 8, 9, 10, 18, 23}

    # Reading the repeat cannot move a single Rule 8 figure (issue #134 Rule
    # 15) - form marks carry no duration.
    assert result.repeats_unread == 0
    assert result.endings_unread == 0
    assert result.endings_truncated == 0
    assert result.form_marks_unanchored == 0

    loaded = _load_musicxml_with_alphatab(result.musicxml, repeats=True)
    expected_order = (
        list(range(1, 9)) + list(range(1, 7)) + list(range(9, 24)))
    assert loaded["tickLookup"] == expected_order


def test_zeldas_lullaby_reads_its_navigation_marks(zelda_lullaby_pdf):
    """The phase-2 acceptance case, on the same score phase 1 used.

    THE FORM THE PAGE DESCRIBES, established from the engraving and from the
    bar numbers the page itself prints at the head of each system (1, 6, 11,
    15, 19 - which agree with the transcription's own numbering):

        "To Coda" above the notation staff of system 2, right-aligned at the
        backward repeat that closes bar 8;
        "D.C. al Coda" above system 4, right-aligned at the double barline
        that closes bar 18;
        the coda sign at the head of system 5, over bar 19, beside the
        printed bar number "19".

    So the piece plays 1-8, repeats to 1, plays 1-6 and takes ending 2 at 9,
    runs on to 18, goes back to the top and plays 1-8 again, and at the To
    Coda leaves for the coda at 19.

    THE ISSUE'S OWN NOTE SAYS MEASURE 14 FOR THE D.C., AND THAT IS WRONG.
    Bar 14 is the last bar of system 3, and the "D.C. al Coda" is engraved
    6.9pt BELOW system 3's tab staff and 29.9pt above system 4's notation
    staff - nearer the system above it, which is how the note came to say
    14. It cannot be 14: bars 15-18 are a section of the piece, and a D.C.
    at 14 would mean nothing ever played them. Reading it as system 4's -
    the nearest staff BELOW the mark, which is the rule this decoder uses -
    puts it at 18, on the same bar as the double barline the page draws to
    end that section.
    """
    result = tabextract.extract(zelda_lullaby_pdf)
    assert result.extractable
    assert result.bars == 23

    from test_engraved_fixtures import _navigation_structure
    assert _navigation_structure(result.musicxml) == {
        8: [("after", "To Coda", {"tocoda": "coda"})],
        18: [("after", "D.C. al Coda", {"dacapo": "yes"})],
        19: [("before", "coda", {"coda": "coda"})],
    }
    # The D.C. and the double barline that ends the same section land on the
    # same bar, which is the page's own corroboration of measure 18.
    from test_engraved_fixtures import _barline_structure
    assert _barline_structure(result.musicxml)[18]["right"] == {
        "bar_style": "light-light"}

    assert result.nav_marks_unanchored == 0
    assert result.nav_marks_unresolved == 0
    assert result.confidence["structure"].startswith("high")

    # A navigation mark carries no duration: the phase-1 figures for this
    # score are the same to the unit.
    assert result.bars_unread == 0
    assert (result.bars_overfull, result.bars_short, result.bars_defective) == (
        1, 11, 12)

    # ... and alphaTab still plays it straight past every one of them, for
    # the reason measured in
    # test_navigation_pdf_is_correct_musicxml_that_alphatab_still_plays_straight:
    # the repeat structure is honoured and the navigation is not.
    loaded = _load_musicxml_with_alphatab(result.musicxml, repeats=True)
    assert loaded["tickLookup"] == (
        list(range(1, 9)) + list(range(1, 7)) + list(range(9, 24)))


def test_lennas_theme_reads_a_volta_that_opens_a_system(lenna_theme_pdf):
    """The adversarial review's own acid test for issue #134's blocker 1: a
    volta bracket opening AT a system start, past the clef and key
    signature, with no barline stroke anywhere near its left end because the
    fret-column filter that builds `bounds` carved that region out entirely
    (see _anchor_mark case 2). The nearest_barline guard in
    _associate_voltas used to run BEFORE _anchor_mark ever got a chance to
    place a mark like this, rejecting it outright - which on this score
    dropped ending 2 and left ending 1 the only ending read, the "reads only
    1 or 2" signature the review measured across 59 scores.

    Both endings are one bar wide here: ending 1 opens and closes on bar 10
    (closed by the backward repeat), ending 2 opens on bar 11 - the very
    next bar, immediately after the repeat - and is left open (no closing
    hook drawn).
    """
    result = tabextract.extract(lenna_theme_pdf)
    assert result.extractable
    # 17, not the 13 this pinned before issue #152: the page's last band
    # prints TWO systems side by side (bars 14-15 on the left, 16-17 on the
    # right), and the pair came back as a 10-line and a 12-line group that
    # were discarded whole. 17 is the number printed on the page.
    assert result.bars == 17
    assert result.bars_unread == 0
    assert result.systems_unread == 0

    from test_engraved_fixtures import _barline_structure
    structure = _barline_structure(result.musicxml)
    assert structure[3]["left"] == {"bar_style": "heavy-light", "repeat": "forward"}
    assert structure[10]["left"] == {"ending": ("1", "start")}
    assert structure[10]["right"] == {
        "bar_style": "light-heavy", "ending": ("1", "stop"), "repeat": "backward"}
    assert structure[11]["left"] == {"ending": ("2", "start")}
    assert structure[11]["right"] == {"ending": ("2", "discontinue")}
    # Bars 15 and 17 join the structure with the recovered systems: 17 is the
    # score's final barline, which was on the system that used to be lost.
    assert set(structure) == {3, 10, 11, 15, 17}
    assert structure[17]["right"] == {"bar_style": "light-heavy"}

    # Reading the volta cannot move a single Rule 8 figure - form marks carry
    # no duration.
    assert result.endings_unread == 0
    assert result.endings_truncated == 0
    assert result.form_marks_unanchored == 0
    assert result.endings_incomplete == 0

    loaded = _load_musicxml_with_alphatab(result.musicxml, repeats=True)
    # ... and the recovered bars 14-17 play out at the end, in the order the
    # page prints them (issue #152): 14-15 on the left-hand system of the
    # last band, 16-17 on the one beside it. (On this page the right-hand
    # system is ruled 1.0pt LOWER, so top order and reading order agree -
    # test_a_right_hand_coda_system_is_read_in_its_printed_order pins the
    # case where they do not.)
    expected_order = list(range(1, 11)) + list(range(3, 10)) + list(range(11, 18))
    assert loaded["tickLookup"] == expected_order


def test_an_incomplete_ending_run_alone_downgrades_structure_confidence(victory_fanfare_pdf):
    """Blocker 2 (issue #134 adversarial review): `structure_issues` (~the
    confidence sum near musicxml build in tabextract.py) used to omit
    `endings_incomplete`, so a score could simultaneously warn that its
    ending numbers do not form a run starting at 1 and report `structure`
    as "high - read directly from the engraving" - two claims that
    contradict each other on the same response.

    This score is the case that isolates the bug: a "2." bracket with no
    matching "1." anywhere, and every OTHER repeat/volta term zero -
    repeats_unread, endings_unread, endings_truncated and
    form_marks_unanchored - so if the sum omits `endings_incomplete` the
    number in the message drops by exactly one, which is what the exact
    count below is asserted for rather than the word "medium".

    Phase 2 (issue #134) leaves that isolation exactly as it was. The score
    prints a "D.S." AND draws the segno it names (see
    test_victory_fanfare_resolves_its_ds_to_the_segno_the_page_draws), so it
    contributes no navigation term at all and the count is 1 - from
    `endings_incomplete` alone, which is what makes dropping that term from
    the sum visible as 1 -> 0 rather than as one less of several.
    """
    result = tabextract.extract(victory_fanfare_pdf)
    assert result.extractable
    assert result.endings_incomplete == 1
    assert result.repeats_unread == 0
    assert result.endings_unread == 0
    assert result.endings_truncated == 0
    assert result.form_marks_unanchored == 0
    assert result.nav_marks_unresolved == 0
    assert result.nav_marks_unanchored == 0
    assert result.confidence["structure"].startswith("medium"), result.confidence["structure"]
    assert "1 repeat/volta/navigation mark(s)" in result.confidence["structure"]


def test_victory_fanfare_resolves_its_ds_to_the_segno_the_page_draws(
        victory_fanfare_pdf):
    """Blocker 1 of the adversarial review on this branch, on a real score.

    The library DOES draw segnos - 88 of them across 84 files - and every one
    is Finale's Maestro glyph ID 4, which glyph_rhythm's calibrated table
    labelled "simile" until the outline was rendered and looked at. While it
    did, every "D.S." in the library was written as words with no
    `<sound dalsegno=>` and disclosed as unresolved, and 76 scores were
    downgraded from `structure` high to medium for it.

    This score is the smallest real case: a segno opening bar 4 and a bare
    "D.S." closing bar 11, and nothing else navigational on the page. The
    engraved `navigation` fixture covers the same shape in a SMuFL font,
    which reaches the segno by an entirely different route (a published
    codepoint, not a glyph ID), so it could never have caught this."""
    result = tabextract.extract(victory_fanfare_pdf)
    assert result.extractable
    from test_engraved_fixtures import _navigation_structure
    assert _navigation_structure(result.musicxml) == {
        4: [("before", "segno", {"segno": "segno"})],
        11: [("after", "D.S.", {"dalsegno": "segno"})],
    }
    assert result.nav_marks_unresolved == 0


def test_two_thick_strokes_with_no_readable_dots_emit_heavy_heavy_not_nothing(
        tarrega_estudio_em_pdf):
    """Item 6 (issue #134 adversarial review): a barline group of two-or-more
    thick strokes ("tHHt" here) with no repeat direction resolved at all -
    not because a dot pair was found and disputed, but because no dot-shaped
    glyph was found near it at all - used to be dropped from the emitted
    MusicXML entirely: no <bar-style>, no <repeat>, no warning.
    `_bar_style_for_shape` deliberately returns None for 2+ thick strokes (it
    expects the "both"-repeat branch in _apply_repeat_marks to write
    heavy-heavy with its own direction attached), which is exactly the gap
    that let this group fall through the top-of-loop guard silently.

    Fixed: this now writes heavy-heavy on both sides of the boundary it
    sits between, with the repeat itself disclosed as unread rather than
    guessed - the same "bar-style for the strokes seen is still written,
    the repeat is not" contract repeats_unread already promises.
    """
    result = tabextract.extract(tarrega_estudio_em_pdf)
    assert result.extractable
    assert result.repeats_unread == 1
    assert result.repeats_unread_bars == [8]

    from test_engraved_fixtures import _barline_structure
    structure = _barline_structure(result.musicxml)
    assert structure[8]["right"] == {"bar_style": "heavy-heavy"}
    assert structure[9]["left"] == {"bar_style": "heavy-heavy"}
    assert "repeat" not in structure[8]["right"]
    assert "repeat" not in structure[9]["left"]

    assert any(
        "dots next to a barline group but the dots could not be resolved" in w
        and "8" in w
        for w in result.warnings
    )
    assert result.confidence["structure"].startswith("medium")


# ---------------------------------------------------------------------------
# Navigation marks (issue #134 phase 2, Rule 16)
# ---------------------------------------------------------------------------


def _nav(text):
    """The single navigation mark `text` reads as, or None."""
    marks = tabextract._nav_text_marks([(text, 0.0, 0.0, 10.0, 5.0)])
    assert len(marks) <= 1, f"{text!r} read as {len(marks)} marks"
    return marks[0] if marks else None


@pytest.mark.parametrize("text,kind,back_to,until,number", [
    ("D.C. al Coda", "jump", "start", "coda", None),
    ("D.S. al Coda", "jump", "segno", "coda", None),
    ("D.C. al Fine", "jump", "start", "fine", None),
    ("D.S. al Fine", "jump", "segno", "fine", None),
    ("D.C.", "jump", "start", None, None),
    ("D. S.", "jump", "segno", None, None),
    ("D.S. 2", "jump", "segno", None, 2),
    ("Da Capo al Fine", "jump", "start", "fine", None),
    ("Dal Segno", "jump", "segno", None, None),
    ("To Coda", "tocoda", None, None, None),
    ("To Coda 2", "tocoda", None, None, 2),
    ("Coda", "coda", None, None, None),
    ("Coda 1", "coda", None, None, 1),
    # Finale draws the sign inside the same text line as the system's own
    # printed bar number, so the line reads like this once the sign's
    # private-use codepoint is stripped out.
    ("41 Coda", "coda", None, None, None),
    ("55 Coda 1", "coda", None, None, 1),
    ("Fine", "fine", None, None, None),
])
def test_a_navigation_phrase_is_read_as_the_instruction_it_prints(
        text, kind, back_to, until, number):
    """The vocabulary, phrase by phrase. `back_to` is where the jump goes
    (the start of the score for a D.C., the segno for a D.S.) and `until` is
    where it stops - the two halves MusicXML carries on different elements,
    so a reader that collapsed them would lose the "al Coda"/"al Fine" half
    entirely."""
    mark = _nav(text)
    assert mark is not None, f"{text!r} was not read as a navigation mark"
    assert (mark.kind, mark.back_to, mark.until, mark.number) == (
        kind, back_to, until, number)


@pytest.mark.parametrize("text", [
    # A phrase that CONTAINS a mark's own words must not be read as one.
    "Fine = Finish, end of the piece",   # a method book's legend, 2 in the library
    "define",
    "Fingering",
    "Andante",
    "capo 2",
    # "Coda" alone is a mark; "Coda" inside a longer phrase is not, and the
    # longer phrases are read as themselves (asserted above), not twice.
    "the coda section repeats",
])
def test_prose_that_merely_contains_a_marks_words_is_not_a_mark(text):
    assert _nav(text) is None, f"{text!r} was read as a navigation mark"


@pytest.mark.parametrize("text", [
    # Every one of these is a text line in this project's library, verbatim,
    # and every one was being emitted as a live <direction>. The first three
    # are performance prose - an instruction to the PLAYER about a jump, not
    # the jump - and "repeat after D.C." was giving Kaine Salvation a
    # <sound dacapo="yes"/> on measure 1 with nothing disclosing it.
    "repeat after D.C.",
    "after D.S. repeat this",
    "on return D.S.",
    "To Coda after repeat",
    # Two lines from one score's own performance notes, which name a D.S.
    # and then say which repeat to take on it.
    "D.S. 1: use second repeat",
    "D.S. 2: use first repeat to Coda",
    # Method-book lines: the phrase followed by its definition. The library
    # prints the first of these twice, once with an "fi" ligature in "fine",
    # which is a different string to match and so is listed as one.
    "D.C. al Fine = Return to the beginning of the piece and play to the fine.",
    "D.C. al Fine = Return to the beginning of the piece and play to the ﬁne.",
    "D.C. al Fine - Return to beginning and play until the Fine.",
    "Da Capo al Fine - Return to the beginning and play until the Fine at the "
    "final barline.",
    "Students should observe the legato use of 3 on A when E occurs before or "
    "after, especially at the D.C. al Fine.",
])
def test_a_line_that_only_contains_a_jump_phrase_is_prose_not_a_jump(text):
    """Blocker 3 of the adversarial review on this branch. The jump and
    "To Coda" patterns were searched for ANYWHERE in a text line while the
    "Fine" one was anchored, so a sentence mentioning a jump became one -
    written out as words with a live <sound> beside it, which tells a reader
    to play a form the engraver never wrote.

    Both patterns are now anchored to the whole line. These 11 lines are
    every prose line in the library that either pattern matched - 10 read as
    a jump and one as a "To Coda", of which six were on pages the extractor
    processes and so were actually written out - and all 11 are refused. All
    176 real jump marks and all 147 real "To Coda" marks in the library still
    read (see the vocabulary tests above and below, which between them cover
    every distinct form of them the library prints)."""
    assert _nav(text) is None, f"{text!r} was read as a navigation mark"


@pytest.mark.parametrize("text,number", [
    ("D.S. al Coda 1", None),      # the coda's number, after the phrase
    ("D.C. al Coda 2", None),
    ("D.S. 1 al Coda 1", 1),       # the D.S.'s own number, before it
    ("D.S. 2 al Coda", 2),
    ("D.C. al Coda x2", None),     # "play it twice"
    ("D.S. al Coda x2", None),
    ("D.C. al Fine.", None),       # a closing full stop
    ("To Coda 1 & 2", None),
])
def test_the_tails_the_library_prints_after_a_jump_still_read(text, number):
    """The other half of anchoring the patterns: the real marks that carry
    something after the phrase must still match. Each of these is a library
    line verbatim."""
    mark = _nav(text)
    assert mark is not None, f"{text!r} was not read as a navigation mark"
    assert mark.number == number


def test_a_numbered_to_coda_list_is_read_without_a_number_rather_than_wrongly():
    """"To Coda 1, 2" names two different codas from one mark, which
    MusicXML's `tocoda` cannot express (it takes one id). Read as an
    unnumbered instruction - which then resolves to nothing on a score with
    more than one coda and is disclosed - rather than silently taking the
    first number and pointing at half of what the page says. Two in the
    library."""
    mark = _nav("To Coda 1, 2")
    assert mark is not None
    assert (mark.kind, mark.number) == ("tocoda", None)


def test_a_coda_label_gives_its_number_to_the_sign_it_labels_not_a_mark_of_its_own(
        monkeypatch):
    """A Maestro page draws the coda sign, the word "Coda", its number and
    the system's own printed bar number as ONE text line, so the line's left
    edge is the bar number's - several bars from where the coda is. The sign
    is what carries the position and the label is what carries the number,
    and the two have to end up as one mark rather than two."""
    sign = tabextract._NavMark("coda", "", 392.3, 609.3, 402.2, 629.8)
    monkeypatch.setattr(tabextract.glyph, "navigation_glyph_events",
                        lambda page: [_FakeGlyph(sign)])
    monkeypatch.setattr(tabextract, "_text_lines", lambda page: [
        # The bar number, the sign and the label, in one line - the sign's
        # own box sits INSIDE this line's box.
        ("55 Coda 1", 360.0, 610.1, 432.6, 623.3),
    ])
    marks = tabextract._read_navigation_marks(object())
    assert len(marks) == 1
    assert (marks[0].kind, marks[0].number) == ("coda", 1)
    assert marks[0].x0 == sign.x0, "the sign's position, not the line's"


def test_the_sign_printed_inside_a_to_coda_is_that_instructions_glyph_not_a_coda(
        monkeypatch):
    """Blocker 4 of the adversarial review on this branch. Six library files
    engrave the instruction as "To Coda (sign)" - the coda glyph inside the
    instruction's own text line, naming the sign it sends the player to.
    Read as a coda SECTION head it took the "To Coda"'s own bar (it is the
    first coda seen, and `codas.setdefault` keeps the first), so the
    instruction pointed at itself and the score wrote `coda="coda"` twice.

    The boxes below are Bygone Days' own, at the coordinates the page
    reports: the glyph's box sits inside the text line's, which is what
    distinguishes it from that score's REAL coda head lower down the page
    (x 417.9-427.8, y 616.6-637.1), which is inside no text line at all."""
    sign = tabextract._NavMark("coda", "", 244.29, 345.18, 254.17, 365.68)
    monkeypatch.setattr(tabextract.glyph, "navigation_glyph_events",
                        lambda page: [_FakeGlyph(sign)])
    monkeypatch.setattr(tabextract, "_text_lines", lambda page: [
        ("To Coda", 197.1, 327.58, 254.17, 377.51),
    ])
    marks = tabextract._read_navigation_marks(object())
    assert [m.kind for m in marks] == ["tocoda"]


def test_a_coda_sign_beside_a_to_coda_rather_than_inside_it_is_still_a_coda(
        monkeypatch):
    """The other side of the test above, so the exclusion cannot be widened
    into "any coda near a To Coda". Containment is the whole rule: a sign
    drawn clear of the instruction's box is a section head, wherever it
    happens to be on the page."""
    sign = tabextract._NavMark("coda", "", 417.9, 616.59, 427.78, 637.09)
    monkeypatch.setattr(tabextract.glyph, "navigation_glyph_events",
                        lambda page: [_FakeGlyph(sign)])
    monkeypatch.setattr(tabextract, "_text_lines", lambda page: [
        ("To Coda", 197.1, 327.58, 254.17, 377.51),
    ])
    marks = tabextract._read_navigation_marks(object())
    assert sorted(m.kind for m in marks) == ["coda", "tocoda"]


class _FakeGlyph:
    """Just enough of a glyph event for _read_navigation_marks."""

    def __init__(self, mark):
        self.category = mark.kind
        self.x0, self.y0, self.x1, self.y1 = mark.x0, mark.y0, mark.x1, mark.y1


def test_a_coda_label_with_no_sign_near_it_is_still_a_coda(monkeypatch):
    """One library file draws its coda in a font this decoder does not
    recognise and prints only the word, so the word alone has to be enough."""
    monkeypatch.setattr(tabextract.glyph, "navigation_glyph_events",
                        lambda page: [])
    monkeypatch.setattr(tabextract, "_text_lines", lambda page: [
        ("Coda", 76.0, 610.0, 104.0, 623.0),
    ])
    marks = tabextract._read_navigation_marks(object())
    assert [(m.kind, m.x0) for m in marks] == [("coda", 76.0)]


def test_a_jump_and_a_sign_are_anchored_by_different_ends_of_their_own_text():
    """The alignment problem, isolated (issue #134 phase 2). Four bars, 100pt
    each. A sign opening a section is anchored by containment of its LEFT
    edge; an instruction is anchored to the bar the nearest boundary closes,
    which is what makes Finale's right-aligned placement and MuseScore's
    left-aligned one land on the same bar."""
    bounds = [0.0, 100.0, 200.0, 300.0, 400.0]
    spacing = 5.0

    def bar_of(mark):
        anchored, refused = tabextract._apply_nav_marks(
            [mark], bounds, 1, spacing)
        assert not refused, f"{mark!r} was refused, not anchored"
        return anchored[0][0]

    # Finale: the instruction is right-aligned, so its text ENDS at bar 2's
    # closing barline and runs backwards into bar 2.
    right_aligned = tabextract._NavMark("jump", "D.C. al Coda", 150.0, 0, 199.0, 5)
    assert bar_of(right_aligned) == 2
    # MuseScore: the same instruction left-aligned at the same barline, so
    # its text STARTS there and runs forward into bar 3. Same bar.
    left_aligned = tabextract._NavMark("jump", "D.C. al Coda", 201.0, 0, 250.0, 5)
    assert bar_of(left_aligned) == 2
    # A sign opens the bar it sits in, and is not snapped to a boundary at
    # all - the coda sign on Zelda's Lullaby sits 34.8pt into its own bar,
    # past the system's clef and key signature.
    sign = tabextract._NavMark("coda", "", 201.0, 0, 215.0, 5)
    assert bar_of(sign) == 3
    # An instruction aligned to nothing falls back to the bar its left edge
    # is in, rather than snapping across half a bar to the nearest barline.
    adrift = tabextract._NavMark("jump", "D.C.", 240.0, 0, 260.0, 5)
    assert bar_of(adrift) == 3
    # A mark that merely OVERHANGS an end still anchors: a right-aligned
    # instruction whose text runs past the last barline is ordinary
    # engraving, and the bar it closes is the last one.
    overhanging = tabextract._NavMark("jump", "D.C.", 380.0, 0, 440.0, 5)
    assert bar_of(overhanging) == 4


def test_a_mark_drawn_entirely_outside_the_staff_is_refused_not_clamped():
    """Blocker 2 of the adversarial review on this branch. A mark past either
    end of the bar grid used to be clamped onto the bar at that end. On the
    layouts that engrave the coda system to the RIGHT of the last system on
    the same band - and whose right-hand system the staff detector loses
    whole (issue #152) - that put the coda sign on the same bar as the
    D.C./D.S. that jumps to it: 40 coda signs library-wide, each one a bar
    off what the page prints, and disclosed as nothing at all.

    Refused instead, and counted in `nav_marks_unanchored`, which is how a
    mark with no bar to name is already handled everywhere else."""
    bounds = [0.0, 100.0, 200.0, 300.0, 400.0]
    spacing = 5.0
    # 1 AM's geometry, to scale: the coda sign sits 7.5 staff spaces past its
    # staff's right end. Nothing in the library sits closer than 3.42 spaces
    # out, so there is no borderline case between this and the overhang above.
    past_the_end = tabextract._NavMark("coda", "", 437.5, 0, 447.4, 5)
    anchored, refused = tabextract._apply_nav_marks(
        [past_the_end], bounds, 1, spacing)
    assert anchored == []
    assert refused == [past_the_end]
    # And the same on the left, which the library happens not to draw.
    before = tabextract._NavMark("coda", "", -20.0, 0, -8.0, 5)
    anchored, refused = tabextract._apply_nav_marks([before], bounds, 1, spacing)
    assert anchored == []
    assert refused == [before]


def test_a_right_hand_coda_system_is_read_in_its_printed_order(
        one_am_pdf, kakariko_village_pdf, imprisoned_town_pdf, nautilus_knoweth_pdf):
    """Issue #152 on the four library pages it was verified against by
    reading them, asserted by the bar numbers those pages PRINT.

    All four engrave the coda as a short system to the RIGHT of the last
    full system, on the same horizontal band. All four lost it - by two
    different routes, both fixed here (see _detect_staves):

      - 1 AM and Kakariko Village rule that system's staff lines short
        enough to fall under the length floor, so it was never seen at all:
        no staff, no anomaly, and no bars, with nothing saying so.

      - Imprisoned Town and The Nautilus Knoweth rule both systems long, a
        shade apart, so the pair clustered as one group with twice the lines
        and was discarded as unreadable.

    The bar numbers below are the ones printed on the pages, not the ones
    the extractor happened to produce: 1 AM prints its coda at 18, Kakariko
    at 37, Imprisoned Town at 34 (of 35), Nautilus at 57 (of 58). Each was
    previously one bar short of its coda, or four to five short of its end.

    THE ORDER IS AN ASSERTION, not a by-product. On 1 AM the right-hand
    system is ruled 0.3pt HIGHER than the one beside it, so ordering staves
    by `top` - which is what this did - puts the coda system's bar FIRST and
    numbers the page backwards. The coda landing on the LAST bar is what
    says reading order beat top order.

    This also closes issue #153's named residual. Nautilus's coda sign could
    not be refused by any x test, because the staff record it was measured
    against spanned the whole page width; here it anchors to the bar the
    page prints it over, and nav_marks_unanchored falls to 0 on all four.
    """
    from test_engraved_fixtures import _navigation_structure

    one_am = tabextract.extract(one_am_pdf)
    assert one_am.extractable
    assert one_am.bars == 18, "the page prints 18 bars"
    assert _navigation_structure(one_am.musicxml) == {
        1: [("before", "segno", {"segno": "segno"})],
        8: [("after", "To Coda", {"tocoda": "coda"})],
        17: [("after", "D.S. al Coda", {"dalsegno": "segno"})],
        18: [("before", "coda", {"coda": "coda"})],
    }
    # The jump and the coda are now on DIFFERENT bars, which is the whole
    # point: the clamp used to put both on 17.
    assert one_am.nav_marks_unanchored == 0
    assert one_am.nav_marks_unresolved_bars == []
    assert one_am.systems_unread == 0

    kakariko = tabextract.extract(kakariko_village_pdf)
    assert kakariko.extractable
    assert kakariko.bars == 37, "the page prints 37 bars"
    assert _navigation_structure(kakariko.musicxml) == {
        19: [("after", "To Coda", {"tocoda": "coda"})],
        36: [("after", "D.C. al Coda", {"dacapo": "yes"})],
        37: [("before", "coda", {"coda": "coda"})],
    }
    assert kakariko.nav_marks_unanchored == 0
    assert kakariko.nav_marks_unresolved_bars == []
    assert kakariko.systems_unread == 0

    # The 12-line route. Its two notation staves were ruled at the same y and
    # had merged into one full-width staff, so this page lost a whole band of
    # four printed bars (32-35) while reporting a staff for it.
    imprisoned = tabextract.extract(imprisoned_town_pdf)
    assert imprisoned.extractable
    assert imprisoned.bars == 35, "the page prints 35 bars"
    assert _navigation_structure(imprisoned.musicxml) == {
        14: [("after", "To Coda", {"tocoda": "coda"})],
        33: [("after", "D.C. al Coda", {"dacapo": "yes"})],
        34: [("before", "coda", {"coda": "coda"})],
    }
    assert imprisoned.nav_marks_unanchored == 0
    assert imprisoned.systems_unread == 0

    # Both groups at once - a 10-line pair of notation staves and a 12-line
    # pair of tab staves on one band - and issue #153's named residual.
    nautilus = tabextract.extract(nautilus_knoweth_pdf)
    assert nautilus.extractable
    assert nautilus.bars == 58, "the page prints 58 bars"
    assert _navigation_structure(nautilus.musicxml) == {
        16: [("after", "To Coda", {"tocoda": "coda"})],
        56: [("after", "D.C. al Coda", {"dacapo": "yes"})],
        57: [("before", "coda", {"coda": "coda"})],
    }
    assert nautilus.nav_marks_unanchored == 0
    assert nautilus.systems_unread == 0


def test_the_two_systems_on_one_band_are_separate_staves(
        imprisoned_town_pdf, one_am_pdf):
    """The geometry issue #152 turns on, asserted directly rather than only
    through the bar counts it produces.

    Imprisoned Town's last band holds two systems whose staff lines do not
    overlap in x at all - 54.0-341.7 and 378.2-575.9, a 36.5pt gap - and
    whose tab staves are ruled 1.7pt apart, which is what interleaved them
    into one 12-line group inside the 15.0pt cluster gap. Its notation
    staves are ruled at the IDENTICAL y, which is the other half of the same
    defect: those merged silently into a single staff record spanning
    54.0-575.9, describing music across a 36.5pt gap where the page draws
    none.

    1 AM is the short-lines route, and pins the ordering hazard: its
    right-hand system is ruled HIGHER (683.9 against 684.2), so `top` alone
    orders that band right to left.
    """
    import fitz

    with fitz.open(imprisoned_town_pdf) as doc:
        staves, anomalies = tabextract._detect_staves(doc[1])
    assert anomalies == [], "no 12-line group is left to discard"
    last = [s for s in staves if s.band == max(t.band for t in staves)]
    assert [(s.kind, round(s.x0, 1), round(s.x1, 1)) for s in last] == [
        ("tab", 54.0, 341.7), ("tab", 378.2, 575.9)]
    # ... and the notation band above it is two staves too, not the one
    # full-width record it used to be.
    stds = [s for s in staves if s.kind == "standard" and s.top > 600]
    assert [(round(s.x0, 1), round(s.x1, 1)) for s in stds] == [
        (54.0, 341.7), (378.2, 575.9)]

    with fitz.open(one_am_pdf) as doc:
        staves, anomalies = tabextract._detect_staves(doc[0])
    assert anomalies == []
    band = max(s.band for s in staves)
    last = [s for s in staves if s.band == band]
    assert [(round(s.top, 1), round(s.x0, 1)) for s in last] == [
        (684.2, 54.0), (683.9, 441.2)]
    # The right-hand system is the HIGHER of the two, so reading order and
    # top order genuinely disagree here - and reading order is what comes
    # back, left to right.
    assert last[1].top < last[0].top
    assert [s.reading_order for s in last] == sorted(s.reading_order for s in last)


def test_performance_prose_naming_a_jump_writes_no_jump(kaine_salvation_pdf):
    """Blocker 3 of the adversarial review, on the page it was verified
    against. Kaine Salvation prints "only do the second / repeat after D.C."
    as a note to the player above its first system; the unanchored jump
    pattern read the second line of that as a D.C. and gave measure 1 a live
    `<sound dacapo="yes"/>`, disclosed as nothing. The score's one REAL jump,
    a bare "D.C." closing its last bar, still reads."""
    result = tabextract.extract(kaine_salvation_pdf)
    assert result.extractable
    from test_engraved_fixtures import _navigation_structure
    assert _navigation_structure(result.musicxml) == {
        42: [("after", "D.C.", {"dacapo": "yes"})],
    }
    assert result.nav_marks_unresolved == 0
    assert result.nav_marks_unanchored == 0


def test_the_sign_inside_a_to_coda_does_not_become_the_coda_it_points_at(
        bygone_days_pdf):
    """Blocker 4 of the adversarial review, on the page it was verified
    against. Bygone Days engraves "To Coda (sign)" closing bar 12 and its
    real coda head opening bar 24. The glyph inside the instruction was read
    as a coda section head, and being the first one seen it took bar 12 - so
    the "To Coda" pointed at its own measure and the score wrote
    `coda="coda"` twice.

    The coda head is bar 24, not the 25 this pinned before issue #152: this
    page's last band prints two systems side by side (22-23 on the left, the
    one-bar coda 24 on the right) ruled at the same y, which merged into ONE
    full-width staff record spanning the gap between them - and the gap
    produced a bar boundary the page does not draw, so the score came out a
    bar long. 24 is the number printed on the page."""
    result = tabextract.extract(bygone_days_pdf)
    assert result.extractable
    assert result.bars == 24, "the page prints 24 bars"
    from test_engraved_fixtures import _navigation_structure
    assert _navigation_structure(result.musicxml) == {
        12: [("after", "To Coda", {"tocoda": "coda"})],
        23: [("after", "D.C. al Coda", {"dacapo": "yes"})],
        24: [("before", "coda", {"coda": "coda"})],
    }
    assert result.musicxml.count("<coda />") == 1
    assert result.nav_marks_unresolved == 0
    assert result.nav_marks_unanchored == 0


def _zelda_page_staves():
    """Systems 3, 4 and 5 of Zelda's Lullaby's only page, at the coordinates
    the staff detector actually reports for them - so the assertions below
    are about the real geometry rather than about numbers chosen to make a
    rule look good."""
    def staff(kind, top, spacing, lines):
        return tabextract._Staff(
            kind, [top + i * spacing for i in range(lines)], 41.2, 575.9)

    return {
        "s3_std": staff("standard", 361.5, 5.12, 5),
        "s3_tab": staff("tab", 427.1, 7.68, 6),     # bottom 465.5
        "s4_std": staff("standard", 515.5, 5.12, 5),
        "s4_tab": staff("tab", 576.1, 7.68, 6),     # bottom 614.5
        "s5_std": staff("standard", 658.5, 5.12, 5),
        "s5_tab": staff("tab", 721.7, 7.70, 6),
    }


def _assign_on(staves_by_name, mark):
    staves = list(staves_by_name.values())
    tab_for_top = {}
    for name, s in staves_by_name.items():
        if name.endswith("_std"):
            tab_for_top[id(s)] = staves_by_name[name.replace("_std", "_tab")]
        else:
            tab_for_top[id(s)] = s
    buckets, unowned = tabextract._assign_nav_marks([mark], staves, tab_for_top)
    if unowned:
        return None
    owner_id = next(iter(buckets))
    return next(n for n, s in staves_by_name.items() if id(s) == owner_id)


def test_a_navigation_mark_belongs_to_the_nearest_staff_below_it():
    """The rule, on the geometry that discriminates it.

    A guitar system is notation over tablature, so the gap above one
    system's notation staff is also the gap below the previous system's tab
    staff - and on Zelda's Lullaby, where the page prints its own bar
    numbers to check the answer against, the mark is NEARER the wrong one:

      "D.C. al Coda"  6.9pt below system 3's tab staff and 29.9pt above
                      system 4's notation staff, and it is system 4's.
      the coda sign   7.4pt below system 4's tab staff and 16.1pt above
                      system 5's notation staff, and it is system 5's,
                      which is what the page prints beside that system.

    Nearest-staff-by-distance gets both of those wrong, in the direction
    that leaves four bars of the piece played by nothing."""
    staves = _zelda_page_staves()

    dc = tabextract._NavMark("jump", "D.C. al Coda", 508.9, 472.4, 575.6, 485.6)
    assert _assign_on(staves, dc) == "s4_tab"

    coda = tabextract._NavMark("coda", "", 76.0, 621.9, 85.9, 642.4)
    assert _assign_on(staves, coda) == "s5_tab"

    # And the case where the two readings agree, so that the rule is shown
    # to be right rather than merely different: "To Coda" sits 15.3pt below
    # system 1's tab staff and 13.8pt above system 2's notation staff.
    two = {"s3_std": staves["s3_std"], "s3_tab": staves["s3_tab"],
           "s4_std": staves["s4_std"], "s4_tab": staves["s4_tab"]}
    to_coda = tabextract._NavMark("tocoda", "To Coda", 340.4, 480.8, 384.6, 494.1)
    assert _assign_on(two, to_coda) == "s4_tab"


def test_a_navigation_mark_between_a_systems_two_staves_belongs_to_that_system():
    """The second entry `tab_for_top` carries. A mark drawn between a
    system's notation staff and its tablature has the TAB staff as the
    nearest thing below it, and belongs to that system - 57 of the 569
    navigation marks this extractor reads off the library are placed there,
    and without this they were disclosed as having no bar grid to land on
    when they had one."""
    staves = _zelda_page_staves()
    between = tabextract._NavMark("fine", "Fine", 340.0, 400.0, 360.0, 410.0)
    assert _assign_on(staves, between) == "s3_tab"


def test_a_navigation_mark_too_far_from_any_staff_is_page_furniture():
    """NAV_BAND_SPACES is 12 of the owning staff's own spaces - 61pt on
    these notation staves - against a measured worst case of 10.87 spaces
    across every navigation mark in the library. A phrase farther off than
    that is annotating nothing."""
    staves = _zelda_page_staves()
    high = tabextract._NavMark("fine", "Fine", 340.0, 280.0, 360.0, 290.0)
    assert _assign_on(staves, high) is None


def test_a_navigation_mark_below_the_last_staff_belongs_to_the_staff_above_it():
    """Where an engraver puts a closing instruction. Nothing follows it, so
    there is nothing for it to be ambiguous with."""
    staves = _zelda_page_staves()
    below = tabextract._NavMark("jump", "D.C.", 500.0, 775.0, 560.0, 785.0)
    assert _assign_on(staves, below) == "s5_tab"


def test_a_navigation_mark_on_a_staff_with_no_bar_grid_is_disclosed():
    """A notation staff whose tab partner was never detected (the 12-line
    staff-line anomaly on Imprisoned Town's last page is the real case) has
    no bars for a mark above it to name. Disclosed, not pushed onto the
    neighbouring system's bars."""
    def staff(kind, top, spacing, lines):
        return tabextract._Staff(
            kind, [top + i * spacing for i in range(lines)], 40.0, 570.0)

    orphan_std = staff("standard", 220.0, 5.0, 5)
    mark = tabextract._NavMark("coda", "", 80.0, 200.0, 92.0, 210.0)
    buckets, unowned = tabextract._assign_nav_marks([mark], [orphan_std], {})
    assert buckets == {}
    assert unowned == [mark]


def test_a_d_s_with_no_segno_is_written_as_words_with_no_jump_and_disclosed():
    """The library's RAREST navigation case, and still the reason phase 2
    cannot simply write every jump it reads.

    An earlier version of this docstring called it the dominant case, on the
    strength of "86 of 297 files print D.S. and not one file in the library
    draws a segno for it to name - measured twice, once over every
    categorised music glyph and once over every UNcategorised one". That is
    false and the method named in it is the reason it went unnoticed: the
    library draws 88 segnos across 84 files, all of them Finale's Maestro
    glyph ID 4, which the calibrated table labelled "simile" - and a glyph in
    the WRONG category is in neither of those two sweeps. See Rule 16 in
    docs/musicxml-tab-profile.md, which retracts the claim in full.

    Measured now: 86 files print a "D.S." and 84 of them draw the segno it
    names. Two do not - Hollow (Final Fantasy VII Remake) and Rebel Army
    Theme (Final Fantasy II). A third, Rito Village - Night, used to be here
    too: its Maestro embed was filtered out by resource name before any
    glyph on it was read at all, which issue #154 fixed by fingerprinting a
    TrueType resource regardless of its name - it now draws its segno like
    every other Maestro file. Those two remaining files are what this branch
    exists for.

    A `<sound dalsegno=>` naming a segno that is not in the file would make
    the transcription play a form nobody engraved, so the instruction is
    written as the words the page prints and the bar is counted."""
    ds = tabextract._NavMark("jump", "D.S. al Coda", 100.0, 0, 160.0, 5,
                             back_to="segno", until="coda")
    coda = tabextract._NavMark("coda", "", 40.0, 0, 52.0, 5)
    directions, unresolved, _refused = tabextract._resolve_nav_marks([(4, ds), (6, coda)])
    assert unresolved == [4]
    assert directions[4]["after"] == [{"words": "D.S. al Coda", "sound": None}]
    # The coda sign itself is still written: it was read in full.
    assert directions[6]["before"] == [{"symbol": "coda", "sound": {"coda": "coda"}}]

    # Give it a segno and the same instruction resolves.
    segno = tabextract._NavMark("segno", "", 40.0, 0, 52.0, 5)
    directions, unresolved, _refused = tabextract._resolve_nav_marks(
        [(1, segno), (4, ds), (6, coda)])
    assert unresolved == []
    assert directions[4]["after"] == [
        {"words": "D.S. al Coda", "sound": {"dalsegno": "segno"}}]


def test_a_d_c_needs_no_mark_to_point_at_but_its_al_fine_half_does():
    """A D.C. goes back to the start of the score, which is always there -
    so `dacapo` is written whatever else the page draws. "al Fine" is the
    other half of the same instruction and names a mark that may not be
    there; MusicXML carries it on the Fine itself, so the jump is written
    either way and the bar is disclosed when the Fine is missing."""
    dc = tabextract._NavMark("jump", "D.C. al Fine", 100.0, 0, 160.0, 5,
                             back_to="start", until="fine")
    directions, unresolved, _refused = tabextract._resolve_nav_marks([(8, dc)])
    assert directions[8]["after"] == [
        {"words": "D.C. al Fine", "sound": {"dacapo": "yes"}}]
    assert unresolved == [8]

    fine = tabextract._NavMark("fine", "Fine", 40.0, 0, 60.0, 5)
    directions, unresolved, _refused = tabextract._resolve_nav_marks([(6, fine), (8, dc)])
    assert unresolved == []
    assert directions[6]["after"] == [{"words": "Fine", "sound": {"fine": "yes"}}]


def test_a_numbered_to_coda_names_the_coda_that_carries_the_same_number():
    """The Oeth arrangements number theirs ("To Coda 1" / "Coda 2"), and
    MusicXML's `coda`/`tocoda` are ids precisely so more than one can exist.
    An unnumbered "To Coda" on a score with one coda names that one; on a
    score with several it names none of them unambiguously and is
    disclosed."""
    coda1 = tabextract._NavMark("coda", "", 40.0, 0, 52.0, 5, number=1)
    coda2 = tabextract._NavMark("coda", "", 40.0, 0, 52.0, 5, number=2)
    to2 = tabextract._NavMark("tocoda", "To Coda 2", 100.0, 0, 150.0, 5, number=2)
    directions, unresolved, _refused = tabextract._resolve_nav_marks(
        [(4, to2), (10, coda1), (14, coda2)])
    assert unresolved == []
    assert directions[4]["after"] == [
        {"words": "To Coda 2", "sound": {"tocoda": "coda2"}}]
    assert directions[10]["before"] == [{"symbol": "coda", "sound": {"coda": "coda1"}}]
    assert directions[14]["before"] == [{"symbol": "coda", "sound": {"coda": "coda2"}}]

    plain = tabextract._NavMark("tocoda", "To Coda", 100.0, 0, 150.0, 5)
    _d, unresolved, _refused = tabextract._resolve_nav_marks([(4, plain), (10, coda1), (14, coda2)])
    assert unresolved == [4], "two codas, and nothing says which one"
    _d, unresolved, _refused = tabextract._resolve_nav_marks([(4, plain), (10, coda1)])
    assert unresolved == []


def test_a_refused_coda_changes_the_disclosure_wording_and_not_a_single_count():
    """One root cause, two counters, one sentence that says which.

    Measured over the library: 79 of the 87 `nav_marks_unresolved` bars, in
    40 of the 47 files, are on scores whose coda sign was REFUSED for sitting
    outside its staff - the same defect `nav_marks_unanchored` is already
    reporting on the same score. Only 8 bars, in 7 files, name a target the
    page genuinely does not draw.

    The fix is prose, not arithmetic: no bar moves between the counters and
    no third counter exists, because `nav_marks_unresolved` means exactly one
    thing. What changes is whether the score is told "no coda read" or "the
    coda is drawn on a system this transcription does not hold"."""
    tocoda = tabextract._NavMark("tocoda", "To Coda", 100.0, 0, 150.0, 5)
    coda = tabextract._NavMark("coda", "", 480.0, 0, 492.0, 5)

    _d, unresolved, refused_flag = tabextract._resolve_nav_marks([(4, tocoda)])
    assert unresolved == [4] and refused_flag is False, "nothing was refused"

    _d, unresolved, refused_flag = tabextract._resolve_nav_marks(
        [(4, tocoda)], refused=[coda])
    assert unresolved == [4], "the count is identical either way"
    assert refused_flag is True

    # A refused mark that is not a coda says nothing about a missing coda.
    jump = tabextract._NavMark("jump", "D.S. 2", 480.0, 0, 520.0, 5,
                               back_to="segno")
    _d, _u, refused_flag = tabextract._resolve_nav_marks(
        [(4, tocoda)], refused=[jump])
    assert refused_flag is False


def test_the_unresolved_warning_says_which_cause_it_was(phantom_train_pdf):
    """The two causes of an unresolved jump still say which one they were.

    *Phantom Train* prints a "To Coda" on a score that draws no coda sign
    and no coda label anywhere at all - genuinely target-less - and is
    asserted against the real page.

    THE OTHER CAUSE NO LONGER HAPPENS IN THIS LIBRARY, so it is exercised
    directly instead of through a score. A coda REFUSED for having no bar to
    name used to be the reason for 79 of the library's 87 unresolved bars;
    since issue #152 reads the systems those codas were drawn on,
    `nav_marks_unanchored` is 0 across all 297 files and no score reaches
    this branch. The branch is still right and still reachable - a mark can
    be drawn outside its staff's x span for reasons other than a lost system
    - so it keeps its test, on a constructed refusal rather than on a score
    that would silently stop covering it."""
    absent = tabextract.extract(phantom_train_pdf)
    assert absent.nav_marks_unanchored == 0
    assert any("al Coda with no coda read" in w for w in absent.warnings), \
        absent.warnings

    # A "To Coda" that was anchored, and a coda sign that was not. The
    # instruction goes out without its jump either way; what differs is
    # which sentence the reader gets.
    to_coda = tabextract._NavMark("tocoda", "To Coda", 10.0, 0, 60.0, 5)
    coda_sign = tabextract._NavMark("coda", "", 900.0, 0, 910.0, 5)
    directions, unresolved, coda_was_refused = tabextract._resolve_nav_marks(
        [(4, to_coda)], refused=[coda_sign])
    assert unresolved == [4], "the bar carrying the instruction is disclosed"
    assert coda_was_refused, "and the cause is the refused coda, not an absent one"
    # Written as the words the page prints, with no <sound> jump beside it -
    # naming a coda this file does not hold would make it play a form nobody
    # engraved.
    assert directions[4]["after"] == [{"words": "To Coda", "sound": None}]

    # With nothing refused, the same bar is unresolved for the other reason.
    _d, unresolved, coda_was_refused = tabextract._resolve_nav_marks([(4, to_coda)])
    assert unresolved == [4]
    assert not coda_was_refused


def _library_pdfs(library_root):
    return sorted(library_root.rglob("*.pdf"))


def test_library_wide_repeat_structure_leaves_conformance_untouched(library_root):
    """The invariant issue #134's own research corrected the issue for:
    reading repeat barlines and volta brackets requires collapsing
    multi-stroke barlines first (see BARLINE_STROKE_MERGE_SPACES), which does
    move the bar count and bars_unread - but must NOT move a single Rule 8
    conformance figure, because a form mark carries no duration.

    Run across the whole configured library (297 PDFs in the one this
    profile was developed against). Slow on purpose: this is the one test
    that actually proves the invariant end to end rather than on one
    fixture.
    """
    totals = collections.Counter()
    scores_with_structure = 0
    scores_with_navigation = 0
    scores_with_endings_truncated = 0
    scores_with_systems_unread = 0
    extractable = 0
    for pdf in _library_pdfs(library_root):
        try:
            result = tabextract.extract(pdf)
        except Exception:
            continue
        if not result.extractable:
            continue
        extractable += 1
        totals["bars"] += result.bars
        totals["bars_unread"] += result.bars_unread
        totals["notes"] += result.notes
        totals["bars_overfull"] += result.bars_overfull
        totals["bars_short"] += result.bars_short
        totals["bars_defective"] += result.bars_defective
        totals["bars_padded"] += result.bars_padded
        totals["inferred_rest_quarters"] += result.inferred_rest_quarters
        totals["form_marks_unanchored"] += result.form_marks_unanchored
        totals["endings_truncated"] += result.endings_truncated
        totals["unison_digits_shared"] += result.unison_digits_shared
        totals["nav_marks_unanchored"] += result.nav_marks_unanchored
        totals["nav_marks_unresolved"] += result.nav_marks_unresolved
        totals["systems_unread"] += result.systems_unread
        totals["coda_signs"] += result.musicxml.count("<coda />")
        totals["segno_signs"] += result.musicxml.count("<segno />")
        totals["nav_directions"] += (result.musicxml.count("<direction ")
                                     - result.musicxml.count("<metronome>"))
        totals["structure_" + result.confidence["structure"].split(" ")[0]] += 1
        if result.endings_truncated:
            scores_with_endings_truncated += 1
        if result.systems_unread:
            scores_with_systems_unread += 1
        if "<repeat " in result.musicxml or "<ending " in result.musicxml:
            scores_with_structure += 1
        if (result.musicxml.count("<direction ")
                > result.musicxml.count("<metronome>")):
            scores_with_navigation += 1

    # The exact figures issue #134's commit 1 fix produces on this library -
    # see the same numbers pinned by the library-wide scan in the PR/issue -
    # as moved, once, by issue #137's shared-unison digit (see
    # tabextract._share_unison_digits). #137 changes exactly two scores and
    # nothing else in these 293, measured score by score rather than in
    # aggregate: The Cosmic Wheel (FF XI), where 12 bars stop being short of
    # an eighth they were padded with silence for (notes +12, bars_short and
    # bars_defective and bars_padded -12, inferred_rest_quarters -6.0), and
    # Castti, the Apothecary (Octopath Traveler II), where 4 notes come back
    # into a voice that had been padded around them (notes +4,
    # inferred_rest_quarters -2.0) with every conformance figure of its own
    # unmoved. bars, bars_unread and notes do not move at all: nothing here
    # reads a barline or a bracket, and nothing here changes which fret
    # numbers were assigned.
    #
    # bars_overfull/bars_short/bars_defective/bars_padded/inferred_rest_quarters
    # moved a SECOND time, again by exactly one score, when issue #154 fixed
    # load_music_fonts rejecting a music font by resource name before its
    # fingerprint was ever consulted: "Rito Village - Night (The Legend of
    # Zelda Breath of the Wild)" embeds its Maestro subset as a resource
    # named "CIDFont+F1" (every embedded font in that PDF was renamed
    # generically), so this decoder used to read zero glyphs from a fully
    # engraved score and fall back to the spacing heuristic for its rhythm.
    # Measured score by score, exactly this one file's own figures move -
    # bars_overfull +4, bars_short -19, bars_defective -15, bars_padded +26,
    # inferred_rest_quarters +22.0 - with bars (64), notes (411) and every
    # other score's output byte-for-byte unchanged.
    #
    # AND THEN MOVED BY ISSUE #152, WHICH IS THE ONE CHANGE HERE THAT IS
    # SUPPOSED TO MOVE THEM. Everything above this line is about reading the
    # same music better; #152 is about reading music that was not being read
    # at all - a system printed to the RIGHT of the last full one on the same
    # band. So the conformance figures MUST move: the recovered bars carry
    # notes, and those notes add up or fail to add up like any others.
    #
    # Measured score by score against this branch's parent: PLACEHOLDER_IDENT
    # of the 297 files come out BYTE-IDENTICAL, and all PLACEHOLDER_CHANGED
    # that differ are files whose bar count changed. No score's output moved
    # without its bar count moving, which is the check that this reads new
    # music rather than re-reading the old music differently.
    #
    # 3 scores LOSE a bar - Our Terms 29->28, Bygone Days 25->24 and The
    # Crestlands 39->38 - and those three are corrections too, verified
    # against the printed pages: their two side-by-side systems were ruled at
    # the same y and had merged into ONE full-width staff record, so the gap
    # between the systems produced a bar boundary the page does not draw. Our
    # Terms prints 28 bars, Bygone Days 24, and The Crestlands 37 plus a
    # pickup measure.
    assert extractable == 293
    assert totals["bars"] == P_BARS
    assert totals["bars_unread"] == P_BARS_UNREAD
    assert totals["notes"] == P_NOTES
    assert totals["bars_overfull"] == P_OVERFULL
    assert totals["bars_short"] == P_SHORT
    assert totals["bars_defective"] == P_DEFECTIVE
    assert totals["bars_padded"] == P_PADDED
    assert totals["inferred_rest_quarters"] == P_IRQ
    # The systems still lost, named. Both are 7-line groups - a 6-line tab
    # staff with ONE extra full-width rule ruled 14.3pt below it, inside the
    # 15.0pt cluster gap - which is a different defect from #152's two
    # systems side by side, and one no split by x extent can reach, because
    # the stray rule spans the same extent the staff does. Dynamis p1 and
    # Hide, Hideaway p2. Before this change the same measurement over the
    # library counted 41 such systems across 22 files.
    assert totals["systems_unread"] == P_SYSU
    assert scores_with_systems_unread == P_SYSU_SCORES
    # The whole of #137's effect on this library, disclosed as data: 16 notes
    # given a fret number read for their coincident twin. It equals the note
    # delta above exactly (+12 +4), which is the check that no note came back
    # by any other route - a shared digit is the ONLY way this change can add
    # a note, so any drift between these two numbers is a different defect.
    assert totals["unison_digits_shared"] == 16
    # Not a judgement call - issue #134 S3.2 measured 0 of 513 repeat marks
    # in the library landing inside a bar with no boundary to anchor to, and
    # this stays 0 for volta brackets too once the adversarial review's
    # blocker 1 (the anchor guard running before _anchor_mark) is fixed - the
    # library holds no numbered bracket _anchor_mark's own case 4 rejects.
    assert totals["form_marks_unanchored"] == 0
    # Disclosed but previously unpinned (adversarial review, "smaller" list):
    # an ending whose last bar could not be established (no backward repeat,
    # and the drawn right end snaps to no boundary) is written over its
    # first bar only. Measured directly against the spec's own predicted 3 -
    # the gap is the review's own finding, not a regression to chase here.
    # 25 + 3 (issue #152): Hollow, Our Terms and Link is Awake each gain one,
    # all three on a system that was previously not read at all, whose volta
    # bracket has no closing hook drawn and so is written over its first bar
    # only - the same disclosure these 25 already were, on new music.
    assert totals["endings_truncated"] == 28
    assert scores_with_endings_truncated == 25
    # "Expect large" (issue #134's own phrasing): the census found 190 of 297
    # scores carrying a repeat barline or a volta. A floor rather than a pin,
    # since which scores the maintainer's library holds can change.
    assert scores_with_structure >= 150

    # PHASE 2, and the same invariant for the same reason: a navigation mark
    # carries no duration either, so every Rule 8 figure asserted above is
    # unmoved by reading them. Measured directly - the whole library was
    # extracted on this branch's parent and on this branch, score by score,
    # and all of bars/bars_measured/notes/beats came out identical on all
    # 297, with 166 of them gaining navigation marks in their MusicXML. (The
    # bars_overfull/short/defective/padded/inferred_rest_quarters figures
    # above are NOT part of that invariant - they are rhythm figures, and
    # issue #154, layered on the same branch, moved exactly one score's.)
    #
    # 527 <direction> elements over the library, and the accounting for it is
    # exact: a full-page census of every navigation mark on every page reads
    # 581 (176 jumps, 150 codas, 147 "To Coda", 88 segnos, 20 "Fine"), of
    # which 11 are on pages this extractor does not process at all - which
    # carry no bars either - and 43 are disclosed as unanchored below.
    # 581 - 11 - 43 = 527, before issue #152.
    #
    # 527 + PLACEHOLDER_NAVD (issue #152): the 43 marks that used to be
    # disclosed as unanchored now name a bar and are written as directions.
    assert totals["nav_directions"] == P_NAVDIR
    assert scores_with_navigation == 166
    # 109 coda signs written, of the 156 the library draws. The 47 not
    # written: 41 sit entirely past their staff's right end, on the
    # coda-system layout whose right-hand system the staff detector loses
    # (see test_a_coda_drawn_on_a_lost_right_hand_system_is_disclosed_not_moved
    # and issue #152) and are disclosed as unanchored rather than clamped
    # onto the jump's own bar - Rito Village's own coda sign, now read as a
    # glyph like the rest of its page (issue #154), is one of these 41: it
    # sits outside its own staff's bar span the same way the other 40 do, so
    # it is still read from the word beside it rather than the sign's own
    # position; being outside the decoder's font vocabulary was never why
    # this one mark placed by word - and 6 are the reference glyph printed
    # inside a "To Coda" and are that instruction's, not section heads. One
    # more coda MARK reaches the count from elsewhere: Imprisoned Town's
    # sign has no bars on its system.
    #
    # And 88 segnos, from 84 files - all of them Finale's Maestro GID 4,
    # which the calibrated glyph table labelled "simile" until the outline
    # was rendered and looked at. While it did, this assertion read 0 and
    # every "D.S." in the library went out without its jump. The 84th file
    # is Rito Village - Night: issue #154 fixed load_music_fonts rejecting
    # its Maestro subset by resource name (embedded as "CIDFont+F1") before
    # the fingerprint that would have recognised it regardless ever ran.
    #
    # 109 + 41 (issue #152), and the accounting is exact: of the 43 marks
    # that were unanchored, 41 are coda signs - the 40 sitting entirely past
    # their staff's right end, plus Imprisoned Town's, whose system had no
    # bars at all - and every one of them now anchors to the bar its page
    # prints it over. The other 2 were a "D.S. 2" and Imprisoned Town's D.C.
    assert totals["coda_signs"] == P_CODA
    assert totals["segno_signs"] == P_SEGNO
    # The disclosure, pinned rather than assumed. 87 BARS (the counter counts
    # distinct bars, so two instructions closing one bar contribute one)
    # carry an instruction naming a jump this transcription holds no target
    # for. By reason, over 87 instructions on 87 distinct bars in 47 files:
    # 45 a "To Coda" with no coda read, 40 an "al Coda" with none, and 2 a
    # "D.S." on a score that genuinely draws no segno - Hollow (Final
    # Fantasy VII Remake) and Rebel Army Theme (Final Fantasy II). Most of
    # the first two are the lost coda system above: the page draws the coda,
    # this transcription could not place it.
    #
    # Rito Village - Night moved OUT of the "D.S. genuinely no segno" bucket
    # and INTO "al Coda with none" when issue #154 let its segno be read: its
    # "D.S. al Coda" now writes <sound dalsegno="segno"/> (the segno half
    # resolves), but its own coda sign is one of the ones outside its
    # staff's bar span, so `codas` is still empty for this file and the
    # "al Coda" half - and so the whole bar - stays unresolved. Same total,
    # same file, a different reason: the count did not move (87 both before
    # and after #154) because this one bar's problem changed shape rather
    # than going away.
    #
    # 43 unanchored: 41 drawn entirely outside their staff's own x span (40
    # coda signs and one "D.S. 2"), and Imprisoned Town's two, whose last
    # system's tab staff comes back as a 12-line anomaly so that system has
    # no bars at all.
    # ISSUE #152 ANSWERS BOTH OF THESE, and answers them by reading the music
    # rather than by describing the loss better. 87 -> 8 and 43 -> 0.
    #
    # 8 bars over 7 files, and every one is now a score that genuinely names
    # a target its page does not draw: Rebel Army Theme, Vamo alla Flamenco,
    # Phantom Train, Hollow, Spoken Without End, Heartgem's Burden (2) and
    # Rito Village - whose Maestro embed this decoder reads no glyphs from,
    # so its "D.S." has no segno to point at whatever else is fixed. The
    # other 79 were all the lost coda system, exactly as the note on
    # _resolve_nav_marks predicted.
    #
    # 0 unanchored. Not "near zero" - the library now holds no navigation
    # mark at all that was read off a page and has no bar to name, because
    # the systems those marks were drawn over are read.
    assert totals["nav_marks_unresolved"] == 8
    assert totals["nav_marks_unanchored"] == 0
    # What all of that costs the score a reader actually sees. On this
    # branch's parent the library reports 263 scores at `structure` high, 29
    # medium and 1 "n/a - nothing found"; reading navigation marks moves 43
    # of the high ones to medium and gives the "n/a" one marks to report, so
    # 221 / 72. It was 188 / 105 while GID 4 was mislabelled - 76 scores
    # downgraded for a D.S. whose segno was on the page all along, which is
    # the figure that makes the mislabel a user-visible defect rather than a
    # tidiness one.
    # And issue #152 moves 34 of the medium ones back to high, because the
    # thing they were medium FOR was a mark that could not be placed on a
    # system this transcription did not hold. The 2 "low" are the two scores
    # that still lose a system: a lost system outranks every other structure
    # term, since the marks that were read may be complete and still describe
    # a form built out of bars the file does not contain.
    assert totals["structure_high"] == 255
    assert totals["structure_medium"] == 36
    assert totals["structure_low"] == 2
    assert totals["structure_n/a"] == 0


# xs:NCName: (Letter | '_') (NCNameChar)* - a bare digit run is not one,
# which is why every id musicxml.py writes is prefixed with "n" (Rule 17).
_NCNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def _assert_no_offenders(scores, label, sample=6):
    """assert-empty for a library-wide list of offending filenames, without
    dumping the whole library at a reader when a defect is systemic. A
    mutation that breaks uniqueness (or NCName validity) across the board -
    see the two mutation tests this is written to survive - named ~279 (and
    ~213) files in one AssertionError before this existed, which buried the
    one thing worth reading (that it IS systemic) under noise nobody was
    going to read."""
    if not scores:
        return
    shown = scores[:sample]
    remainder = len(scores) - len(shown)
    message = f"{label}, {len(scores)} score(s): {shown}"
    if remainder:
        message += f" and {remainder} more"
    raise AssertionError(message)


def test_library_wide_note_ids_are_unique_and_valid_ncnames(library_root):
    """Rule 17, library-wide (issue #150). Every `<note>` this project emits -
    from every PDF the library this profile was developed against holds, not
    a hand-built beats model - carries an id, every id is a legal xs:NCName,
    and no two ids collide within their own document.

    Run across the whole configured library, the same shape as
    test_library_wide_repeat_structure_leaves_conformance_untouched: one
    fixture proves the rule holds in principle, this proves it holds at
    the scale a real library actually reaches.

    Deliberately NOT pinned to an exact score count or an exact total id
    count (PR #156 review): both are library-composition-dependent, and a
    decoder change that reads one more or fewer note anywhere in the library
    - which is any ordinary decoder improvement, not a Rule 17 regression -
    would break an exact pin with no signal that ids are the problem. What
    IS asserted is the identity that actually follows from Rule 17: every
    `<note>` counted here is either a sounding note or a rest, so the total
    must equal `result.notes` - the sounding-note count, computed from the
    beats model BEFORE emission (see tabextract.py's own `notes_total`),
    entirely independent of parsing the id-bearing XML this test reads -
    plus the rests counted straight out of that same XML. If Rule 17 ever
    stopped writing an id on some `<note>`, or wrote one on something that
    is not a `<note>`, ids-vs-elements would still balance and this identity
    would not catch it - that failure mode is what missing_id_scores below
    is for. What this identity catches is scope creep or a miscount in
    EITHER independent count feeding it. (Cross-check, not asserted: on the
    library this was measured against, the identity holds as
    100017 == 98704 sounding + 1313 rests.)
    """
    scores_checked = 0
    identity_mismatches = []
    duplicate_scores = []
    invalid_ncname_scores = []
    missing_id_scores = []
    for pdf in _library_pdfs(library_root):
        try:
            result = tabextract.extract(pdf)
        except Exception:
            continue
        if not result.extractable or not result.musicxml:
            continue
        root = ET.fromstring(result.musicxml)
        notes = root.findall("./part/measure/note")
        if not notes:
            continue
        scores_checked += 1
        ids = [n.get("id") for n in notes]
        sounding = sum(1 for n in notes if n.find("rest") is None)
        if sounding != result.notes:
            identity_mismatches.append((pdf.name, sounding, result.notes))
        if not all(ids):
            missing_id_scores.append(pdf.name)
        if not all(_NCNAME_RE.match(i) for i in ids if i):
            invalid_ncname_scores.append(pdf.name)
        if len(set(ids)) != len(ids):
            duplicate_scores.append(pdf.name)

    _assert_no_offenders(missing_id_scores, "note(s) missing an id")
    _assert_no_offenders(invalid_ncname_scores, "id(s) not a valid NCName")
    _assert_no_offenders(duplicate_scores, "duplicate id(s) within a document")
    assert identity_mismatches == [], (
        f"XML sounding-note count disagrees with result.notes (score, xml_sounding, "
        f"result.notes): {identity_mismatches[:10]}"
        + (f" and {len(identity_mismatches) - 10} more" if len(identity_mismatches) > 10 else ""))
    # A floor rather than a pin (see docstring): which PDFs in the maintainer's
    # library extract cleanly can change, but the check must not have silently
    # stopped running against anything close to the whole library.
    assert scores_checked >= 250, f"only {scores_checked} scores were checked"
