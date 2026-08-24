"""Extraction, exercised against engraved scores that live in the repository.

WHAT THESE ARE FOR. Every test that needed a real engraved PDF used to read
one from the maintainer's own library, which cannot be committed, so 36
tests - all of the extractor's, the transcription API's and the glyph
decoder's - skipped in CI. Reading tablature out of an engraved PDF is the
one thing this project does that nothing else does, and nothing outside a
developer's own machine was checking it. These fixtures are engraved from
MusicXML in the repository (see server/tools/tab_extract/engrave_fixtures.py)
so they can be committed, regenerated, and - because the input says exactly
what was asked for - compared against ground truth rather than against the
extractor's own previous answer.

WHAT THEY CANNOT REACH, stated plainly because a fixture that looks like
coverage and is not is worse than none:

  * The Maestro glyph-ID fingerprint and the Opus PUA name map. Those fonts
    ship with Finale and Sibelius and cannot be committed either, so
    test_glyph_rhythm.py's fingerprint tests and test_tabextract.py's
    Maestro tests still need FERMATA_TEST_LIBRARY. What runs here is the
    third calibration, the SMuFL codepoint map, which is what a free
    engraver draws with.
  * A CFF-flavour embedding of Maestro or Opus, which is one of the reasons
    those two fall back to spacing. It does not arise for a SMuFL font -
    nothing on that path reads outlines at all - so there is nothing here to
    exercise it with.
  * Raster pages beyond a refusal: the rasterised fixture proves extraction
    declines a scan, not that anything reads one.
  * READING a diamond/harmonic notehead. Those codepoints are deliberately
    not in the SMuFL map; harmonics_dense covers the reporting of that gap,
    not its closure.
  * A repeat bracket welded into a phantom staff line. This engraver leaves
    a visible gap in an ending bracket where Finale's abut exactly, so that
    geometry is covered by a synthetic page built here instead - see
    test_abutting_furniture_below_a_staff_is_not_welded_into_a_line - and
    the real examples live only in the maintainer's library.
  * A filled notehead whose stem the vector pass cannot see. This engraver
    draws every stem as a clean vector line, so all twelve fixtures here
    report zero of them, while 493 of the 2657 notation staves in the
    library that supplied glyph durations at all carry at least one. The
    counter and the disclosure for that state (see #115) are exercised
    against a real score in test_tabextract.py and against explicit
    geometry in test_glyph_rhythm.py; nothing here can reach it.
  * Scale. The library's reference score is 50 bars of real two-voice
    fingerstyle writing; the fixture with two voices is eight contrived bars.
    A regression that only shows up in density will still only show up
    there.
"""
import collections
import re
import xml.etree.ElementTree as ET

import fitz
import pytest

from fermata import glyph_rhythm, musicxml, tabextract

from conftest import ENGRAVED_DIR
from test_tabextract import _parse_with_alphatab


# ---------------------------------------------------------------------------
# Reading a transcription back out of what was emitted
# ---------------------------------------------------------------------------

_DUR = re.compile(r"^:(\d+)$")


def emitted_bars(alphatex):
    """[[ (quarters, [(fret, string), ...]), ... ] per voice per bar ].

    Read out of the emitted alphaTex rather than out of an intermediate,
    because the alphaTex is what gets stored and rendered - an assertion
    against a value the emitter never used would pass while the transcription
    was wrong."""
    body = alphatex.split("\n.\n", 1)[1]
    bars = []
    for line in body.strip().splitlines():
        line = re.sub(r"\\ts\s+\d+\s+\d+", "", line).rstrip().rstrip("|")
        voices = []
        for segment in line.split("\\voice"):
            beats = []
            dur, dots, notes = None, 0, []
            for token in segment.split():
                m = _DUR.match(token)
                if m:
                    if dur is not None:
                        beats.append((tabextract._beat_quarters(dur, dots), notes))
                    dur, dots, notes = int(m.group(1)), 0, []
                    continue
                if "{dd}" in token:
                    dots = 2
                elif "{d}" in token:
                    dots = 1
                for fret, string in re.findall(r"(\d+)\.(\d+)", token):
                    notes.append((int(fret), int(string)))
            if dur is not None:
                beats.append((tabextract._beat_quarters(dur, dots), notes))
            voices.append(beats)
        bars.append(voices)
    return bars


def emitted_meters(musicxml):
    """The meter in force in each measure of the emitted MusicXML.

    Read out of the canonical output rather than off the ExtractionResult,
    because a consumer gets the meter of a measure from the file - a score
    reporting one opening meter while its measures declare another is exactly
    the failure this reads for."""
    meters = []
    current = None
    for measure in ET.fromstring(musicxml).findall("./part/measure"):
        time = measure.find("./attributes/time")
        if time is not None:
            current = (int(time.findtext("beats")), int(time.findtext("beat-type")))
        meters.append(current)
    return meters


def source_meters(name):
    """The same thing from the MusicXML the fixture was engraved FROM: the
    meter each measure was asked for, meter by meter, as ground truth. An
    invisible `<time>` counts - it is still the meter the music is in."""
    meters = []
    current = None
    for measure in ET.parse(ENGRAVED_DIR / f"{name}.musicxml").getroot().findall(
            "./part/measure"):
        time = measure.find("./attributes/time")
        if time is not None:
            current = (int(time.findtext("beats")), int(time.findtext("beat-type")))
        meters.append(current)
    return meters


TYPE_QUARTERS = {"whole": 4.0, "half": 2.0, "quarter": 1.0, "eighth": 0.5,
                 "16th": 0.25, "32nd": 0.125}


def source_beats(name):
    """[[quarters, ...] per measure] from the notation staff of the MusicXML
    the fixture was engraved FROM - the ground truth a transcription is
    supposed to recover. Chord members do not start a beat, and the tablature
    staff is skipped because it carries the same notes a second time."""
    root = ET.parse(ENGRAVED_DIR / f"{name}.musicxml").getroot()
    out = []
    for measure in root.findall("./part/measure"):
        beats = []
        for note in measure.findall("note"):
            if note.findtext("staff", "1") != "1":
                continue
            if note.find("chord") is not None:
                continue
            quarters = TYPE_QUARTERS[note.findtext("type")]
            for _dot in note.findall("dot"):
                quarters *= 1.5
            mod = note.find("time-modification")
            if mod is not None:
                quarters = quarters * int(mod.findtext("normal-notes")) / int(
                    mod.findtext("actual-notes"))
            beats.append(quarters)
        out.append(beats)
    return out


# ---------------------------------------------------------------------------
# Notation over tablature: the ordinary case, against ground truth
# ---------------------------------------------------------------------------


def test_an_engraved_score_is_read_from_its_own_glyphs(engraved):
    """The whole point of committing this fixture: a free engraver's output
    reaching the glyph decoder rather than the spacing heuristic. Before the
    SMuFL codepoint map, this exact file decoded with
    rhythm_provenance {'spacing': 1} and "no readable music-font note glyphs
    on the paired notation staff" - every duration a guess from x-positions,
    at low confidence, with no time signature and no key."""
    result = tabextract.extract(engraved("notation_and_tab"))
    assert result.extractable
    assert result.reason is None
    assert result.pages_processed == 2
    assert result.tab_staff_count == 2
    assert result.standard_staff_count == 2
    assert result.rhythm_provenance == {tabextract.PROV_GLYPHS: 2}
    assert "own engraving" in result.confidence["rhythm"]
    assert not any("inferred from horizontal spacing" in w for w in result.warnings)


def test_every_duration_matches_the_score_it_was_engraved_from(engraved):
    """Ground truth, not self-consistency. The extracted duration of every
    beat is compared against the MusicXML the page was engraved from, which
    is the only check that can tell "the bars add up" from "the bars add up
    and the music is right" - three of the five voice-separation defects
    found earlier made the arithmetic look BETTER while making the music
    worse."""
    result = tabextract.extract(engraved("notation_and_tab"))
    extracted = [[q for q, _notes in voices[0]] for voices in emitted_bars(result.alphatex)]
    assert extracted == source_beats("notation_and_tab")


def test_the_frets_read_off_the_page_are_the_ones_engraved(engraved):
    """Fret extraction against a known page. The pitches were written an
    octave up because the part carries an octave-transposing clef, so an E4
    in the source sounds E3 and lands on the 4th string at the 2nd fret -
    which is what these numbers are."""
    result = tabextract.extract(engraved("notation_and_tab"))
    bars = emitted_bars(result.alphatex)
    assert [notes for _q, notes in bars[0][0]] == [[(2, 4)], [(4, 4)], [(0, 3)], [(2, 3)]]
    # the whole-note chord: three noteheads on no stem at all, one beat
    assert bars[3][0] == [(4.0, [(3, 2), (2, 3), (0, 4)])]
    # ...and the second page repeats the first, so nothing was lost or
    # doubled when the pages were accumulated
    assert bars[4:] == bars[:4]


