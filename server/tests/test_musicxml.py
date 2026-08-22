"""Tests for the MusicXML emitter and the profile's conformance rules.

These read the emitted document back with a parser rather than matching
substrings, because the things that go wrong in MusicXML are structural: a
child in the wrong place still looks right in a diff and fails validation, and
a mirrored string numbering is invisible in the text but puts every note on
the wrong string.

Nothing here needs a sheet music library, so it all runs in CI. Validation
against the real MusicXML 4.0 schema is a separate step and is skipped unless
FERMATA_MUSICXML_XSD points at a copy of it - see test_validates_against_xsd.
That is why the child ORDER of every xs:sequence element is asserted directly
here as well: those assertions are what the schema would have caught, and they
run whether or not a schema is to hand.
"""

import os
import xml.etree.ElementTree as ET

import pytest

from fermata import musicxml, tabextract

DEFAULT_TUNING = tabextract.DEFAULT_TUNING
DROP_D = tabextract.DROP_D_TUNING


def _one_bar(beats, ts=(4, 4), **kwargs):
    return musicxml.build("T", None, DEFAULT_TUNING, ts, [(beats, ts)], **kwargs)


def _root(xml: str):
    return ET.fromstring(xml)


def _notes(root):
    return root.findall("./part/measure/note")


# ---------------------------------------------------------------------------
# Rules 1-5: document form and required structure
# ---------------------------------------------------------------------------


def test_document_is_score_partwise_version_4():
    root = _root(_one_bar([[(4, 0, [(1, 0)])]]))
    assert root.tag == "score-partwise"
    assert root.get("version") == "4.0"


def test_no_doctype_is_emitted():
    """The MusicXML DTDs are deprecated as of 4.0, the public DTD URL no longer
    resolves, and secure-by-default XML parsers refuse a document carrying an
    external DTD reference at all - so a DOCTYPE costs interoperability."""
    xml = _one_bar([[(4, 0, [(1, 0)])]])
    assert "<!DOCTYPE" not in xml
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')


def test_part_list_declares_the_part_the_music_is_in():
    root = _root(_one_bar([[(4, 0, [(1, 0)])]]))
    declared = [sp.get("id") for sp in root.findall("./part-list/score-part")]
    assert declared == ["P1"]
    assert [p.get("id") for p in root.findall("./part")] == ["P1"]
    # midi-instrument must reference a score-instrument that exists
    instruments = {si.get("id") for si in root.findall("./part-list/score-part/score-instrument")}
    for midi in root.findall("./part-list/score-part/midi-instrument"):
        assert midi.get("id") in instruments


def test_attributes_children_are_in_schema_order():
    """<attributes> is an xs:sequence, so anything but this order is invalid
    however sensible it reads."""
    root = _root(_one_bar([[(4, 0, [(1, 0)])]]))
    attributes = root.find("./part/measure/attributes")
    order = [child.tag for child in attributes]
    assert order == ["divisions", "key", "time", "clef", "staff-details"]


def test_note_children_are_in_schema_order():
    """A normal note's sequence is chord?, pitch, duration, ..., voice, type,
    dot*, ..., notations - voice BEFORE type, notations last."""
    root = _root(_one_bar([[(4, 1, [(1, 0), (2, 2)])]]))
    first, second = _notes(root)
    assert [c.tag for c in first] == [
        "pitch", "duration", "voice", "type", "dot", "notations"]
    # the second note of a chord leads with <chord/>
    assert [c.tag for c in second] == [
        "chord", "pitch", "duration", "voice", "type", "dot", "notations"]


def test_rest_is_a_note_with_rest_in_place_of_pitch():
    root = _root(_one_bar([[(4, 0, [])]]))
    (rest,) = _notes(root)
    assert [c.tag for c in rest] == ["rest", "duration", "voice", "type"]


def test_staff_details_declares_six_lines_and_one_tuning_per_string():
    root = _root(_one_bar([[(4, 0, [(1, 0)])]]))
    details = root.find("./part/measure/attributes/staff-details")
    assert details.findtext("staff-lines") == "6"
    tunings = details.findall("staff-tuning")
    assert len(tunings) == 6
    assert [t.tag for t in details] == ["staff-lines"] + ["staff-tuning"] * 6


