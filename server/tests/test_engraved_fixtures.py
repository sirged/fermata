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
  * Diamond/harmonic noteheads, which are deliberately not in the SMuFL map.
  * Scale. The library's reference score is 50 bars of real two-voice
    fingerstyle writing; the fixture with two voices is eight contrived bars.
    A regression that only shows up in density will still only show up
    there.
"""
import re
import xml.etree.ElementTree as ET

import fitz
import pytest

from fermata import glyph_rhythm, tabextract

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
    page = fitz.open(engraved("notation_and_tab"))[0]
    trace = page.get_texttrace()
    assert glyph_rhythm._smufl_font_names(trace) == {"Leland"}
    # the text fonts on the same page are not music fonts
    drawn = {s.get("font", "").split("+")[-1] for s in trace}
    assert "Edwin-Roman" in drawn or "FreeSans" in drawn


def test_a_font_using_the_private_use_area_for_something_else_is_not_music():
    """The guard that makes the codepoint key safe: landing in SMuFL's range
    is not enough, the codepoints have to BE calibrated music symbols, and
    several of them. Sibelius's Opus draws at U+F0xx and an icon font can
    draw anywhere in the private use area; either would be read as an
    engraved staff if presence in the range were the test."""
    opus_like = [{"font": "Opus", "chars": [(0xF0CF, 1, (0, 0), (0, 0, 1, 1))] * 40}]
    assert glyph_rhythm._smufl_font_names(opus_like) == set()
    # and a font drawing a couple of real SMuFL codepoints is still not one:
    # it takes SMUFL_MIN_MAPPED_GLYPHS of them
    barely = [{"font": "Mystery", "chars": [(0xE0A4, 1, (0, 0), (0, 0, 1, 1))] * 3}]
    assert glyph_rhythm._smufl_font_names(barely) == set()
    enough = [{"font": "Mystery",
               "chars": [(0xE0A4, 1, (0, 0), (0, 0, 1, 1))] * glyph_rhythm.SMUFL_MIN_MAPPED_GLYPHS}]
    assert glyph_rhythm._smufl_font_names(enough) == {"Mystery"}


class _TracePage:
    """A page that draws exactly the characters a test hands it.

    Nothing in the committed fixtures makes a music font draw a plain text
    character, so the rule that keeps those out of the decode has no engraved
    example - and an untested rule is the same as no rule. This supplies one
    without pretending to be a real page."""

    parent = None

    def __init__(self, font, codes):
        self._spans = [{"font": font,
                        "chars": [(code, i + 1, (0.0, 0.0), (float(i), 0.0, float(i) + 1, 1.0))
                                  for i, code in enumerate(codes)]}]

    def get_fonts(self, full=False):
        return []

    def get_texttrace(self):
        return self._spans


def test_a_music_font_may_also_draw_text_and_that_text_is_not_a_glyph_event():
    """A music font can carry plain characters. Counting them as music
    symbols would report a healthy page as mostly unrecognised vocabulary and
    take a perfectly good decode to the spacing fallback; counting them as
    UNKNOWN music symbols would be worse, because the unknown ratio is what
    decides that. So codepoints outside SMuFL's range are ignored outright,
    while an unrecognised codepoint INSIDE it is kept and reported."""
    glyph_rhythm.clear_caches()
    page = _TracePage("Leland", [
        0xE0A4, 0xE0A4, 0xE0A4, 0xE0A4,   # enough noteheads to be a music font
        ord("A"), ord("3"),               # ...plus text, which is not a symbol
        0xE0D9,                           # ...plus a diamond notehead, deliberately uncalibrated
    ])
    glyphs = glyph_rhythm.extract_glyph_events(page)
    assert [e.code for e in glyphs.events] == [0xE0A4] * 4 + [0xE0D9]
    assert [e.calibration_key for e in glyphs.unknown] == ["U+E0D9"]
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
    assert result.bars_short == 2
    assert result.bars_defective == 6
    assert result.bars_defective <= result.bars_measured
    assert max(result.bars_overfull, result.bars_short) <= result.bars_defective
    assert any("hold more than their time signature" in w for w in result.warnings)
    assert any("hold less than their time signature" in w for w in result.warnings)
    assert not result.confidence["rhythm"].startswith("high")

    bars = emitted_bars(result.alphatex)
    assert sum(q for q, _n in bars[0][0]) == 5.0, "the overfull bar is emitted as engraved"
    assert sum(q for q, _n in bars[1][0]) == 3.0, "and the short one is not padded out"


def test_a_short_voice_beside_an_overfull_one_is_padded_not_reported_short(engraved):
    """The bar the library has no example of: five quarters in the upper voice
    against three in the lower, wrong in both directions at once.

    It is engraved here, and what actually happens is worth knowing rather
    than asserting away - the short voice is padded to the meter with
    inferred silence (which is what stops voices drifting against each
    other), so the bar is reported overfull and NOT short. The padding is
    announced. `_bar_conformance` counts a both-directions bar once and there
    is a unit test for that shape, but this is why extraction has not so far
    produced one."""
    result = tabextract.extract(engraved("defective_bars"))
    both = emitted_bars(result.alphatex)[2]
    assert len(both) == 2, both
    lengths = sorted(sum(q for q, _n in voice) for voice in both)
    assert lengths == [4.0, 5.0], both
    assert any("deduced from the time signature" in w for w in result.warnings)
    assert any("concurrent voices" in w for w in result.warnings)


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
# The fixtures themselves
# ---------------------------------------------------------------------------


ENGRAVED_NAMES = (
    "notation_and_tab", "rests_and_flags", "tab_only", "two_voices",
    "tuplet_and_tie", "drop_d", "defective_bars", "notation_only", "raster_scan",
)


@pytest.mark.parametrize("name", ENGRAVED_NAMES)
def test_every_engraved_fixture_is_committed(name, engraved):
    """A test suite that goes quiet when its fixtures vanish is how this
    whole gap started. These are committed, so a missing one fails here
    instead of turning some other test into a skip."""
    assert engraved(name).stat().st_size > 0


@pytest.mark.parametrize("name", [n for n in ENGRAVED_NAMES if n != "raster_scan"])
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

    def __init__(self, skipped):
        self.stats = {"skipped": skipped}
        self.lines = []

    def write_sep(self, _char, text, **_kw):
        self.lines.append(text)


class _FakeReport:
    def __init__(self, reason):
        self.longrepr = ("tests/test_x.py", 1, f"Skipped: {reason}")


def test_the_summary_says_how_many_tests_skipped_for_want_of_a_library(monkeypatch):
    """The loud skip. Today a suite that skipped a third of itself said so
    only by a number nobody compared against anything - which is exactly how
    36 skipped extraction tests went unnoticed. A count on the screen is not
    a guarantee, but silence was not one either.

    An optional-tool skip must not be counted into it: "node not available"
    says nothing about whether extraction was exercised."""
    import conftest

    monkeypatch.delenv("FERMATA_TEST_LIBRARY", raising=False)
    reporter = _FakeReporter([
        _FakeReport("FERMATA_TEST_LIBRARY not set (or missing 'To Zanarkand' fixture)"),
        _FakeReport("FERMATA_TEST_LIBRARY not set (or missing Tarrega fixture)"),
        _FakeReport("node not available"),
    ])
    conftest.pytest_terminal_summary(reporter, 0, None)
    assert len(reporter.lines) == 1
    assert "2 test(s) skipped for want of a sheet music library" in reporter.lines[0]
    assert "did NOT exercise extraction" in reporter.lines[0]

    # nothing library-shaped skipped, and the library was there: say so
    reporter = _FakeReporter([_FakeReport("node not available")])
    monkeypatch.setenv("FERMATA_TEST_LIBRARY", "/somewhere")
    conftest.pytest_terminal_summary(reporter, 0, None)
    assert reporter.lines == ["real-library tests all ran (FERMATA_TEST_LIBRARY is set)"]

    # ...and with no library set and nothing skipped for it, no claim either way
    reporter = _FakeReporter([])
    monkeypatch.delenv("FERMATA_TEST_LIBRARY", raising=False)
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