def test_the_time_and_key_signatures_are_decoded_through_an_octave_clef(engraved):
    """This fixture is in D major, and its two engraved sharps are what `2`
    means here. The clef it is engraved with is the octave-transposing one
    every guitar score in this repertoire uses, drawn at its own codepoint -
    a different glyph from a plain treble clef - and the meter and key are
    located by looking rightwards from the clef, so it has to be classified
    as one."""
    result = tabextract.extract(engraved("notation_and_tab"))
    assert result.time_signature == (4, 4)
    assert result.time_signature_source == "glyph-decoded"
    assert result.key_fifths == 2
    assert result.key_signature_source == "glyph-decoded"
    assert "high" in result.confidence["key_signature"]

    # Assert the CATEGORY, not merely that the codepoint was drawn: every
    # glyph on the page carries its codepoint whether the decoder understood
    # it or not, so `0xE052 in codes` would hold just as well with the clef
    # classified as furniture - a guard blind to the one thing that mattered.
    page = fitz.open(engraved("notation_and_tab"))[0]
    classified = {(e.code, e.category) for e in glyph_rhythm.extract_glyph_events(page).events}
    assert (0xE052, "clef") in classified, "gClef8vb, recognised as a clef"
    assert not any(code == 0xE050 for code, _cat in classified), \
        "and no plain treble clef in a guitar part"


def test_a_dotted_note_and_a_beam_survive_into_the_transcription(engraved):
    result = tabextract.extract(engraved("notation_and_tab"))
    assert "{d}" in result.alphatex
    bars = emitted_bars(result.alphatex)
    assert bars[1][0][0][0] == 3.0, "a dotted half is three quarters"
    # four beamed sixteenths, decoded from the beam and not from spacing
    assert [q for q, _n in bars[2][0][:4]] == [0.25] * 4


def test_the_bars_of_a_correct_score_all_add_up(engraved):
    result = tabextract.extract(engraved("notation_and_tab"))
    assert result.bars == 8
    assert result.bars_measured == 8
    assert (result.bars_overfull, result.bars_short, result.bars_defective) == (0, 0, 0)
    for bar in emitted_bars(result.alphatex):
        for voice in bar:
            assert sum(q for q, _n in voice) == 4.0, bar


def test_the_musicxml_holds_the_same_notes_as_the_alphatex(engraved):
    result = tabextract.extract(engraved("notation_and_tab"))
    root = ET.fromstring(result.musicxml)
    assert len(root.findall("./part/measure")) == result.bars
    pitched = [n for n in root.findall("./part/measure/note") if n.find("rest") is None]
    assert len(pitched) == result.notes == 30
    assert root.findtext("./part/measure/attributes/key/fifths") == "2"


def test_the_emitted_alphatex_parses_with_alphatab(engraved):
    """The transcription is stored to be rendered, so it has to parse with the
    importer the player actually uses - not merely look like alphaTex."""
    result = tabextract.extract(engraved("notation_and_tab"))
    parsed = _parse_with_alphatab(result.alphatex)
    assert parsed["bars"] == result.bars
    assert parsed["notes"] == result.notes
    assert parsed["dottedBeats"] > 0


# ---------------------------------------------------------------------------
# A SMuFL font is recognised by what it draws
# ---------------------------------------------------------------------------


def test_a_smufl_font_is_recognised_from_the_codepoints_it_draws(engraved):
    """Not from its name. MuseScore draws with Leland and falls back to
    Bravura for glyphs Leland omits; Dorico uses Bravura throughout. Keying
    on the codepoints means one calibration covers all of them - and it has
    to, because the embedded subset has its glyph names stripped and its
    glyph order minted per file, so neither of the other two keys exists."""
    doc = fitz.open(engraved("notation_and_tab"))
    page = doc[0]
    trace = page.get_texttrace()
    assert glyph_rhythm._smufl_music_fonts(doc, page, trace) == {"Leland"}
    # the text fonts on the same page are not music fonts
    drawn = {s.get("font", "").split("+")[-1] for s in trace}
    assert "Edwin-Roman" in drawn or "FreeSans" in drawn


def _fake_smufl_page(codes, name="Mystery", embedded=True, gid_offset=None):
    """A page drawing `codes` in one font, with control over the two things a
    codepoint cannot vouch for on its own: whether the PDF carries a font
    program at all, and whether the mapping is the synthetic identity."""

    class _Doc:
        def extract_font(self, _xref):
            return b"a font program" if embedded else b""

    class _Page:
        parent = _Doc()

        def get_fonts(self, full=False):
            return [(1, "ttf" if embedded else "n/a", "Type0", name, "F1", "Identity-H", 0)]

        def get_texttrace(self):
            chars = []
            for i, code in enumerate(codes):
                gid = code - 0xE000 if gid_offset == "identity" else i + 1
                chars.append((code, gid, (0.0, 0.0), (float(i), 0.0, float(i) + 1, 1.0)))
            return [{"font": name, "chars": chars}]

    page = _Page()
    return page.parent, page, page.get_texttrace()


def test_a_codepoint_is_a_claim_the_producer_wrote_not_a_credential():
    """The hole an earlier version of this left open, and the test that used
    to pin it open by asserting a font named "Mystery" drawing four copies of
    one codepoint WAS a music font.

    A SMuFL codepoint reaches us through the PDF's own ToUnicode CMap. It is
    what the producer said, so on its own it cannot be the whole basis for
    reading a page as engraved music - a page whose "music font" was an
    unembedded text font drawing the letters A-F decoded as a staff at high
    confidence. Each requirement below is aimed at a specific way that goes
    wrong; see _smufl_music_fonts."""
    noteheads = [0xE0A4] * 6

    # the honest case: embedded, real mapping, noteheads among the glyphs
    assert glyph_rhythm._smufl_music_fonts(*_fake_smufl_page(noteheads)) == {"Mystery"}

    # 1. no font program in the PDF - a reader-supplied substitute cannot be
    #    the font whose glyphs were calibrated
    assert glyph_rhythm._smufl_music_fonts(
        *_fake_smufl_page(noteheads, embedded=False)) == set()

    # 2. the synthetic identity mapping a producer emits when it had no cmap
    #    to read: `U+E000 + glyph id` lands on this table's keys by arithmetic
    assert glyph_rhythm._smufl_music_fonts(
        *_fake_smufl_page(noteheads, gid_offset="identity")) == set()

    # 3. no noteheads: a notation staff without them is not one, and there
    #    would be nothing here to decode. Ordinary ASCII under the identity
    #    mapping above lands only on clefs, never on a notehead.
    clefs_and_meters = [0xE050, 0xE052, 0xE062, 0xE084, 0xE084, 0xE08A]
    assert all(glyph_rhythm.smufl_unknown_kind(c) != "notehead" for c in clefs_and_meters)
    assert glyph_rhythm._smufl_music_fonts(*_fake_smufl_page(clefs_and_meters)) == set()

    # and it still takes several recognised codepoints, not one or two
    assert glyph_rhythm._smufl_music_fonts(*_fake_smufl_page([0xE0A4] * 3)) == set()


def test_a_font_using_the_private_use_area_for_something_else_is_not_music():
    """Landing in SMuFL's range is not enough - the codepoints have to BE
    calibrated music symbols. Sibelius's Opus draws at U+F0xx and an icon
    font can draw anywhere in the private use area; either would be read as
    an engraved staff if presence in the range were the test."""
    assert glyph_rhythm._smufl_music_fonts(
        *_fake_smufl_page([0xF0CF] * 40, name="Opus")) == set()


def test_a_page_whose_music_font_is_a_text_font_is_refused(engraved):
    """The same thing again as a whole PDF rather than a stubbed page: a
    committed fixture whose only music credential is a ToUnicode CMap saying
    that the letters A-H are noteheads, a clef, a meter digit and a dot.

    Verified to decode as rhythm-from-glyphs before the corroboration
    requirements went in - `rhythm_provenance` came back {'glyphs': 1} for
    this exact file - and to be refused by the version this branch started
    from. It has to keep being refused."""
    result = tabextract.extract(engraved("fake_music_font"))
    assert result.rhythm_provenance == {tabextract.PROV_SPACING: 1}
    assert result.confidence["rhythm"].startswith("low")
    assert result.time_signature_source.startswith("not detected")
    assert result.key_signature_source.startswith("not detected")
    assert any("no readable music-font note glyphs" in w for w in result.warnings)

    doc = fitz.open(engraved("fake_music_font"))
    page = doc[0]
    trace = page.get_texttrace()
    assert glyph_rhythm._smufl_music_fonts(doc, page, trace) == set()
    # the claim really is in the file - this fixture would be pointless if the
    # codepoints it advertises were not there to be believed
    claimed = {c[0] for span in trace for c in span["chars"]}
    assert claimed & set(glyph_rhythm.SMUFL_CODE_MAP), claimed