def test_staff_tuning_line_1_is_the_lowest_string_not_the_highest():
    """The single most consequential detail in the profile. MusicXML numbers
    staff LINES from the bottom and STRINGS from the top, so line 1 is the low
    E and line 6 the high E. Getting it backwards still validates and still
    loads - it just mirrors every note onto the wrong string."""
    root = _root(_one_bar([[(4, 0, [(1, 0)])]]))
    details = root.find("./part/measure/attributes/staff-details")
    by_line = {
        t.get("line"): (t.findtext("tuning-step"), t.findtext("tuning-octave"))
        for t in details.findall("staff-tuning")
    }
    assert by_line["1"] == ("E", "2")  # bottom line, string 6, low E
    assert by_line["2"] == ("A", "2")
    assert by_line["3"] == ("D", "3")
    assert by_line["4"] == ("G", "3")
    assert by_line["5"] == ("B", "3")
    assert by_line["6"] == ("E", "4")  # top line, string 1, high E


def test_drop_d_tuning_lowers_only_the_bottom_line():
    root = _root(musicxml.build("T", None, DROP_D, (4, 4), [([[(4, 0, [(6, 0)])]], (4, 4))]))
    details = root.find("./part/measure/attributes/staff-details")
    tunings = details.findall("staff-tuning")
    assert (tunings[0].findtext("tuning-step"), tunings[0].findtext("tuning-octave")) == ("D", "2")
    assert (tunings[5].findtext("tuning-step"), tunings[5].findtext("tuning-octave")) == ("E", "4")
    # and the open sixth string sounds D2, not E2
    (note,) = _notes(root)
    assert note.find("pitch/step").text == "D"
    assert note.find("pitch/octave").text == "2"


def test_tab_clef_is_declared():
    root = _root(_one_bar([[(4, 0, [(1, 0)])]]))
    clef = root.find("./part/measure/attributes/clef")
    assert clef.findtext("sign") == "TAB"


def test_every_note_carries_a_string_and_a_fret():
    beats = [[(8, 0, [(1, 0), (3, 5)]), (8, 0, []), (4, 0, [(6, 12)])]]
    root = _root(_one_bar(beats))
    pitched = [n for n in _notes(root) if n.find("rest") is None]
    assert len(pitched) == 3
    for note in pitched:
        technical = note.find("notations/technical")
        assert technical.findtext("string") is not None
        assert technical.findtext("fret") is not None
    # a rest carries neither - there is no string it is not being played on
    rests = [n for n in _notes(root) if n.find("rest") is not None]
    assert rests and all(n.find("notations") is None for n in rests)


def test_capo_follows_the_tunings_and_raises_the_sounding_pitch():
    """staff-details' sequence puts capo AFTER every staff-tuning, and the
    spec defines the tunings as the open, non-capo values - so a capo shows up
    as an offset, not as rewritten tunings."""
    root = _root(_one_bar([[(4, 0, [(1, 0)])]], capo=2))
    details = root.find("./part/measure/attributes/staff-details")
    assert [c.tag for c in details][-1] == "capo"
    assert details.findtext("capo") == "2"
    assert details.findall("staff-tuning")[5].findtext("tuning-step") == "E"
    (note,) = _notes(root)
    # open first string, capo at the second fret: F sharp 4, not E4
    assert note.findtext("pitch/step") == "F"
    assert note.findtext("pitch/alter") == "1"
    assert note.findtext("pitch/octave") == "4"


# ---------------------------------------------------------------------------
# Rule 2: divisions
# ---------------------------------------------------------------------------


def test_divisions_makes_every_duration_an_integer():
    """The point of the chosen divisions value: no duration in the emitter's
    vocabulary needs a fraction, so a consumer checking Rule 8 does integer
    arithmetic."""
    for code in musicxml.TYPE_NAMES:
        for dots in (0, 1, 2):
            value = musicxml.beat_divisions(code, dots)
            assert value == int(value)
            assert value > 0


def test_the_tightest_duration_is_the_double_dotted_thirty_second():
    # 1/8 of a quarter times 7/4 = 7/32 of a quarter; at 480 divisions that
    # is 105, and it is the value that decides the minimum for DIVISIONS.
    assert musicxml.beat_divisions(32, 2) == 105
    assert musicxml.beat_divisions(4, 0) == musicxml.DIVISIONS