def test_a_music_font_may_also_draw_text_and_that_text_is_not_a_glyph_event():
    """A music font can carry plain characters. Counting them as music
    symbols would report a healthy page as mostly unrecognised vocabulary and
    take a perfectly good decode to the spacing fallback; counting them as
    UNKNOWN music symbols would be worse, because the unknown ratio is what
    decides that. So codepoints outside SMuFL's range are ignored outright,
    while an unrecognised codepoint INSIDE it is kept and reported - including
    one from the optional block above U+F3FF, which used to be dropped
    silently, leaving a note at its base duration with a clean report."""
    glyph_rhythm.clear_caches()
    _doc, page, _trace = _fake_smufl_page([
        0xE0A4, 0xE0A4, 0xE0A4, 0xE0A4,   # enough noteheads to qualify the font
        ord("A"), ord("3"),               # ...plus text, which is not a symbol
        0xE0D9,                           # ...a diamond notehead, deliberately uncalibrated
        0xF500,                           # ...and something from SMuFL's optional block
    ], name="Leland")
    glyphs = glyph_rhythm.extract_glyph_events(page)
    assert [e.code for e in glyphs.events] == [0xE0A4] * 4 + [0xE0D9, 0xF500]
    assert [e.calibration_key for e in glyphs.unknown] == ["U+E0D9", "U+F500"]
    assert all(e.smufl for e in glyphs.events)


def test_the_engraved_vocabulary_is_fully_calibrated(engraved):
    """The decode's own honesty metric, on a real page. An unrecognised glyph
    sitting where a flag attaches decodes as a systematically wrong duration
    while every other signal looks healthy, so "zero unknown" is the claim
    worth pinning - and if a future engraver draws something new, this is
    where it surfaces instead of in a silently wrong rhythm."""
    doc = fitz.open(engraved("notation_and_tab"))
    for page in doc:
        staves, _anomalies = tabextract._detect_staves(page)
        standard = [s for s in staves if s.kind == "standard"]
        assert standard
        for staff in standard:
            _notes, stats = glyph_rhythm.decode_note_events(
                page, staff.top, staff.bottom, staff.x0, staff.x1,
                staff.line_ys, staff.spacing)
            assert stats["unknown_glyphs"] == 0, stats["unknown_gid_or_name_sample"]
            assert stats["unknown_at_flag_position"] == 0
            assert stats["stem_count"] > 0, "stems are vector strokes, not glyphs"
            assert stats["note_events"] > 0


# ---------------------------------------------------------------------------
# Staff lines broken at every barline
# ---------------------------------------------------------------------------


def _lines_pdf(path, rows, width=612.0, height=792.0, digits=()):
    """A page of horizontal rules - {y: [(x0, x1), ...]} - and optionally some
    fret digits, for the cases that have to get as far as a transcription."""
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    for y, spans in rows.items():
        for x0, x1 in spans:
            page.draw_line((x0, y), (x1, y), width=0.5)
    for text, x, y in digits:
        page.insert_text((x, y), text, fontsize=7, fontname="helv")
    doc.save(path)
    doc.close()
    return path


def test_abutting_furniture_below_a_staff_is_not_welded_into_a_line(tmp_path):
    """The regression that joining collinear pieces caused, in its exact
    geometry - which no engraved fixture here can reproduce, because
    MuseScore leaves a visible gap in a repeat bracket while Finale does not.

    Finale draws a "1." / "2." repeat bracket below the tab staff as two
    strokes meeting at a barline with a measured gap of 0.0000pt - the same
    gap as between the pieces of a broken staff line, so no tolerance can
    separate them. Welded, the bracket cleared the length floor and landed
    inside the cluster gap below the staff, making a 6-line group into a
    7-line group that was discarded whole. Six library scores lost 33 bars
    and 264 notes between them, and the loss IMPROVED their bar-conformance
    figures, because the bars that disappeared were the ones that did not
    add up.

    What rejects it is not the gap but the company it keeps: a run assembled
    from more than one piece has to have siblings at its own extent, and a
    bracket has none."""
    staff = {180.0 + i * 7.4: [(60.0, 300.0), (300.0, 560.0)] for i in range(6)}
    bracket_y = 180.0 + 5 * 7.4 + 10.0     # inside the 15pt cluster gap
    rows = dict(staff)
    rows[bracket_y] = [(375.0, 478.8), (478.8, 572.9)]   # abutting, 0.0pt gap
    pdf = _lines_pdf(tmp_path / "volta_weld.pdf", rows)

    page = fitz.open(pdf)[0]
    kept = tabextract._long_horizontal_segments(page)
    assert len(kept) == 6, kept
    assert all(abs(x1 - x0 - 500.0) < 1.0 for _y, x0, x1 in kept), kept
    assert not any(abs(y - bracket_y) < 0.05 for y, _x0, _x1 in kept), (
        "the repeat bracket was welded into a staff line")

    staves, anomalies = tabextract._detect_staves(page)
    assert [s.kind for s in staves] == ["tab"]
    assert anomalies == [], "and the tab staff was not discarded as a 7-line group"


def test_the_evidence_a_joined_run_needs_is_four_siblings_at_its_own_extent(tmp_path):
    """The two numbers the containment rests on, each measured rather than
    merely present - a constant no test can feel is a constant nobody can
    change safely.

    Both hostile rows here are drawn in abutting pieces at the same 0.0pt gap
    as a broken staff line, so nothing about the join itself separates them:
    one row has a single companion at its own extent (a bracket with a
    second stroke, or a box), and one spans nearly the same width as the
    staff. Neither is a staff line, and it takes both the count and the
    tightness of the extent match to say so."""
    rows = {180.0 + i * 7.4: [(60.0, 300.0), (300.0, 560.0)] for i in range(6)}
    # a pair of furniture rows sharing one extent: one sibling each, not four
    for y in (180.0 + 5 * 7.4 + 8.0, 180.0 + 5 * 7.4 + 12.0):
        rows[y] = [(375.0, 478.8), (478.8, 572.9)]
    # and one nearly as wide as the staff, but not within the match tolerance
    rows[180.0 - 10.0] = [(100.0, 300.0), (300.0, 520.0)]
    pdf = _lines_pdf(tmp_path / "near_miss.pdf", rows)

    page = fitz.open(pdf)[0]
    kept = tabextract._long_horizontal_segments(page)
    assert len(kept) == 6, kept
    assert all((round(x0), round(x1)) == (60, 560) for _y, x0, x1 in kept), kept
    staves, anomalies = tabextract._detect_staves(page)
    assert [s.kind for s in staves] == ["tab"], "the staff survived all three"
    assert anomalies == []


def test_a_discarded_staff_sized_group_says_music_is_missing(tmp_path):
    """Whatever caused it, throwing away a group of staff lines has to be
    LOUD - because the loss makes the score look better, not worse.

    A discarded group used to be reported as "1 staff-line group(s) with an
    unexpected line count were ignored" with every confidence still high,
    while a whole system's bars and notes were absent from the
    transcription. Worse, the bars that vanish are as likely as any to be
    the ones that did not add up, so the defective-bar count IMPROVES when
    music disappears. A number that gets better when notes go missing is
    worse than no number at all."""
    rows = {180.0 + i * 7.4: [(60.0, 560.0)] for i in range(6)}
    rows[180.0 + 6 * 7.4] = [(60.0, 560.0)]   # a seventh line: not a staff any more
    rows[400.0] = [(60.0, 560.0)]             # ...and a lone rule elsewhere
    for i in range(6):
        rows[500.0 + i * 7.4] = [(60.0, 560.0)]   # a real tab staff, so there IS a result
    pdf = _lines_pdf(tmp_path / "discarded.pdf", rows,
                     digits=[("3", 100.0, 505.0), ("5", 200.0, 512.4)])

    _staves, anomalies = tabextract._detect_staves(fitz.open(pdf)[0])
    assert sorted(a["line_count"] for a in anomalies) == [1, 7]

    result = tabextract.extract(pdf)
    missing = [w for w in result.warnings if "MISSING from this transcription" in w]
    assert len(missing) == 1, result.warnings
    assert "1 of the ignored group(s) had at least 5 lines" in missing[0]
    assert any("line counts: [1, 7]" in w for w in result.warnings)
    assert result.confidence["frets"].startswith("medium"), (
        "a whole system's digits may be absent, which is a claim about the frets")

    # the same page without the stray groups keeps the plain high claim
    clean = _lines_pdf(tmp_path / "clean.pdf",
                       {500.0 + i * 7.4: [(60.0, 560.0)] for i in range(6)},
                       digits=[("3", 100.0, 505.0), ("5", 200.0, 512.4)])
    clean_result = tabextract.extract(clean)
    assert clean_result.extractable
    assert clean_result.confidence["frets"].startswith("high")
    assert not any("ignored" in w for w in clean_result.warnings)


def test_a_page_cropped_to_its_content_keeps_its_staff_lines(tmp_path):
    """A staff line as wide as the page is still a staff line. Refusing one
    for being too long is a claim about engraving, not about PDFs: anything
    cropped to its content - pdfcrop, a tablet reader's trim-margins,
    borderless print - has staff lines running the full width, and throwing
    them away refused a perfectly readable tab score while reporting that it
    held no tablature at all.

    What is page furniture is a rule drawn ON the page boundary, which is a
    test of position."""
    rows = {180.0 + i * 7.4: [(0.0, 300.0), (300.0, 612.0)] for i in range(6)}
    rows[0.0] = [(0.0, 612.0)]        # the page's own top edge
    rows[792.0] = [(0.0, 612.0)]      # ...and its bottom
    pdf = _lines_pdf(tmp_path / "cropped.pdf", rows)

    page = fitz.open(pdf)[0]
    kept = tabextract._long_horizontal_segments(page)
    assert len(kept) == 6, kept
    assert all(abs(x0) < 0.01 and abs(x1 - 612.0) < 0.01 for _y, x0, x1 in kept), kept

    staves, anomalies = tabextract._detect_staves(page)
    assert [s.kind for s in staves] == ["tab"]
    assert anomalies == [], "the page-edge rules are not staff groups"


def test_a_staff_line_drawn_in_pieces_is_still_one_staff(engraved):
    """Whether a staff line arrives as one primitive per system is the
    exporter's choice: Finale and Sibelius draw one line across the system,
    MuseScore draws a separate abutting piece per measure. Before the pieces
    were joined, a system whose bars were each under a quarter of the page
    wide was invisible - detection depended on a score happening to have
    wide bars, so a tab staff could be found on one system of a page and not
    the next."""
    page = fitz.open(engraved("tab_only"))[0]
    pieces = 0
    for drawing in glyph_rhythm.page_drawings(page):
        for item in drawing.get("items", []):
            if item[0] == "l" and abs(item[1].y - item[2].y) < 0.08:
                pieces += 1
    joined = tabextract._long_horizontal_segments(page)
    assert pieces > len(joined) * 2, (
        f"{pieces} horizontal strokes joined into {len(joined)} lines - this fixture "
        "is supposed to be drawn a measure at a time")

    staves, anomalies = tabextract._detect_staves(page)
    assert [s.kind for s in staves] == ["tab", "tab"]
    assert anomalies == [], "the page-edge rules are not staff groups"
    for staff in staves:
        assert staff.x1 - staff.x0 > page.rect.width * 0.5


# ---------------------------------------------------------------------------
# Every rest value and every flag hook
# ---------------------------------------------------------------------------


def test_every_rest_value_is_read_from_the_glyph_that_spells_it(engraved):
    """A SMuFL font gives the half and the whole rest separate codepoints,
    unlike Maestro and Opus, which draw both with one glyph and leave the
    reader to tell them apart by which staff line it hangs from. Where the
    engraving says the value outright there is no reason to guess at it - and
    a rest read at the wrong value silently shifts everything after it in the
    bar."""
    result = tabextract.extract(engraved("rests_and_flags"))
    assert result.extractable
    rests = [(q, notes) for bar in emitted_bars(result.alphatex)
             for voice in bar for q, notes in voice if not notes]
    assert sorted({q for q, _n in rests}) == [0.125, 0.25, 0.5, 1.0, 2.0, 4.0], rests


def test_the_rest_the_engraving_names_is_the_rest_its_position_says(engraved):
    """The positional rule, checked against the one population where the
    answer is known WITHOUT it.

    Maestro and Opus draw the half and the whole rest with a single glyph, so
    for those fonts geometry is the only discriminator there is - for a
    twofold difference in duration. A SMuFL font names the value in the
    codepoint, so this fixture's rests are ground truth for the rule: apply
    the geometry to them and it has to reach the same answer the engraving
    already stated. Measured over the library the two disagree on 4 of the 19
    rests where the truth is knowable, and every one of those disagreements is
    the geometry being wrong.
    """
    doc = fitz.open(engraved("rests_and_flags"))
    named = {"rest_whole": 4.0, "rest_half": 2.0}
    checked = collections.Counter()
    try:
        for page in doc:
            staves, _anomalies = tabextract._detect_staves(page)
            standard = [s for s in staves if s.kind == "standard"]
            glyphs = glyph_rhythm.extract_glyph_events(page)
            for staff in standard:
                pad = (staff.bottom - staff.top) * 1.6
                for ev in glyphs.events:
                    if ev.category not in named:
                        continue
                    if not staff.top - pad <= ev.yc <= staff.bottom + pad:
                        continue
                    assert ev.ink_measured, "the rule needs the ink, not the metrics box"
                    base, decided = glyph_rhythm.half_or_whole_rest(
                        ev.yc, staff.line_ys, staff.spacing)
                    assert decided, (ev.category, ev.yc, staff.line_ys)
                    assert base == named[ev.category], (
                        f"the engraving says {ev.category} ({named[ev.category]} quarters), "
                        f"its position on the staff says {base}")
                    checked[ev.category] += 1
    finally:
        doc.close()
    # ...and both readings really were exercised, in both directions
    assert checked == {"rest_whole": 1, "rest_half": 1}, checked


def test_a_flagged_thirty_second_is_not_read_as_a_quarter(engraved):
    """A filled notehead is a quarter until something shortens it, and for an
    unbeamed note the only thing that can is the flag glyph at the free end
    of its stem. An uncalibrated flag leaves the note at its base value while
    every other signal still looks healthy - which is what the decoder's
    unknown-at-flag-position count exists to catch, and what this measures
    from the other side."""
    result = tabextract.extract(engraved("rests_and_flags"))
    bars = emitted_bars(result.alphatex)
    thirty_seconds = bars[4][0][:8]
    assert [q for q, _n in thirty_seconds] == [0.125] * 8
    assert sum(q for q, _n in bars[4][0]) == 4.0
    # the flagged sixteenths in bar 2, likewise unbeamed
    assert [q for q, _n in bars[1][0]] == [0.5, 0.5, 0.25, 0.25, 0.25, 0.25, 2.0]


def test_the_rest_fixture_matches_the_score_it_was_engraved_from(engraved):
    result = tabextract.extract(engraved("rests_and_flags"))
    extracted = [[q for q, _notes in voices[0]] for voices in emitted_bars(result.alphatex)]
    assert extracted == source_beats("rests_and_flags")
    assert (result.bars_overfull, result.bars_short, result.bars_defective) == (0, 0, 0)


def test_engraved_rests_are_never_reported_as_inferred_silence(engraved):
    """The negative control for Rule 14. This fixture is fourteen printed rests
    across every value the vocabulary spells, all read from the glyph that
    spells them - so nothing here was deduced from the meter, and nothing may
    say it was. A marker that fired on engraved silence would put a `<forward>`
    where the engraver wrote a rest and under-report the bar, which is the same
    class of lie in the other direction."""
    result = tabextract.extract(engraved("rests_and_flags"))
    rests = [(q, notes) for bar in emitted_bars(result.alphatex)
             for voice in bar for q, notes in voice if not notes]
    assert len(rests) == 14, rests
    assert result.bars_padded == 0
    assert result.padded_bars == []
    assert result.inferred_rest_quarters == 0.0
    assert result.bars_unread == 0
    assert "<forward>" not in result.musicxml
    assert not any("deduced from the time signature" in w for w in result.warnings)
    # every rest in the file is a real rest element, and the beat count is the
    # same either way because nothing was inferred
    root = ET.fromstring(result.musicxml)
    written = [n for n in root.findall("./part/measure/note")
               if n.find("rest") is not None]
    assert len(written) == 14
    assert result.confidence["rhythm"].startswith("high")
    assert "bar(s)" not in result.confidence["rhythm"], "nothing to qualify it with"


def test_a_repeat_with_ending_brackets_leaves_its_staves_alone(engraved):
    """An engraved repeat with "1." / "2." brackets, which is what the
    library files that regressed actually contain. This engraver leaves a
    visible gap in the bracket, so it does not reproduce the weld itself
    (see test_abutting_furniture_below_a_staff_is_not_welded_into_a_line for
    that geometry) - what it does check is that a bracket sitting close under
    a staff disturbs neither the staff nor the group it belongs to."""
    pdf = engraved("volta")
    doc = fitz.open(pdf)
    for page in doc:
        staves, anomalies = tabextract._detect_staves(page)
        assert [s.kind for s in staves] == ["standard", "tab", "standard", "tab"]
        assert anomalies == [], "no group was discarded"

    result = tabextract.extract(pdf)
    assert result.tab_staff_count == 2
    assert result.rhythm_provenance == {tabextract.PROV_GLYPHS: 2}
    # The repeat's thick-thin barline is read as two barlines, so an empty bar
    # appears between the two halves. That is an artifact and it is pinned as
    # one rather than left to be discovered: nine bars for eight written, the
    # extra one a whole rest.
    bars = emitted_bars(result.alphatex)
    assert len(bars) == 9
    assert bars[4][0] == [(4.0, [])], bars[4]
    assert [len(v[0]) for v in bars] == [4, 4, 4, 4, 1, 4, 4, 4, 4]
    # ...and that phantom bar is reported as a bar nothing was read from, which
    # is the only signal it leaves: its whole rest adds up to the meter, so
    # Rule 8 passes and the file cannot distinguish it from an engraved silence.
    # Nine bars for eight written is exactly the kind of thing a reader has to
    # be told about by number rather than left to notice.
    assert result.bars_unread == 1
    assert result.unread_bars == [5]
    assert (result.bars_overfull, result.bars_short, result.bars_defective) == (0, 0, 0)
    assert "<forward>" not in result.musicxml
    unread = next(w for w in result.warnings if "hold nothing that was read" in w)
    assert "1 of 9 bar(s)" in unread and "The bars are: 5." in unread
    # one bar in nine is under the downgrade threshold, and the confidence still
    # says so rather than reading as an unqualified high
    assert result.confidence["rhythm"].startswith("high")
    assert "hold nothing that was read from the score (1)" in result.confidence["rhythm"]