def test_measure_divisions_honours_the_denominator():
    # 6/8 and 3/4 are both three quarters' worth
    assert musicxml.measure_divisions((6, 8)) == musicxml.measure_divisions((3, 4))
    assert musicxml.measure_divisions((4, 4)) == 4 * musicxml.DIVISIONS


def test_divisions_is_declared_once_and_time_again_only_on_a_change():
    measures = [
        ([[(4, 0, [(1, 0)])] * 4], (4, 4)),
        ([[(4, 0, [(1, 0)])] * 4], (4, 4)),
        ([[(4, 0, [(1, 0)])] * 3], (3, 4)),
    ]
    root = _root(musicxml.build("T", None, DEFAULT_TUNING, (4, 4), measures))
    bars = root.findall("./part/measure")
    assert len(bars) == 3
    assert len(root.findall("./part/measure/attributes/divisions")) == 1
    assert bars[1].find("attributes") is None
    # the meter change re-declares <time> and nothing else
    changed = bars[2].find("attributes")
    assert [c.tag for c in changed] == ["time"]
    assert changed.findtext("time/beats") == "3"


# ---------------------------------------------------------------------------
# Rules 6-8: voices, backup, and the sum invariant
# ---------------------------------------------------------------------------


def _voice_sums(measure):
    """Total duration written per voice, the way an independent consumer would
    read it: a note's duration advances its voice unless it is a chord member,
    and <backup> rewinds."""
    sums = {}
    for child in measure:
        if child.tag == "backup":
            continue
        if child.tag != "note":
            continue
        if child.find("chord") is not None:
            continue
        voice = child.findtext("voice")
        sums[voice] = sums.get(voice, 0) + int(child.findtext("duration"))
    return sums


def test_voices_are_numbered_from_one_in_order():
    beats = [
        [(4, 0, [(1, 0)]), (4, 0, [(1, 2)]), (4, 0, [(1, 3)])],
        [(2, 1, [(5, 0)])],
    ]
    root = _root(_one_bar(beats, ts=(3, 4)))
    voices = [n.findtext("voice") for n in _notes(root)]
    assert set(voices) == {"1", "2"}
    # voice 1 is written first, in full, before voice 2 starts
    assert voices == ["1", "1", "1", "2"]


def test_backup_returns_to_the_start_of_the_measure():
    beats = [
        [(4, 0, [(1, 0)]), (4, 0, [(1, 2)]), (4, 0, [(1, 3)])],
        [(2, 1, [(5, 0)])],
    ]
    root = _root(_one_bar(beats, ts=(3, 4)))
    measure = root.find("./part/measure")
    (backup,) = measure.findall("backup")
    assert int(backup.findtext("duration")) == 3 * musicxml.DIVISIONS


def test_a_monophonic_measure_needs_no_backup():
    root = _root(_one_bar([[(4, 0, [(1, 0)])] * 4]))
    assert root.findall("./part/measure/backup") == []


def test_both_voices_sum_to_the_measure_duration():
    beats = [
        [(4, 0, [(1, 0)]), (4, 0, [(1, 2)]), (4, 0, [(1, 3)])],
        [(2, 1, [(5, 0)])],
    ]
    root = _root(_one_bar(beats, ts=(3, 4)))
    expected = musicxml.measure_divisions((3, 4))
    assert _voice_sums(root.find("./part/measure")) == {"1": expected, "2": expected}


def test_a_chord_does_not_advance_the_voice():
    """Every note of a chord carries the same duration, but only the first one
    moves the position - so a four-note chord in 4/4 still fills one beat."""
    beats = [[(4, 0, [(6, 0), (5, 2), (4, 2), (3, 1)])] + [(4, 0, [(1, 0)])] * 3]
    root = _root(_one_bar(beats))
    measure = root.find("./part/measure")
    assert len(_notes(root)) == 7
    assert _voice_sums(measure) == {"1": musicxml.measure_divisions((4, 4))}


def test_an_overfull_bar_is_emitted_as_read_not_padded_or_trimmed():
    """The emitter must not make the arithmetic come out. A bar that does not
    add up is a defect the extractor already counts, and emitting a standard
    format is what lets somebody else's tool find it - silently fixing it here
    would destroy exactly that."""
    over = [[(4, 0, [(1, 0)])] * 5]  # five quarters in a 4/4 bar
    root = _root(_one_bar(over))
    assert _voice_sums(root.find("./part/measure")) == {"1": 5 * musicxml.DIVISIONS}
    short = [[(4, 0, [(1, 0)])] * 2]
    root = _root(_one_bar(short))
    assert _voice_sums(root.find("./part/measure")) == {"1": 2 * musicxml.DIVISIONS}