# ---------------------------------------------------------------------------
# An unrecognised notehead, at a density no ratio can see
# ---------------------------------------------------------------------------


def test_an_unrecognised_notehead_is_reported_however_few_there_are(engraved):
    """The honesty gate that a ratio could not be. Diamond noteheads -
    harmonics - are deliberately not calibrated, and on a sparse system two
    of them are a fifth of the glyphs and degrade confidence on the ratio
    alone. On a dense two-voice system the same two are three percent, and
    before this they reported NOTHING: no warning, no defective bar,
    confidence "high", while the voice above them lost beats to an invented
    rest and their tab digits were attached to the voice below.

    So this fixture is dense on purpose, and the assertion that matters is
    that the ratio is *under* the threshold while the decode is degraded
    anyway - density is not evidence about a notehead."""
    result = tabextract.extract(engraved("harmonics_dense"))
    assert result.extractable
    # the system carrying the two harmonics is degraded; the other is not
    assert result.rhythm_provenance == {tabextract.PROV_GLYPHS_DEGRADED: 1,
                                        tabextract.PROV_GLYPHS: 1}
    assert not result.confidence["rhythm"].startswith("high")
    assert any("not been calibrated" in w for w in result.warnings)
    assert any("U+E0DB" in w for w in result.warnings), (
        "the unrecognised codepoint is named, so it can be calibrated later")
    # No note is lost here - all 96 are read, the two uncalibrated noteheads
    # included, with their durations inferred from what their bar had left over.
    # What bar 1 loses is a BEAT: one digit token beside the staff could not be
    # assigned to a string, so its voice holds seven eighths where the meter
    # wants eight. That bar used to report as adding up, because the voice was
    # padded back to its meter with invented silence before the arithmetic was
    # checked, which left the honesty gate above as the only thing on the page
    # saying a word about any of it. It is no longer alone: the padding is
    # marked, excluded from the sums, and the bar is reported short by exactly
    # the eighth that went missing.
    assert result.notes == 96, "nothing was dropped - this is a missing beat, not a note"
    assert (result.bars_overfull, result.bars_short, result.bars_defective) == (0, 1, 1)
    assert result.bars_padded == 1
    assert result.padded_bars == [1]
    assert result.inferred_rest_quarters == 0.5, "one eighth note of it"
    assert any("deduced from the time signature" in w for w in result.warnings)
    # and the confidence string states the defect even though one bar in eight
    # is under the downgrade threshold
    assert "1 of 8 bar(s) do not add up" in result.confidence["rhythm"]

    doc = fitz.open(engraved("harmonics_dense"))
    degraded = 0
    for page in doc:
        for staff in tabextract._detect_staves(page)[0]:
            if staff.kind != "standard":
                continue
            _notes, stats = glyph_rhythm.decode_note_events(
                page, staff.top, staff.bottom, staff.x0, staff.x1,
                staff.line_ys, staff.spacing)
            if not stats["unknown_noteheads"]:
                continue
            degraded += 1
            assert stats["unknown_ratio"] < tabextract._UNKNOWN_RATIO_WARN, (
                "if the ratio could see this, the gate would not be needed")
            assert stats["unknown_at_flag_position"] == 0
    assert degraded == 1


def test_expression_marks_are_not_read_as_incomprehension():
    """The same ratio failed in the other direction too. An accent, a
    fermata, a dynamic or a repeat dot says nothing about a note's duration,
    and a score was being downgraded to medium over two repeat dots while
    two unrecognised harmonics on a dense system triggered nothing. What the
    decoder could not read has to be separated from what it needed."""
    assert glyph_rhythm.smufl_unknown_kind(0xE0DB) == "notehead"
    assert glyph_rhythm.smufl_unknown_kind(0xE244) == "duration"       # a flag
    assert glyph_rhythm.smufl_unknown_kind(0xE4E9) == "duration"       # a rest
    assert glyph_rhythm.smufl_unknown_kind(0xE4A0) == "furniture"      # accent
    assert glyph_rhythm.smufl_unknown_kind(0xE4C0) == "furniture"      # fermata
    assert glyph_rhythm.smufl_unknown_kind(0xE520) == "furniture"      # dynamic
    assert glyph_rhythm.smufl_unknown_kind(0xE044) == "furniture"      # repeat dots
    # a codepoint in a block this decoder has no opinion about counts as
    # duration-bearing, which is the fail-safe direction
    assert glyph_rhythm.smufl_unknown_kind(0xF500) == "duration"


def test_furniture_alone_does_not_degrade_a_clean_decode():
    glyph_rhythm.clear_caches()
    _doc, page, _trace = _fake_smufl_page(
        [0xE0A4] * 8 + [0xE4A0, 0xE4C0, 0xE044], name="Leland")
    glyphs = glyph_rhythm.extract_glyph_events(page)
    assert len(glyphs.unknown) == 3, "they are still seen"
    kinds = {glyph_rhythm.smufl_unknown_kind(e.code) for e in glyphs.unknown}
    assert kinds == {"furniture"}


def test_a_second_font_supplying_one_glyph_is_still_accounted_for():
    """An engraver falls back to another font for symbols its main one lacks,
    and one library page draws its single harmonic notehead that way -
    Bravura, one glyph, on a Leland page. Excluding that font for drawing too
    little to qualify on its own meant the notehead was neither read nor
    reported: it simply was not there, and the honesty stats were spotless."""
    glyph_rhythm.clear_caches()

    class _Doc:
        def extract_font(self, _xref):
            return b"a font program"

    class _Page:
        parent = _Doc()

        def get_fonts(self, full=False):
            return [(1, "ttf", "Type0", "Leland", "F1", "Identity-H", 0),
                    (2, "ttf", "Type0", "Bravura", "F2", "Identity-H", 0)]

        def get_texttrace(self):
            return [
                {"font": "Leland",
                 "chars": [(0xE0A4, i + 1, (0.0, 0.0), (float(i), 0.0, float(i) + 1, 1.0))
                           for i in range(8)]},
                # one glyph only, from the fallback font: a real harmonic
                {"font": "Bravura", "chars": [(0xE0E2, 1, (0.0, 0.0), (9.0, 0.0, 10.0, 1.0))]},
            ]

    glyphs = glyph_rhythm.extract_glyph_events(_Page())
    assert [e.code for e in glyphs.unknown] == [0xE0E2]
    assert glyph_rhythm.smufl_unknown_kind(0xE0E2) == "notehead"


# ---------------------------------------------------------------------------
# Tablature with no notation staff beside it
# ---------------------------------------------------------------------------


def test_tablature_alone_falls_back_to_spacing_and_says_which(engraved):
    """There is no rhythm in a stemless tab staff. The honest fallback - a
    duration guessed from horizontal spacing, at low confidence, with the
    reason named - had no CI coverage at all, and it is the branch that a
    tab-only edition takes."""
    result = tabextract.extract(engraved("tab_only"))
    assert result.extractable
    assert result.tab_staff_count == 2
    assert result.standard_staff_count == 0
    assert result.bars == 12
    assert result.notes == 48
    assert result.rhythm_provenance == {tabextract.PROV_SPACING: 2}
    assert result.confidence["rhythm"].startswith("low")
    assert any("own system" in w for w in result.warnings)
    assert any("inferred from horizontal spacing" in w for w in result.warnings)
    assert result.time_signature_source.startswith("not detected")
    # WHICH bars those are, as data - one entry per emitted bar, so a consumer
    # can mark them without parsing the prose. The staff count in
    # rhythm_provenance says how much of the score came from spacing; only this
    # says which of it, and the two have to agree.
    assert result.spacing_bars == list(range(1, 13))
    assert result.degraded_bars == []


def test_a_degraded_system_names_the_bars_it_produced(engraved):
    """The same thing for a staff that WAS read from the engraving with
    something on it left unread. Four bars of this fixture come off the staff
    carrying two uncalibrated harmonics, and until these were collected a
    reader was told that some fraction of the score was in question with no way
    to find out which fraction."""
    result = tabextract.extract(engraved("harmonics_dense"))
    assert result.rhythm_provenance == {tabextract.PROV_GLYPHS_DEGRADED: 1,
                                        tabextract.PROV_GLYPHS: 1}
    assert result.degraded_bars == [1, 2, 3, 4]
    assert result.spacing_bars == []
    named = next(w for w in result.warnings
                 if "1 staff system(s) were read from the engraved" in w)
    assert "The bars they produced are: 1, 2, 3, 4." in named


def test_a_final_system_too_short_to_detect_loses_its_bars(engraved):
    """A known limitation, engraved so that it is a tripwire rather than a
    sentence in a README.

    A staff is found by the length of its lines, and an engraver does not
    stretch a final system that is only part full - so a two-bar last system
    has lines under the length floor and is not detected at all. This fixture
    writes eight bars and six are read. The silence is the part worth
    knowing: `tab_only` is twelve bars precisely to avoid this, and if that
    had been the only fixture nothing here would say it happens."""
    assert len(source_beats("tab_only_short_last_system")) == 8
    result = tabextract.extract(engraved("tab_only_short_last_system"))
    assert result.extractable
    assert result.bars == 6, "six of the eight bars engraved"
    assert result.tab_staff_count == 1, "the second system was not found at all"
    assert not any("were not detected" in w for w in result.warnings), (
        "and nothing reports the two missing bars - which is the defect")


# ---------------------------------------------------------------------------
# Two voices in one bar
# ---------------------------------------------------------------------------


def test_two_voices_in_one_bar_are_separated_by_their_stems(engraved):
    """A melody in quarters with its stems up over an accompaniment in
    eighths with its stems down. Flattened into one voice each bar holds 8
    quarters against a 4/4 meter; separated, each voice holds exactly 4. This
    is the metric voice separation exists to move."""
    result = tabextract.extract(engraved("two_voices"))
    assert result.extractable
    assert result.bars == 8
    assert result.notes == 96, "4 quarters + 8 eighths, eight times over"
    assert (result.bars_overfull, result.bars_short, result.bars_defective) == (0, 0, 0)
    assert "\\voicemode barwise" in result.alphatex

    bars = emitted_bars(result.alphatex)
    assert all(len(voices) == 2 for voices in bars), bars
    for upper, lower in bars:
        assert [q for q, _n in upper] == [1.0] * 4
        assert [q for q, _n in lower] == [0.5] * 8
    assert any("concurrent voices" in w for w in result.warnings)


def test_the_second_voice_really_sounds_at_the_same_time(engraved):
    """`\\voice` only means concurrent voices with `\\voicemode barwise`; the
    default reading is "restart the staff for the next voice", which parses
    just as happily and plays the bass line after the melody instead of under
    it. More sounding voices than bars is what says it landed."""
    result = tabextract.extract(engraved("two_voices"))
    parsed = _parse_with_alphatab(result.alphatex)
    assert parsed["voices"] > parsed["bars"], parsed


# ---------------------------------------------------------------------------
# Tuplets and ties: known gaps, pinned as gaps
# ---------------------------------------------------------------------------


def test_a_triplet_is_not_shortened_and_its_bar_is_reported_overfull(engraved):
    """Tuplets are not detected. That is a documented gap, and what must not
    happen is for it to be a SILENT one: an eighth-note triplet decoded at
    its plain written value overfills its bar by half a quarter, and the
    score has to say the bar does not add up. The blanket "tuplets are not
    detected" caveat appears on every score whether or not it has one, so it
    is not evidence - the bar count is."""
    result = tabextract.extract(engraved("tuplet_and_tie"))
    assert result.extractable
    assert result.bars == 8
    assert result.bars_overfull == 2, "the two triplet bars, and only those"
    assert result.bars_short == 0
    assert result.bars_defective == 2
    assert any("hold more than their time signature" in w for w in result.warnings)

    bars = emitted_bars(result.alphatex)
    triplet_bar = bars[0][0]
    assert [q for q, _n in triplet_bar] == [0.5, 0.5, 0.5, 1.0, 2.0]
    assert sum(q for q, _n in triplet_bar) == 4.5, "half a quarter over the meter"
    # the bars that do add up still do
    assert sum(q for q, _n in bars[3][0]) == 4.0