def test_voice_durations_reports_the_same_totals_the_xml_carries():
    """voice_durations() is what the extractor reports Rule 8 from, so it has
    to agree with what a consumer reads out of the emitted document."""
    beats = [
        [(4, 0, [(1, 0)]), (4, 0, [(1, 2)]), (4, 0, [(1, 3)])],
        [(2, 1, [(5, 0)])],
    ]
    root = _root(_one_bar(beats, ts=(3, 4)))
    from_model = musicxml.voice_durations(beats)
    from_xml = _voice_sums(root.find("./part/measure"))
    assert sorted(from_model) == sorted(from_xml.values())


def test_voice_durations_is_the_left_hand_side_of_rule_8_for_every_shape():
    """The Rule 8 left-hand side is what every reported conformance figure is
    computed from, and one test guarding it means one deletion removes all of
    its coverage. Each of these is a shape that can change the answer: a dotted
    beat, a chord (one beat, not one per note), a rest, a beat with no writable
    pitch, and inferred silence.
    """
    D = musicxml.DIVISIONS
    cases = [
        ([[(4, 0, [(1, 0)])]], [D]),
        ([[(4, 1, [(1, 0)])]], [D + D // 2]),
        ([[(4, 2, [(1, 0)])]], [D + D // 2 + D // 4]),
        # a four-note chord is ONE beat (Rule 7)
        ([[(4, 0, [(6, 0), (5, 2), (4, 2), (3, 1)])]], [D]),
        # a printed rest counts; an inferred one does not (Rule 14)
        ([[(4, 0, [])]], [D]),
        ([[(4, 0, musicxml.inferred_rest())]], [0]),
        ([[(4, 0, [(1, 0)]), (4, 0, musicxml.inferred_rest())]], [D]),
        # a note with no writable pitch keeps its beat, as a rest (Rule 11)
        ([[(4, 0, [(1, 78)])]], [D]),
        # two voices, in voice order
        ([[(2, 0, [(1, 0)])], [(4, 0, [(5, 0)]), (4, 0, [(5, 2)])]], [2 * D, 2 * D]),
        # a flat list of beats is the one-voice case
        ([(4, 0, [(1, 0)]), (8, 0, [(1, 2)])], [D + D // 2]),
        ([], []),
    ]
    for beats, expected in cases:
        assert musicxml.voice_durations(beats) == expected, beats
        if not beats:
            continue
        # ...and it agrees with what the emitted file carries, which is the
        # only reason the number is worth reporting
        measure = _root(_one_bar(beats, ts=(4, 4))).find("./part/measure")
        sums = _voice_sums(measure)
        assert sorted(sums.values()) == sorted(v for v in expected if v), beats


# ---------------------------------------------------------------------------
# Rule 14: inferred silence
# ---------------------------------------------------------------------------


def _padded_bar(ts=(3, 4)):
    """A 3/4 bar whose second voice sounds one quarter and was filled out to
    the meter with a half note of silence nothing on the page said was there."""
    return [
        [(4, 0, [(1, 0)]), (4, 0, [(1, 2)]), (4, 0, [(1, 3)])],
        [(4, 0, [(5, 0)]), (2, 0, musicxml.inferred_rest())],
    ]


def test_inferred_silence_is_a_forward_and_not_a_rest():
    """Rule 14. A rest is a claim that somebody engraved one. Writing invented
    silence as a rest makes the measure add up, so Rule 8 passes and the notes
    that went missing leave no trace anywhere."""
    measure = _root(_one_bar(_padded_bar(), ts=(3, 4))).find("./part/measure")
    rests = [n for n in measure.findall("note") if n.find("rest") is not None]
    assert rests == [], "inferred silence must not be written as a rest"
    (forward,) = measure.findall("forward")
    assert int(forward.findtext("duration")) == 2 * musicxml.DIVISIONS
    assert forward.findtext("voice") == "2", "and it says which voice it belongs to"
    assert forward.findtext("footnote") == musicxml.INFERRED_REST_FOOTNOTE


def test_forward_children_are_in_schema_order():
    """forward's sequence is duration, footnote?, level?, voice?, staff? - the
    footnote comes BEFORE the voice. Any other order fails validation outright.
    """
    measure = _root(_one_bar(_padded_bar(), ts=(3, 4))).find("./part/measure")
    (forward,) = measure.findall("forward")
    assert [c.tag for c in forward] == ["duration", "footnote", "voice"]


def test_a_padded_measure_fails_rule_8_for_a_consumer_that_never_heard_of_us():
    """The point of the whole mechanism. A consumer summing each voice's notes
    and rests has to see the measure fall short by exactly what was not read -
    otherwise the producer's own defect count and everyone else's disagree, and
    emitting a standard format buys nothing."""
    measure = _root(_one_bar(_padded_bar(), ts=(3, 4))).find("./part/measure")
    expected = musicxml.measure_divisions((3, 4))
    sums = _voice_sums(measure)
    assert sums["1"] == expected, "the voice that was fully read still adds up"
    assert sums["2"] == musicxml.DIVISIONS, "the padded one falls short by the padding"
    # and the figure the emitter's own helper reports is that same number
    assert musicxml.voice_durations(_padded_bar()) == [expected, musicxml.DIVISIONS]


def test_a_forward_still_advances_the_position_for_the_following_voice():
    """Inferred silence is emitted because the bar has to play: a voice that
    entered late still enters late. So the position it holds has to be real -
    a <backup> after it must return to the START of the measure, not to
    wherever the notes alone reached."""
    beats = [
        [(4, 0, [(1, 0)]), (2, 0, musicxml.inferred_rest())],
        [(4, 0, [(5, 0)]), (4, 0, [(5, 2)]), (4, 0, [(5, 3)])],
    ]
    measure = _root(_one_bar(beats, ts=(3, 4))).find("./part/measure")
    (backup,) = measure.findall("backup")
    assert int(backup.findtext("duration")) == 3 * musicxml.DIVISIONS
    # the leading case too: silence first, then the note it enters on
    leading = [
        [(2, 0, musicxml.inferred_rest()), (4, 0, [(1, 0)])],
        [(4, 0, [(5, 0)]), (4, 0, [(5, 2)]), (4, 0, [(5, 3)])],
    ]
    measure = _root(_one_bar(leading, ts=(3, 4))).find("./part/measure")
    children = [c.tag for c in measure if c.tag in ("forward", "note", "backup")]
    assert children[:2] == ["forward", "note"], children
    (backup,) = measure.findall("backup")
    assert int(backup.findtext("duration")) == 3 * musicxml.DIVISIONS


def test_an_inferred_rest_is_still_an_ordinary_rest_to_everything_else():
    """The marker rides in the beat's notes slot, so it must stay falsy and
    empty - every existing reader of the beats model asks only whether a beat
    has notes, and a marker that changed that answer would turn inferred
    silence into a note with no strings."""
    marked = musicxml.inferred_rest()
    assert not marked
    assert len(marked) == 0
    assert marked == []
    assert musicxml.is_inferred_rest(marked)
    assert not musicxml.is_inferred_rest([])
    assert not musicxml.is_inferred_rest(None)
    assert not musicxml.is_inferred_rest([(1, 0)])
    # ...and it is a fresh list each time, not one shared mutable instance
    other = musicxml.inferred_rest()
    marked.append((1, 0))
    assert other == []


# ---------------------------------------------------------------------------
# Rules 9-13: fret, string and pitch
# ---------------------------------------------------------------------------


def test_open_strings_sound_their_tuning():
    for string, expected in enumerate(reversed(DEFAULT_TUNING), start=1):
        midi = musicxml.open_string_midi(DEFAULT_TUNING, string)
        assert midi == musicxml.tuning_midi(expected)
    assert musicxml.open_string_midi(DEFAULT_TUNING, 6) == 40  # low E2
    assert musicxml.open_string_midi(DEFAULT_TUNING, 1) == 64  # high E4


def test_a_fret_raises_the_open_string_by_that_many_semitones():
    root = _root(_one_bar([[(4, 0, [(3, 2)])]]))
    (note,) = _notes(root)
    # third string is G3 (MIDI 55); the second fret is A3
    assert note.findtext("pitch/step") == "A"
    assert note.findtext("pitch/octave") == "3"
    assert note.find("pitch/alter") is None


@pytest.mark.parametrize("string,fret,midi", [
    (1, 0, 64), (1, 12, 76), (2, 0, 59), (3, 0, 55),
    (4, 0, 50), (5, 0, 45), (6, 0, 40), (6, 3, 43),
])
def test_emitted_pitch_matches_the_computed_midi_number(string, fret, midi):
    root = _root(_one_bar([[(4, 0, [(string, fret)])]]))
    (note,) = _notes(root)
    step = note.findtext("pitch/step")
    alter = int(note.findtext("pitch/alter") or 0)
    octave = int(note.findtext("pitch/octave"))
    assert musicxml.pitch_midi(step, alter, octave) == midi


def test_spelling_round_trips_for_every_pitch_and_every_key():
    """A spelling that does not resolve back to the MIDI number it came from
    is a wrong note, not a stylistic choice."""
    for fifths in range(-7, 8):
        for midi in range(0, 128):
            step, alter, octave = musicxml.spell_pitch(midi, fifths)
            assert musicxml.pitch_midi(step, alter, octave) == midi
            # never a double sharp or double flat
            assert -1 <= alter <= 1


@pytest.mark.parametrize("fifths,scale", [
    (0, ["C", "D", "E", "F", "G", "A", "B"]),
    (1, ["G", "A", "B", "C", "D", "E", "F#"]),
    (2, ["D", "E", "F#", "G", "A", "B", "C#"]),
    (4, ["E", "F#", "G#", "A", "B", "C#", "D#"]),
    (7, ["C#", "D#", "E#", "F#", "G#", "A#", "B#"]),
    (-1, ["F", "G", "A", "Bb", "C", "D", "E"]),
    (-3, ["Eb", "F", "G", "Ab", "Bb", "C", "D"]),
    (-4, ["Ab", "Bb", "C", "Db", "Eb", "F", "G"]),
    (-6, ["Gb", "Ab", "Bb", "Cb", "Db", "Eb", "F"]),
])
def test_every_note_of_a_key_is_spelled_the_way_the_key_spells_it(fifths, scale):
    """The part of pitch spelling that is not a matter of taste: a note IN the
    key has exactly one correct spelling, in every key."""
    spelled = set()
    for midi in range(60, 84):
        step, alter, _octave = musicxml.spell_pitch(midi, fifths)
        spelled.add(step + ("#" * alter if alter > 0 else "b" * -alter))
    for name in scale:
        assert name in spelled, f"{name} missing from {sorted(spelled)} at fifths={fifths}"


def test_a_tied_spelling_prefers_the_plain_letter_then_the_flat():
    # C major: A flat rather than G sharp - both need an accidental, so the
    # flat wins.
    assert musicxml.spell_pitch(68, 0)[:2] == ("A", -1)
    # A flat major: E natural rather than F flat - the plain letter wins
    # before the flat preference gets a say.
    assert musicxml.spell_pitch(64, -4)[:2] == ("E", 0)


def test_the_key_signature_is_written_once_as_fifths():
    root = _root(_one_bar([[(4, 0, [(1, 0)])]], fifths=-3))
    assert root.findtext("./part/measure/attributes/key/fifths") == "-3"
    assert len(root.findall("./part/measure/attributes/key")) == 1


def test_the_key_changes_spelling_but_never_the_sounding_pitch():
    """Why a mis-read key signature is cheap: it moves the spelling and
    nothing else."""
    beats = [[(4, 0, [(2, 2)])]]  # second string, second fret: MIDI 61
    sharp = _root(_one_bar(beats, fifths=2))
    flat = _root(_one_bar(beats, fifths=-4))
    (a,) = _notes(sharp)
    (b,) = _notes(flat)
    assert (a.findtext("pitch/step"), a.findtext("pitch/alter")) == ("C", "1")
    assert (b.findtext("pitch/step"), b.findtext("pitch/alter")) == ("D", "-1")
    for note in (a, b):
        step = note.findtext("pitch/step")
        alter = int(note.findtext("pitch/alter"))
        assert musicxml.pitch_midi(step, alter, int(note.findtext("pitch/octave"))) == 61
        assert note.findtext("notations/technical/string") == "2"
        assert note.findtext("notations/technical/fret") == "2"


# ---------------------------------------------------------------------------
# Odds and ends the emitter must not get wrong
# ---------------------------------------------------------------------------


def test_a_hostile_title_is_escaped_not_injected():
    title = 'A & B <script>"x"</script>'
    xml = musicxml.build(title, None, DEFAULT_TUNING, (4, 4), [([[(4, 0, [(1, 0)])]], (4, 4))])
    root = _root(xml)  # would raise if the title broke the document
    assert root.findtext("./work/work-title") == title
    assert "<script>" not in xml


def test_tempo_is_emitted_both_for_engraving_and_for_playback():
    root = _root(musicxml.build("T", 96, DEFAULT_TUNING, (4, 4), [([[(4, 0, [(1, 0)])]], (4, 4))]))
    direction = root.find("./part/measure/direction")
    assert direction.findtext("direction-type/metronome/per-minute") == "96"
    assert direction.find("sound").get("tempo") == "96"


def test_no_tempo_means_no_direction_at_all():
    root = _root(_one_bar([[(4, 0, [(1, 0)])]]))
    assert root.findall("./part/measure/direction") == []


def test_a_tuning_is_required():
    with pytest.raises(ValueError):
        musicxml.build("T", None, [], (4, 4), [([[(4, 0, [(1, 0)])]], (4, 4))])


def test_a_bare_beats_list_is_accepted_as_the_one_voice_case():
    """The shape _build_alphatex accepts, so callers can hand either emitter
    the same measures."""
    xml = musicxml.build("T", None, DEFAULT_TUNING, (4, 4), [[(4, 0, [(1, 0)])]])
    root = _root(xml)
    assert [n.findtext("voice") for n in _notes(root)] == ["1"]


def test_measures_are_numbered_from_one_consecutively():
    measures = [([[(4, 0, [(1, 0)])]], (4, 4))] * 4
    root = _root(musicxml.build("T", None, DEFAULT_TUNING, (4, 4), measures))
    assert [m.get("number") for m in root.findall("./part/measure")] == ["1", "2", "3", "4"]


# ---------------------------------------------------------------------------
# Validation against the real schema
# ---------------------------------------------------------------------------


def _emitted_samples():
    return {
        "monophonic": musicxml.build(
            "Monophonic", 80, DEFAULT_TUNING, (4, 4),
            [([[(4, 0, [(1, 0)]), (4, 0, [(2, 2)]), (4, 0, [(3, 2)]), (4, 0, [(1, 3)])]], (4, 4))],
            fifths=0),
        "two-voice": musicxml.build(
            "Two Voice", None, DEFAULT_TUNING, (3, 4),
            [([[(4, 0, [(1, 0)]), (4, 0, [(2, 1)]), (4, 0, [(1, 3)])],
               [(2, 1, [(5, 0)])]], (3, 4))],
            fifths=1),
        "everything": musicxml.build(
            "Everything", 120, DROP_D, (4, 4), [
                ([[(4, 0, [(6, 0), (5, 2), (4, 2)]), (8, 0, []), (8, 1, [(1, 5)]),
                   (16, 0, [(2, 3)]), (32, 2, [(2, 5)])]], (4, 4)),
                ([[(4, 0, [(1, 12)])], [(4, 0, [(6, 3)])]], (1, 4)),
                ([[(2, 0, [(3, 0)])]], (2, 4)),
            ], fifths=-2, capo=3),
        # Rule 14: a `<forward>` carrying a footnote, in both the leading and
        # the trailing position. Nothing else here emits one, so without this
        # sample the schema never sees the element or its child order.
        "inferred-silence": musicxml.build(
            "Inferred Silence", None, DEFAULT_TUNING, (3, 4), [
                ([[(4, 0, [(1, 0)]), (4, 0, [(1, 2)]), (4, 0, [(1, 3)])],
                  [(4, 0, [(5, 0)]), (2, 0, musicxml.inferred_rest())]], (3, 4)),
                ([[(2, 0, musicxml.inferred_rest()), (4, 0, [(1, 5)])],
                  [(4, 0, [(6, 0)]), (4, 0, [(6, 2)]), (4, 0, [(6, 3)])]], (3, 4)),
            ]),
    }


def test_every_sample_parses_as_xml():
    for name, xml in _emitted_samples().items():
        assert ET.fromstring(xml) is not None, name


def test_validates_against_xsd():
    """Validate against the real MusicXML 4.0 schema.

    Skipped unless FERMATA_MUSICXML_XSD points at a local musicxml.xsd, so CI
    stays hermetic - the schema's own xs:import elements name remote URLs, and
    a test that silently depends on the network is worse than one that says it
    was skipped. Point it at a copy whose imports resolve locally.
    """
    path = os.environ.get("FERMATA_MUSICXML_XSD")
    if not path or not os.path.isfile(path):
        pytest.skip("FERMATA_MUSICXML_XSD not set to a musicxml.xsd")
    lxml_etree = pytest.importorskip("lxml.etree")
    schema = lxml_etree.XMLSchema(lxml_etree.parse(path))
    for name, xml in _emitted_samples().items():
        doc = lxml_etree.fromstring(xml.encode("utf-8"))
        if not schema.validate(doc):
            errors = "; ".join(f"line {e.line}: {e.message}" for e in schema.error_log)
            pytest.fail(f"{name} failed MusicXML 4.0 validation: {errors}")


# ---------------------------------------------------------------------------
# The examples the profile document publishes
# ---------------------------------------------------------------------------


def _example_paths():
    """The example files the profile document publishes.

    They live outside server/, so they are absent from a container image built
    from the server directory alone. Callers skip in that case rather than
    fail - the files are documentation, not part of the installed package.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2] / "docs" / "examples"
    if not root.is_dir():
        pytest.skip("docs/examples is not present in this checkout")
    return sorted(root.glob("*.musicxml"))


def test_the_profile_document_quotes_the_footnote_this_emitter_writes():
    """Rule 14 publishes the footnote text as the words a consumer may match on,
    and that text is hand-copied into the document. Two copies of a string are
    two chances to disagree, and the one in the document is the one a third
    party implements against - so a change here has to fail until the document
    is changed with it."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "docs"
    profile = root / "musicxml-tab-profile.md"
    if not profile.is_file():
        pytest.skip("docs/musicxml-tab-profile.md is not present in this checkout")
    text = profile.read_text(encoding="utf-8")
    assert musicxml.INFERRED_REST_FOOTNOTE in text, (
        "the footnote in musicxml.py is not the one Rule 14 publishes")


def test_the_published_examples_exist_and_conform():
    """docs/musicxml-tab-profile.md publishes these as reference files for
    another implementation to compare against, so they have to keep passing the
    rules the document states - an example that drifts out of conformance is
    worse than no example."""
    paths = _example_paths()
    assert [p.name for p in paths] == ["monophonic.musicxml", "two-voice.musicxml"]
    for path in paths:
        root = ET.parse(path).getroot()
        assert root.tag == "score-partwise"
        assert root.get("version") == "4.0"
        divisions = int(root.findtext("./part/measure/attributes/divisions"))
        beats = int(root.findtext("./part/measure/attributes/time/beats"))
        beat_type = int(root.findtext("./part/measure/attributes/time/beat-type"))
        expected = round(divisions * 4 * beats / beat_type)
        for measure in root.findall("./part/measure"):
            sums = _voice_sums(measure)
            assert sums, path.name
            for voice, total in sums.items():
                assert total == expected, f"{path.name} voice {voice}: {total} != {expected}"
        for note in root.findall("./part/measure/note"):
            if note.find("rest") is not None:
                continue
            assert note.findtext("notations/technical/string") is not None, path.name
            assert note.findtext("notations/technical/fret") is not None, path.name
            octave = int(note.findtext("pitch/octave"))
            assert musicxml.MIN_OCTAVE <= octave <= musicxml.MAX_OCTAVE


def test_the_published_examples_validate_against_xsd():
    path = os.environ.get("FERMATA_MUSICXML_XSD")
    if not path or not os.path.isfile(path):
        pytest.skip("FERMATA_MUSICXML_XSD not set to a musicxml.xsd")
    lxml_etree = pytest.importorskip("lxml.etree")
    schema = lxml_etree.XMLSchema(lxml_etree.parse(path))
    for example in _example_paths():
        doc = lxml_etree.parse(str(example))
        if not schema.validate(doc):
            errors = "; ".join(f"line {e.line}: {e.message}" for e in schema.error_log)
            pytest.fail(f"{example.name} failed MusicXML 4.0 validation: {errors}")