def test_a_tie_across_a_barline_is_seen_by_the_decoder(engraved):
    """The tie is found in the engraving - it is a vector curve, not a glyph -
    even though nothing downstream spends it yet, which is what the "tie
    detection is low confidence" caveat is about. Pinning the decode keeps
    the curve matching alive while the emitter catches up.

    This score engraves the same four-bar phrase twice, so it has two ties,
    and only ONE of them is matched: the second falls at a system break,
    where the tie is drawn as two partial curves with the notes it joins on
    different staves. That is a real limitation and it is asserted as one
    rather than papered over - the partial curve at each side of the break is
    counted here so a change that starts matching it will show up as this
    test failing, not as a number nobody looks at."""
    doc = fitz.open(engraved("tuplet_and_tie"))
    per_staff = []
    for page in doc:
        for staff in tabextract._detect_staves(page)[0]:
            if staff.kind != "standard":
                continue
            notes, stats = glyph_rhythm.decode_note_events(
                page, staff.top, staff.bottom, staff.x0, staff.x1,
                staff.line_ys, staff.spacing)
            per_staff.append((sum(1 for n in notes if n.tied_next), stats["curve_count"]))

    assert len(per_staff) == 2, "two systems"
    assert per_staff[0][0] == 1, "the tie inside the first system is matched"
    assert per_staff[1][0] == 0, "the one split across the break is not"
    assert all(curves > 0 for _tied, curves in per_staff), (
        "the split tie's partial curves are still found on both staves")

    result = tabextract.extract(engraved("tuplet_and_tie"))
    bars = emitted_bars(result.alphatex)
    for index in (1, 2, 5, 6):
        assert sum(q for q, _n in bars[index][0]) == 4.0, (
            f"bar {index + 1} is one of the tied pair and still adds up")
    assert any("tie detection is low confidence" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# A non-standard tuning and a tempo, both read out of the page's text
# ---------------------------------------------------------------------------


def test_a_named_non_standard_tuning_and_tempo_are_read_from_the_page(engraved):
    """Neither is a glyph: the tuning is recognised from the words a real
    edition prints above the first bar, and the tempo from the metronome
    mark. Both paths were reachable only with the library."""
    result = tabextract.extract(engraved("drop_d"))
    assert result.extractable
    assert result.tuning_label == "Drop D"
    assert result.tuning == tabextract.DROP_D_TUNING
    assert result.tuning != tabextract.DEFAULT_TUNING
    assert result.tempo == 88
    assert "\\tempo 88" in result.alphatex
    # alphaTex binds the FIRST \tuning entry to string 1, which is the top
    # tab line - so the emitted line is the reverse of result.tuning, and the
    # dropped D has to come out at the END of it
    assert "\\tuning E4 B3 G3 D3 A2 D2" in result.alphatex


def test_the_dropped_string_changes_the_pitch_that_sounds(engraved):
    """A tuning label nothing acts on would be decoration. The lowest string
    is a D, so the MusicXML written for this score has to say so - and the
    open 6th string has to sound D2, not E2."""
    result = tabextract.extract(engraved("drop_d"))
    root = ET.fromstring(result.musicxml)
    tunings = root.findall("./part/measure/attributes/staff-details/staff-tuning")
    assert len(tunings) == 6
    lowest = tunings[0]
    assert lowest.findtext("tuning-step") == "D"
    assert lowest.findtext("tuning-octave") == "2"


# ---------------------------------------------------------------------------
# Bars that genuinely do not add up
# ---------------------------------------------------------------------------


def test_bars_wrong_in_each_direction_are_counted_and_reported(engraved):
    """A bar can be wrong in either direction and both have to be visible -
    only the overfull count existed once, so a bar with a note missing from
    it looked clean. This score engraves both: a 4/4 bar holding five
    quarters and one holding three."""
    result = tabextract.extract(engraved("defective_bars"))
    assert result.extractable
    assert result.bars == 8
    assert result.bars_measured == 8
    assert result.bars_overfull == 4
    # Was 2. The two extra are bars whose short voice used to be padded back to
    # the meter before the arithmetic was checked, so they reported as adding
    # up - see test_a_short_voice_beside_an_overfull_one_is_reported_short.
    assert result.bars_short == 4
    assert result.bars_defective == 6
    assert result.bars_defective <= result.bars_measured
    assert max(result.bars_overfull, result.bars_short) <= result.bars_defective
    assert any("hold more than their time signature" in w for w in result.warnings)
    assert any("hold less than their time signature" in w for w in result.warnings)
    assert not result.confidence["rhythm"].startswith("high")

    bars = emitted_bars(result.alphatex)
    assert sum(q for q, _n in bars[0][0]) == 5.0, "the overfull bar is emitted as engraved"
    assert sum(q for q, _n in bars[1][0]) == 3.0, "and the short one is not padded out"


def test_a_short_voice_beside_an_overfull_one_is_reported_short(engraved):
    """The bar the library has no example of: five quarters in the upper voice
    against three in the lower, wrong in both directions at once - engraved
    here on purpose.

    This assertion used to pin a DEFECT: the short voice was padded to the
    meter before the arithmetic was checked, so the bar reported overfull and
    NOT short, and the rest that filled it went into the transcription
    indistinguishably from one the engraver printed. Extraction could therefore
    never produce a both-directions bar, and the reason was the padding rather
    than anything about the score.

    The padding is still there - it is what stops voices drifting against each
    other - but it is marked, so it no longer counts towards the bar adding up.
    The bar is now reported wrong in both directions at once, which is what it
    is, and this is the only place extraction produces that shape."""
    result = tabextract.extract(engraved("defective_bars"))

    # The alphaTex still carries the padding, because the renderer needs it:
    # the short voice is written out to the full four quarters of the meter.
    both = emitted_bars(result.alphatex)[2]
    assert len(both) == 2, both
    lengths = sorted(sum(q for q, _n in voice) for voice in both)
    assert lengths == [4.0, 5.0], both

    # The MusicXML does not: the padding is a <forward>, which holds the
    # position without claiming a rest, so the short voice's notes and rests
    # sum to THREE quarters and the measure fails Rule 8 for any consumer.
    root = ET.fromstring(result.musicxml)
    measure = root.findall("./part/measure")[2]
    sums = {}
    for note in measure.findall("note"):
        if note.find("chord") is not None:
            continue
        voice = note.findtext("voice")
        sums[voice] = sums.get(voice, 0) + int(note.findtext("duration"))
    assert sorted(sums.values()) == [3 * musicxml.DIVISIONS, 5 * musicxml.DIVISIONS], sums
    forwards = measure.findall("forward")
    assert len(forwards) == 1, ET.tostring(measure, encoding="unicode")
    assert int(forwards[0].findtext("duration")) == musicxml.DIVISIONS
    assert "deduced from the time signature" in forwards[0].findtext("footnote")
    # ...and it says which voice it belongs to, or it could not be attributed
    assert forwards[0].findtext("voice") == min(sums, key=lambda v: sums[v])

    # This bar is wrong in both directions AT ONCE, counted once as defective.
    assert 3 in result.padded_bars, result.padded_bars
    assert result.bars_overfull >= 1 and result.bars_short >= 1
    assert result.bars_defective <= result.bars_overfull + result.bars_short
    assert any("deduced from the time signature" in w for w in result.warnings)
    assert any("concurrent voices" in w for w in result.warnings)
    # the warning names the bars, not just the total
    padded_warning = next(w for w in result.warnings
                          if "deduced from the time signature" in w)
    named = padded_warning.split("The bars are: ")[1].split(".")[0]
    assert 3 in [int(n) for n in named.split(", ")], padded_warning


# ---------------------------------------------------------------------------
# What extraction must refuse
# ---------------------------------------------------------------------------


def test_notation_without_tablature_is_refused_with_its_reason(engraved):
    """Fingering numbers on a notation staff are not fret numbers, and
    reporting them as a transcription would be worse than refusing."""
    pdf = engraved("notation_only")
    info = tabextract.analyze(pdf)
    assert info["extractable"] is False
    assert info["vector"] is True
    assert info["tab_staff_count"] == 0
    assert info["standard_staff_count"] > 0

    result = tabextract.extract(pdf)
    assert result.extractable is False
    assert result.alphatex is None
    assert "standard-notation only" in result.reason


def test_a_rasterised_score_is_refused_as_a_scan(engraved):
    """The same engraving flattened to an image. There are no vector staff
    lines and no text to read, and extraction has to say so rather than
    return an empty transcription."""
    pdf = engraved("raster_scan")
    info = tabextract.analyze(pdf)
    assert info["extractable"] is False
    assert info["vector"] is False
    assert "raster" in info["reason"]

    result = tabextract.extract(pdf)
    assert result.extractable is False
    assert "raster" in result.reason
    assert result.alphatex is None


# ---------------------------------------------------------------------------
# Reading the printed meter, and barring each bar in its own
# ---------------------------------------------------------------------------


def test_a_meter_behind_a_key_signature_is_still_read(engraved):
    """Issue #90, first half. The clef, four sharps and the meter in a row
    put the meter's digits about ten and a half staff spaces into the staff,
    and a search window measured from the staff's left edge stopped at
    eight-and-a-bit - so the printed meter was never looked at and the score
    was barred as 4/4. This one is in 3/4, so that failure misplaces every
    barline rather than landing on the right answer by luck."""
    result = tabextract.extract(engraved("four_sharps_in_three_four"))
    assert result.time_signature == (3, 4)
    assert result.time_signature_source == "glyph-decoded"
    assert result.confidence["time_signature"].startswith("high")
    # the key signature it is engraved behind is read too, and from the same
    # run of glyphs - the meter is what bounds it on the right
    assert result.key_fifths == 4
    assert result.key_signature_source == "glyph-decoded"
    # and the score is BARRED in it: ground truth from the engraver's input
    assert emitted_meters(result.musicxml) == source_meters("four_sharps_in_three_four")
    assert (result.bars, result.bars_defective, result.bars_unread) == (8, 0, 0)
    for bar in emitted_bars(result.alphatex):
        for voice in bar:
            assert sum(q for q, _n in voice) == 3.0, bar


def test_a_two_digit_numerator_assembles_correctly(engraved):
    """Issue #84. A numerator needing two stacked digit glyphs - 12/8 - is
    exactly the shape a single missing digit turns into a confident WRONG
    meter rather than a detected gap: drop the '1' and the remaining lone
    '2' is still a perfectly plausible one-digit numerator. Nothing before
    this fixture engraved a real two-digit numerator through the full PDF
    pipeline; the multi-digit clustering itself was previously only ever
    exercised against hand-built glyph coordinates.

    This exercises the SMuFL digit table only (MuseScore engraves SMuFL) -
    all ten of its digits were already complete before this change, so this
    fixture was already green on main. It stays here as the missing
    real-PDF proof that the multi-digit assembly this project relies on for
    every double-digit meter actually holds, and as the regression lock for
    it - see the Opus digit gap this issue's fix closes instead, which
    cannot be exercised this way because no free engraver draws Opus."""
    result = tabextract.extract(engraved("multidigit_meter"))
    assert result.time_signature == (12, 8)
    assert result.time_signature_source == "glyph-decoded"
    assert result.confidence["time_signature"].startswith("high")
    assert emitted_meters(result.musicxml) == source_meters("multidigit_meter")
    assert (result.bars, result.bars_defective, result.bars_unread) == (8, 0, 0)
    for bar in emitted_bars(result.alphatex):
        for voice in bar:
            assert sum(q for q, _n in voice) == 6.0, bar


def test_a_meter_read_later_is_not_backdated_over_an_unread_opening(engraved):
    """Issue #90, second half. This score's opening meter is engraved
    invisibly and a 3/4 is printed part-way through, so exactly one meter can
    be read - and the one that can is not the opening one.

    Taking the first meter read anywhere as the opening meter reported this
    score as 3/4 throughout, at "read directly from the time-signature digit
    glyphs" confidence, over four bars written in 4/4 - and said nothing
    about a meter change either, because one meter recorded is not a change.
    """
    result = tabextract.extract(engraved("hidden_opening_meter"))
    # the 3/4 does not become the score's opening meter...
    assert result.time_signature == (4, 4)
    assert result.time_signature_source == "not detected (assumed 4/4)"
    # ...and an assumed meter is not reported as a read one
    assert result.confidence["time_signature"].startswith("low")
    # the bars before it are barred as the assumed 4/4, the ones after it in
    # the 3/4 that WAS read - which is what the source asked for, invisible
    # opening `<time>` and all
    assert emitted_meters(result.musicxml) == source_meters("hidden_opening_meter")
    assert emitted_meters(result.musicxml) == [(4, 4)] * 4 + [(3, 4)] * 4
    assert (result.bars, result.bars_defective) == (8, 0)
    # both things a reader needs told: the opening was not read, and the
    # meter changes part-way through
    assert any("the meter printed at the start of this score was not read" in w
               for w in result.warnings), result.warnings
    assert any("changes time signature part-way through" in w
               for w in result.warnings), result.warnings


def test_the_unread_opening_warning_is_quiet_when_the_default_agrees(engraved):
    """The other direction (review finding F10): the opening meter is
    invisible here too, but the only meter ever read anywhere in the score
    is a 4/4 - which is exactly what "assumed 4/4" already guesses. Saying
    "the opening was not read, but a 4/4 was found, so bars are barred as
    4/4" is not a caveat, it is noise: there is no discrepancy for a reader
    to act on. The warning used to fire regardless, and interpolated
    `time_signature_source` straight into the sentence - itself the string
    "not detected (assumed 4/4)" - which read as "barred as 4/4 (not
    detected (assumed 4/4))", a token dump rather than an explanation."""
    result = tabextract.extract(engraved("hidden_opening_meter_matches_the_default"))
    assert result.time_signature == (4, 4)
    assert set(emitted_meters(result.musicxml)) == {(4, 4)}
    assert not any("the meter printed at the start of this score was not read" in w
                   for w in result.warnings), result.warnings
    assert not any("changes time signature" in w for w in result.warnings), result.warnings
    # and no warning anywhere quotes time_signature_source's own parenthetical
    # back into a sentence
    assert not any("(not detected (assumed" in w for w in result.warnings), result.warnings


def test_a_meter_printed_part_way_along_a_system_starts_where_it_is_printed(engraved):
    """Issue #104. The meter used to be resolved once per staff system, so a
    change engraved part-way along one applied to the bars ahead of it as
    well: they were measured against a length nobody wrote, and a voice
    falling short of it was padded with silence towards a meter it was not in.

    Every bar here adds up exactly to its own printed meter and the 2/4 bars
    hold half what the 4/4 bars do, so a bar budgeted against its system's
    meter instead of its own cannot come out conformant."""
    result = tabextract.extract(engraved("mid_system_meter_change"))
    assert result.time_signature == (4, 4)
    assert emitted_meters(result.musicxml) == source_meters("mid_system_meter_change")
    assert emitted_meters(result.musicxml) == [(4, 4)] * 2 + [(2, 4)] * 2 + [(4, 4)] * 4
    # The change is inside the first system, not at the start of one - which
    # is the whole point of the fixture, and would go unnoticed if a
    # re-engraving moved the system break.
    page = fitz.open(engraved("mid_system_meter_change"))[0]
    staves, _ = tabextract._detect_staves(page)
    tops = sorted(s.top for s in staves if s.kind == "standard")
    changes = [entry for entry in tabextract._build_time_signature_timeline(
        [(0, page, [s for s in staves if s.kind == "tab"],
          [s for s in staves if s.kind == "standard"])])[0]
        if entry[2] != tabextract._SYSTEM_START_X]
    assert [entry[3] for entry in changes[:1]] == [(2, 4)]
    assert changes[0][1] == tops[0], "read from the first system, not the second"
    # nothing is measured against the wrong budget, so nothing is defective
    # and nothing is padded towards a meter it is not in
    assert (result.bars, result.bars_defective, result.bars_padded) == (8, 0, 0)
    assert result.inferred_rest_quarters == 0
    for bar, (num, den) in zip(emitted_bars(result.alphatex),
                               emitted_meters(result.musicxml)):
        for voice in bar:
            assert sum(q for q, _n in voice) == num * 4.0 / den, bar


def test_a_key_change_at_the_same_mid_system_barline_does_not_hide_the_meter(engraved):
    """The mid-system counterpart of test_a_meter_behind_a_key_signature_is_still_read
    (issue #90's window defect, again): four sharps printed right after a
    barline push the numerator's own left edge to 5.77 staff spaces past that
    barline, past the flat 5.0-space reach a mid-system reader sized only for
    "nothing between the barline and the meter" used to allow - so the
    printed 3/4 was dropped and the bars after it kept the previous 4/4
    budget."""
    result = tabextract.extract(engraved("mid_system_key_and_meter_change"))
    assert emitted_meters(result.musicxml) == source_meters("mid_system_key_and_meter_change")
    assert emitted_meters(result.musicxml) == [(4, 4)] * 2 + [(3, 4)] * 6
    assert (result.bars, result.bars_defective) == (8, 0)
    for bar, (num, den) in zip(emitted_bars(result.alphatex),
                               emitted_meters(result.musicxml)):
        for voice in bar:
            assert sum(q for q, _n in voice) == num * 4.0 / den, bar


@pytest.mark.parametrize("name", ("notation_and_tab", "rests_and_flags", "two_voices",
                                  "tuplet_and_tie", "volta", "defective_bars"))
def test_no_meter_change_is_invented_where_none_is_printed(name, engraved):
    """The other direction, and the reason the mid-system reader only looks
    just past a barline: the same digit glyphs spell tuplet numbers and
    string numbers all over a staff, and a score in one meter from end to end
    must stay in it. `tuplet_and_tie` prints a triplet number; `volta` prints
    "1." and "2." over its endings."""
    result = tabextract.extract(engraved(name))
    assert set(emitted_meters(result.musicxml)) == {(4, 4)}, name
    assert not any("changes time signature" in w for w in result.warnings), result.warnings


# ---------------------------------------------------------------------------
# The fixtures themselves
# ---------------------------------------------------------------------------


ENGRAVED_NAMES = (
    "notation_and_tab", "rests_and_flags", "tab_only", "tab_only_short_last_system",
    "two_voices", "tuplet_and_tie", "drop_d", "defective_bars", "volta",
    "harmonics_dense", "notation_only", "four_sharps_in_three_four",
    "hidden_opening_meter", "hidden_opening_meter_matches_the_default",
    "mid_system_meter_change", "mid_system_key_and_meter_change",
    "multidigit_meter",
)
SYNTHESISED_NAMES = ("raster_scan", "fake_music_font")


@pytest.mark.parametrize("name", ENGRAVED_NAMES + SYNTHESISED_NAMES)
def test_every_engraved_fixture_is_committed(name, engraved):
    """A test suite that goes quiet when its fixtures vanish is how this
    whole gap started. These are committed, so a missing one fails here
    instead of turning some other test into a skip."""
    assert engraved(name).stat().st_size > 0


@pytest.mark.parametrize("name", ENGRAVED_NAMES + SYNTHESISED_NAMES)
def test_every_fixture_says_which_tool_made_it(name, engraved):
    """These assertions measure coordinates, and a fixture re-engraved by a
    different version of the engraver lands different ones - so the version
    has to be part of what is checked, not only written down in a README.
    The MusicXML has a stronger guard of its own: it is regenerated from the
    script and compared byte for byte."""
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "tools" / "tab_extract" / "engrave_fixtures.py"
    spec = importlib.util.spec_from_file_location("engrave_fixtures", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    creator = fitz.open(engraved(name)).metadata["creator"]
    expected = (module.SYNTHESISED_CREATOR if name in SYNTHESISED_NAMES
                else module.ENGRAVER_CREATOR)
    assert creator == expected, f"{name}.pdf was made by {creator!r}"


@pytest.mark.parametrize("name", ENGRAVED_NAMES)
def test_every_engraved_fixture_keeps_the_musicxml_it_came_from(name):
    """The PDF is only regenerable if the engraver's input is here beside it,
    and only trustworthy if that input is what produced it."""
    source = ENGRAVED_DIR / f"{name}.musicxml"
    assert source.is_file(), source
    root = ET.parse(source).getroot()
    assert root.tag == "score-partwise"
    assert root.findall("./part/measure")


def test_a_missing_engraved_fixture_fails_rather_than_skipping():
    """The failure mode this whole change exists to end. A fixture that goes
    missing must stop the suite, not quietly turn its tests into skips that a
    green log hides.

    Written with an explicit except rather than pytest.raises on purpose: if
    the helper called pytest.skip, pytest.raises would let that through and
    this test would SKIP - reporting nothing at all about the very behaviour
    it exists to check. Verified by making the helper skip and watching this
    go red."""
    from conftest import engraved_pdf

    try:
        engraved_pdf("no_such_fixture")
    except AssertionError as exc:
        assert "engrave_fixtures.py" in str(exc), "and says how to get it back"
    except BaseException as exc:  # noqa: BLE001 - pytest.skip lands here too
        raise AssertionError(
            f"a missing fixture must fail the suite, not raise {type(exc).__name__}"
        ) from None
    else:
        raise AssertionError("a missing fixture was reported as present")


class _FakeReporter:
    """Enough of pytest's terminal reporter to see what the summary wrote."""

    def __init__(self):
        self.stats = {}
        self.lines = []

    def write_sep(self, _char, text, **_kw):
        self.lines.append(text)


def test_the_summary_says_how_many_tests_skipped_for_want_of_a_library(monkeypatch, tmp_path):
    """The loud skip. A suite that skipped a third of itself said so only by
    a number nobody compared against anything, which is exactly how 36
    skipped extraction tests went unnoticed. A count on the screen is not a
    guarantee, but silence was not one either.

    The count comes from a counter the library fixtures increment as they
    skip, NOT from matching text in skip reasons afterwards. Text matching
    would quietly stop counting the day someone worded a new skip
    differently - and an undercount here reads as "everything ran", which is
    the one thing this must never say by accident."""
    import conftest

    monkeypatch.setattr(conftest, "_library_skips", [])
    monkeypatch.delenv("FERMATA_TEST_LIBRARY", raising=False)

    # nothing skipped and no library configured: no claim either way
    reporter = _FakeReporter()
    conftest.pytest_terminal_summary(reporter, 0, None)
    assert reporter.lines == []

    # a library fixture skipping is what feeds the count - by calling the
    # helper, not by the words it happens to use
    for reason in ("no library here", "a differently worded reason"):
        with pytest.raises(BaseException):
            conftest.skip_without_library(reason)
    assert len(conftest._library_skips) == 2

    reporter = _FakeReporter()
    conftest.pytest_terminal_summary(reporter, 0, None)
    assert len(reporter.lines) == 1
    assert "2 test(s) skipped for want of a sheet music library" in reporter.lines[0]
    assert "did NOT exercise extraction" in reporter.lines[0]

    # a real library present and nothing skipped for want of one: say so
    monkeypatch.setattr(conftest, "_library_skips", [])
    monkeypatch.setenv("FERMATA_TEST_LIBRARY", str(tmp_path))
    reporter = _FakeReporter()
    conftest.pytest_terminal_summary(reporter, 0, None)
    assert reporter.lines == ["real-library tests all ran (FERMATA_TEST_LIBRARY is set)"]

    # ...and a path that is not a library does NOT earn the reassuring line
    monkeypatch.setenv("FERMATA_TEST_LIBRARY", str(tmp_path / "nope"))
    reporter = _FakeReporter()
    conftest.pytest_terminal_summary(reporter, 0, None)
    assert reporter.lines == []


def test_the_committed_musicxml_still_matches_its_generator():
    """A fixture whose source has drifted from the script that writes it is
    no longer regenerable, and nobody would find out until the next time
    someone tried."""
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "tools" / "tab_extract" / "engrave_fixtures.py"
    spec = importlib.util.spec_from_file_location("engrave_fixtures", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    drifted = module.write_musicxml(check_only=True)
    assert drifted == [], drifted
