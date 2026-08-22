"""MusicXML emission for extracted guitar tablature.

This writes ordinary MusicXML 4.0 - no private elements, no container of our
own - following the profile documented in docs/musicxml-tab-profile.md. The
profile exists because MusicXML leaves a good deal of latitude and a tab
score can be spelled several equally valid ways; pinning one down is what
lets another program produce files this reads, and read files this writes.
Rule numbers in the comments below are that document's.

Everything a tab staff needs is already in the specification: `<staff-details>`
carries `<staff-lines>` and one `<staff-tuning>` per string, and each note
carries `<notations><technical><string>` and `<fret>`. Per the schema's own
documentation, "Fret numbers start with 0 for an open string" and "String
numbers start with 1 for the highest pitched full-length string" - the latter
being the convention tabextract._Staff.string_for_y already uses.

STRING NUMBER vs STAFF LINE is the one place two numbering schemes run
opposite each other, and getting it backwards mirrors every note onto the
wrong string while still validating. The schema's `staff-line` type says
"Staff lines are numbered from bottom to top, with 1 being the bottom line on
a staff", and `<staff-tuning line=>` is a staff line - so line 1 is the
LOWEST-pitched string, while `<string>` 1 is the HIGHEST-pitched. On a
six-line staff, line = 7 - string. Fermata's `tuning` lists are ordered
lowest string first, so tuning[i] is line i+1 and no reversal appears here,
even though the alphaTex emitter needs one.

CHILD ORDER MATTERS. `<attributes>`, `<note>`, `<staff-details>` and
`<staff-tuning>` are xs:sequence in the schema, so their children have one
legal order and anything else fails validation outright. The order used below
is the schema's; see _append_note and _append_attributes. (`<notations>` and
`<technical>` are xs:choice and their contents may be in any order.)

PITCH. A tab note's sounding pitch is exact: string plus fret plus tuning is
a MIDI number with nothing inferred. Turning that into MusicXML's
`<step>`/`<alter>`/`<octave>` is the only genuinely under-determined step,
because F sharp and G flat are the same MIDI number and the key signature is
what decides between them. spell_pitch() resolves it on the line of fifths -
see its docstring - from a key signature read off the score's own engraved
accidentals (glyph_rhythm.decode_key_signature), defaulting to no accidentals
where that cannot be read. A wrong key costs an odd enharmonic spelling and
nothing else: the sounding pitch, the fret and the string are unaffected, so
this never makes a note WRONG, only oddly spelled.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

# Divisions per quarter note (Rule 2). Every duration this emitter can
# produce has to come out an integer number of divisions, or the arithmetic a
# consumer does to check Rule 8 stops being exact. The tightest case in
# tabextract's vocabulary is a double-dotted 32nd - an eighth of a quarter
# times 7/4, i.e. 7/32 of a quarter - so 32 is the smallest workable value.
# 480 is used instead because it is what notation programs commonly write, so
# the files look ordinary, and because it also divides by 3 and 5, leaving
# room for the tuplets this profile does not yet cover without having to
# change divisions. The schema's own guidance caps it: "If maximum
# compatibility with Standard MIDI 1.0 files is important, do not have the
# divisions value exceed 16383."
DIVISIONS = 480

# tabextract's duration codes (a code is the note value's denominator: 4 is a
# quarter) to MusicXML `<type>` names.
TYPE_NAMES = {
    1: "whole",
    2: "half",
    4: "quarter",
    8: "eighth",
    16: "16th",
    32: "32nd",
}

# Augmentation dots multiply a note's written value by these.
_DOT_FACTORS = (1.0, 1.5, 1.75)

# General MIDI program 25 is Acoustic Guitar (nylon). MusicXML numbers
# programs from 1, so this is the value as written.
_MIDI_PROGRAM_NYLON_GUITAR = 25

_STEP_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# The line of fifths, as note LETTERS at seven consecutive positions.
# Position n has letter _FIFTHS_LETTERS[(n + 1) % 7] and alter (n + 1) // 7,
# so position -1 is F, 0 is C, 5 is B, 6 is F sharp and -2 is B flat.
_FIFTHS_LETTERS = ("F", "C", "G", "D", "A", "E", "B")

# The spellings spell_pitch will consider, as line-of-fifths positions: F flat
# (-8) through B sharp (12). That is exactly the single-accidental range -
# every one of the twelve pitch classes has at least one spelling inside it,
# and none of them is a double sharp or double flat, so no note ever comes out
# spelled with an accidental a guitarist would not expect.
_FIFTHS_MIN = -8
_FIFTHS_MAX = 12

_PITCH_NAME_RE = re.compile(r"^([A-Ga-g])(#{1,2}|b{1,2}|)(-?\d+)$")

# MusicXML's `positive-divisions` type excludes zero, so a beat that resolved
# to no duration at all cannot be written as <duration>0</duration> - it has
# to be left out entirely. See build().
_MIN_DURATION = 1

# MusicXML's `octave` type is an integer from 0 to 9, so <pitch> simply cannot
# express anything outside roughly MIDI 12 to 131. That matters here because a
# fret number is not always a real fret: two adjacent single-digit frets that
# the PDF rendered as one text span come through as e.g. 78, and 78 semitones
# above a string puts the note past the top of the range. Such a note is
# already known-bad - tabextract counts frets above _MAX_SANE_FRET and warns
# about them - and writing it anyway makes the whole document fail validation,
# which is a far worse outcome than one missing note. See is_representable.
MIN_OCTAVE = 0
MAX_OCTAVE = 9


class InferredRest(list):
    """The `notes` slot of a rest beat that was DEDUCED, not read.

    Any rest beat carries an empty notes list. This marks the subset of them
    that were never printed in the source and exist only because a voice the
    producer DID read notes from came up short of its meter - see tabextract's
    _pad_voice_to_budget. Telling the two apart is the whole point: silence
    invented to balance a bar must not read as silence somebody engraved.

    It subclasses `list` and is always empty, so every existing consumer of the
    beats model that only asks "does this beat have notes" is unaffected, and
    an inferred rest still compares equal to a plain one. Anything that needs
    the distinction asks is_inferred_rest().

    WHAT DESTROYS THE MARK, because it rides on the type rather than on a
    value: it survives copy, deepcopy and pickle, and is lost by slicing
    (`notes[:]`), by `list(notes)`, by concatenation, and by any round trip
    through JSON - all of which hand back a plain list. None of those is reached
    today; the beats model goes straight from the extractor to the two
    emitters. But anything that caches the model as JSON and reads it back would
    turn every inferred rest in the document into an engraved one, silently, and
    nothing here would fail. Such a cache has to carry the mark itself.
    """

    __slots__ = ()


def inferred_rest() -> InferredRest:
    """A fresh notes list marked as inferred silence.

    Fresh rather than one shared module-level instance: it is a list, and a
    single shared mutable empty list is one stray append away from putting the
    same note into every inferred rest in the document.
    """
    return InferredRest()


def is_inferred_rest(notes) -> bool:
    """Whether this beat's notes slot marks silence deduced from the meter."""
    return isinstance(notes, InferredRest)


# What a `<forward>` written for inferred silence says about itself, in words,
# beside the machine-readable fact of being a forward and not a rest. A
# constant because it is part of the published profile (Rule 14), so a consumer
# may match on it - see _append_forward.
INFERRED_REST_FOOTNOTE = (
    "silence deduced from the time signature, not read from a rest printed in "
    "the source"
)


def _fifths_position(n: int) -> tuple[str, int]:
    """The (step, alter) at position n on the line of fifths."""
    return _FIFTHS_LETTERS[(n + 1) % 7], (n + 1) // 7


def spell_pitch(midi: int, fifths: int = 0) -> tuple[str, int, int]:
    """Spell a MIDI note number as MusicXML (step, alter, octave).

    The MIDI number is exact; which of its enharmonic spellings to write is
    not, and the key signature is what settles it. Both are handled on the
    line of fifths, where a key signature IS a position: the diatonic notes of
    a key with `fifths` accidentals occupy positions fifths-1 through
    fifths+5, so the key's centre sits at fifths+2. C major (fifths 0) centres
    on 2, and its seven positions -1..5 are exactly F C G D A E B.

    Each pitch class's spellings are 12 positions apart on that line (twelve
    fifths is seven octaves), so at most two of them fall in the
    single-accidental range considered here, and the one nearer the key's
    centre is the one an engraver writes. In C major, position 7 (C sharp) is
    five steps from the centre against D flat's seven, which is why a
    chromatic C sharp is spelled sharp; E flat, at five against D sharp's
    seven, is spelled flat.

    A tie happens for exactly one pitch class per key - the one a tritone from
    the key's centre, six positions either way - and is broken first toward
    the spelling that needs NO accidental, then toward the flat. Both halves
    matter: in C major the tied pair is A flat against G sharp, both accented,
    and the flat is the conventional reading; in A flat major it is E against
    F flat, and preferring the plain letter is what keeps a perfectly ordinary
    E natural from being written F flat. That two-step tie-break is Rule 13's
    documented default.

    Diatonic notes are never affected by the tie-break: a key's own seven
    notes sit within three positions of its centre and their enharmonic
    partners twelve further out, so distance alone always decides them. Only
    chromatic notes are ever in question, and from FOUR accidentals up the
    nearest-position rule can pick a spelling an engraver would not have
    chosen. E major spells F natural as E sharp, and A flat major spells B
    natural as C flat - which also moves the printed octave, since C flat 5
    and B natural 4 are the same pitch. Three accidentals or fewer never
    produce one. It is the right pitch either way, only oddly written - see
    the module docstring.

    The octave follows from the spelling rather than from the MIDI number
    alone, so MIDI 60 spelled as B sharp is octave 3, not 4.
    """
    pitch_class = midi % 12
    centre = fifths + 2
    best = None
    for n in range(_FIFTHS_MIN, _FIFTHS_MAX + 1):
        if (7 * n) % 12 != pitch_class:
            continue
        rank = (abs(n - centre), abs(_fifths_position(n)[1]), n)
        if best is None or rank < best:
            best = rank
    step, alter = _fifths_position(best[-1])
    octave = (midi - alter - _STEP_SEMITONES[step]) // 12 - 1
    return step, alter, octave


def parse_pitch_name(name) -> tuple[str, int, int]:
    """A tuning entry like "E2", "F#2" or "Eb3" as (step, alter, octave)."""
    m = _PITCH_NAME_RE.match(str(name).strip())
    if not m:
        raise ValueError(f"not a pitch name: {name!r}")
    accidentals = m.group(2)
    alter = len(accidentals) if accidentals.startswith("#") else -len(accidentals)
    return m.group(1).upper(), alter, int(m.group(3))


def pitch_midi(step: str, alter: int, octave: int) -> int:
    return 12 * (octave + 1) + _STEP_SEMITONES[step] + alter


def tuning_midi(name) -> int:
    return pitch_midi(*parse_pitch_name(name))


def open_string_midi(tuning, string: int, capo: int = 0) -> int:
    """The sounding pitch of an open string, by MusicXML string number.

    `tuning` is ordered lowest string first (tabextract.DEFAULT_TUNING), and
    string numbers run the other way - string 1 is the highest-pitched - so
    string n is tuning[len(tuning) - n].

    `<staff-tuning>` records "the open, non-capo tuning", and `<capo>`
    "changes the open tuning of the strings specified by staff-tuning by the
    specified number of half-steps" - so a capo raises the sounding pitch
    without altering the written tuning, and fret numbers stay relative to it.
    """
    if not 1 <= string <= len(tuning):
        raise ValueError(f"string {string} is not on a {len(tuning)}-string instrument")
    return tuning_midi(tuning[len(tuning) - string]) + (capo or 0)


def is_representable(midi: int, fifths: int = 0) -> bool:
    """Whether `<pitch>` can express this note at all (Rule 11).

    Checked on the spelling rather than on the MIDI number, because the octave
    is a property of the spelling: MIDI 131 is B9 and representable, while the
    same pitch spelled C flat would be octave 10 and not.
    """
    return MIN_OCTAVE <= spell_pitch(midi, fifths)[2] <= MAX_OCTAVE


def unrepresentable_notes(measures, tuning, fifths=0, capo=None) -> int:
    """How many notes in these measures have no writable `<pitch>`, and so are
    replaced in the emitted score - see build(). Counted from the same
    predicate the emitter applies, so the report and the file agree.

    `fifths` and `capo` mirror build()'s own parameters, in the same order and
    with the same defaults: both shift which notes are representable at the
    octave boundary, so a caller that passes one to build() and not to this
    would get a count that quietly disagrees with the file.
    """
    tuning = list(tuning) if tuning else []
    if not tuning:
        return 0
    count = 0
    for measure_in in measures:
        beats_in, _ts = _split_measure(measure_in, None)
        for voice in voices_of(beats_in):
            for _code, _dots, notes in voice:
                for string, fret in notes or ():
                    string, fret = int(string), int(fret)
                    if not 1 <= string <= len(tuning):
                        count += 1
                        continue
                    midi = open_string_midi(tuning, string, capo) + fret
                    if not is_representable(midi, fifths):
                        count += 1
    return count


def beat_divisions(duration_code: int, dots: int = 0) -> int:
    """One beat's length in divisions. An integer by construction, provided
    duration_code is in TYPE_NAMES - see DIVISIONS."""
    if not duration_code:
        return 0
    factor = _DOT_FACTORS[min(max(dots, 0), len(_DOT_FACTORS) - 1)]
    return round(DIVISIONS * 4 * factor / duration_code)


def measure_divisions(ts) -> int:
    """One measure's length in divisions for this meter. 3/4 and 6/8 both come
    to three quarters' worth, so the denominator has to be honoured - the
    numerator alone would give a compound meter twice its true length."""
    num, den = ts
    return round(DIVISIONS * 4 * num / den)


def voices_of(beats):
    """A measure's beats as a list of voices, accepting a flat list of beats as
    the one-voice case - the same shape tabextract._build_alphatex takes."""
    if not beats:
        return []
    return list(beats) if isinstance(beats[0], list) else [beats]


def writes_a_note(duration_code, dots, notes) -> bool:
    """Whether build() writes this beat as a `<note>` at all.

    Two kinds of beat produce none. Inferred silence becomes a `<forward>`
    (Rule 14), and a beat that resolves to no duration cannot be written,
    because `<duration>` is positive-divisions. Both are decided here rather
    than by each caller, so a count reported beside the file and the file itself
    cannot come from two different rules - see written_beats and build().
    """
    if is_inferred_rest(notes):
        return False
    return beat_divisions(duration_code, dots) >= _MIN_DURATION


def written_beats(measures) -> int:
    """How many beats the emitted document actually holds.

    This is what `<note>` elements a consumer counts (chord members share their
    beat's onset and carry `<chord>`, so a chord is one beat, not one per note -
    Rule 7). Reported rather than the number of beats extraction produced,
    because those are no longer the same number: silence deduced from the meter
    is emitted as `<forward>` and is not a beat of the score.
    """
    count = 0
    for measure_in in measures:
        beats_in, _ts = _split_measure(measure_in, None)
        for voice in voices_of(beats_in):
            count += sum(1 for beat in voice if writes_a_note(*beat))
    return count


def voice_durations(beats) -> list[int]:
    """Each voice's total length in divisions, in voice order. This is the
    left-hand side of Rule 8, computed from the beats model rather than from
    emitted XML, so a caller can report on a transcription without parsing its
    own output back in.

    Inferred silence (see InferredRest) is NOT counted, because it is not
    written as a note or a rest: build() emits `<forward>` for it, which Rule 8
    does not count either. That is what keeps this equal to the per-voice sums
    a consumer adds up out of the emitted file.
    """
    return [sum(beat_divisions(code, dots) for code, dots, notes in voice
                if not is_inferred_rest(notes))
            for voice in voices_of(beats)]


def _sub(parent, tag, text=None, **attrib):
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrib.items()})
    if text is not None:
        el.text = str(text)
    return el


def _append_note(measure, duration, type_name, dots, voice, string=None,
                 fret=None, midi=None, fifths=0, chord=False):
    """One `<note>`. The schema's sequence for a normal note is: chord?,
    (pitch|unpitched|rest), duration, tie*, instrument*, footnote?, level?,
    voice?, type?, dot*, accidental?, ..., notations* - so voice comes BEFORE
    type, and notations last. A rest is the same shape with `<rest/>` in
    place of `<pitch>` and no `<notations>`.
    """
    note = _sub(measure, "note")
    if string is None:
        _sub(note, "rest")
    else:
        if chord:
            _sub(note, "chord")
        step, alter, octave = spell_pitch(midi, fifths)
        pitch = _sub(note, "pitch")
        _sub(pitch, "step", step)
        # <alter> is omitted rather than written as 0: both are legal, and
        # leaving it out is what notation programs write for a natural.
        if alter:
            _sub(pitch, "alter", alter)
        _sub(pitch, "octave", octave)
    _sub(note, "duration", duration)
    _sub(note, "voice", voice)
    if type_name:
        _sub(note, "type", type_name)
    for _ in range(dots):
        _sub(note, "dot")
    if string is not None:
        notations = _sub(note, "notations")
        technical = _sub(notations, "technical")
        _sub(technical, "string", string)
        _sub(technical, "fret", fret)


def _append_forward(measure, duration, voice):
    """Advance one voice's writing position without writing a note or a rest.

    This is how inferred silence is emitted (Rule 14). `<forward>` moves the
    position exactly as a rest of the same duration would, so the notes after
    it still sound where they should and the measure still lays out - but it is
    not a rest, so it does not claim the source printed one, and Rule 8's sum
    over that voice's notes and rests genuinely falls short. A measure padded
    to look complete would report as conformant to every tool that reads it,
    which is precisely the defect that must stay visible.

    The schema's sequence for forward is duration, footnote?, level?, voice?,
    staff? - so the footnote comes before the voice, not after.
    """
    forward = _sub(measure, "forward")
    _sub(forward, "duration", duration)
    _sub(forward, "footnote", INFERRED_REST_FOOTNOTE)
    _sub(forward, "voice", voice)


def _append_attributes(measure, ts, fifths, tuning, capo, opening):
    """`<attributes>` in the schema's order: divisions, key, time, staves,
    part-symbol, instruments, clef, staff-details. Only the opening measure
    declares divisions, key, clef and tuning; a later measure carries nothing
    but a new `<time>`, which is what a meter change is.
    """
    attributes = _sub(measure, "attributes")
    if opening:
        _sub(attributes, "divisions", DIVISIONS)
        key = _sub(attributes, "key")
        _sub(key, "fifths", fifths)
    if ts:
        time = _sub(attributes, "time")
        _sub(time, "beats", ts[0])
        _sub(time, "beat-type", ts[1])
    if opening:
        clef = _sub(attributes, "clef")
        _sub(clef, "sign", "TAB")
        # <line> is optional for a TAB sign ("only needed with the G, F, and C
        # signs in order to position a pitch correctly"), but the spec's own
        # tablature example writes 5, so emit that rather than invent one.
        _sub(clef, "line", 5)
        details = _sub(attributes, "staff-details")
        # staff-details' sequence is staff-type?, staff-lines, staff-tuning*,
        # capo?, staff-size? - capo after the tunings, not before.
        _sub(details, "staff-lines", len(tuning))
        for index, name in enumerate(tuning):
            step, alter, octave = parse_pitch_name(name)
            st = _sub(details, "staff-tuning", line=index + 1)
            _sub(st, "tuning-step", step)
            if alter:
                _sub(st, "tuning-alter", alter)
            _sub(st, "tuning-octave", octave)
        if capo:
            _sub(details, "capo", capo)
    return attributes


def _append_tempo(measure, tempo):
    direction = _sub(measure, "direction", placement="above")
    dtype = _sub(direction, "direction-type")
    metronome = _sub(dtype, "metronome")
    _sub(metronome, "beat-unit", "quarter")
    _sub(metronome, "per-minute", tempo)
    # <sound> is what a player reads; the <metronome> above it is what gets
    # engraved. Both, so the file looks right and plays right.
    _sub(direction, "sound", tempo=tempo)


def _append_part_list(root, part_id, part_name):
    """score-part's sequence puts part-name first, then score-instrument, then
    midi-instrument - whose id must reference the score-instrument's."""
    part_list = _sub(root, "part-list")
    score_part = _sub(part_list, "score-part", id=part_id)
    _sub(score_part, "part-name", part_name)
    instrument_id = f"{part_id}-I1"
    instrument = _sub(score_part, "score-instrument", id=instrument_id)
    _sub(instrument, "instrument-name", part_name)
    midi = _sub(score_part, "midi-instrument", id=instrument_id)
    _sub(midi, "midi-channel", 1)
    _sub(midi, "midi-program", _MIDI_PROGRAM_NYLON_GUITAR)


def _split_measure(measure_in, in_effect):
    """One entry of `measures` as (beats, meter). A bare list of beats - what a
    caller with no per-measure meter to carry hands over - keeps whatever
    meter is already in effect."""
    if measure_in and isinstance(measure_in, tuple) and len(measure_in) == 2:
        beats_in, measure_ts = measure_in
    else:
        beats_in, measure_ts = measure_in, None
    return beats_in, tuple(measure_ts) if measure_ts else in_effect


def build(title, tempo, tuning, ts, measures, fifths=0, capo=None,
          part_name="Guitar", encoding_date=None):
    """Emit one tab part as a MusicXML 4.0 `score-partwise` document.

    `measures` is a list of (beats, measure_ts) pairs - or bare beats lists,
    for a caller with no per-measure meter to carry - where beats is a list of
    VOICES and each voice a list of (duration_code, dots, notes), notes being
    a list of (string, fret). That is exactly what tabextract produces and
    what _build_alphatex consumes, so the two emitters stay interchangeable.

    Voices are written one after another, each after the first preceded by a
    `<backup>` returning the writing position to the start of the measure
    (Rule 6), and numbered from 1 in the order given - which tabextract orders
    top voice first.

    What this deliberately does NOT do is adjust anything to make the
    arithmetic work. A measure whose voices do not sum to its meter is written
    exactly as extracted, so it fails Rule 8 and any MusicXML tool will say
    so. Padding it here would hide a defect the extractor already counts and
    reports, and the whole point of emitting a standard format is that
    somebody else's tool can find those.

    A beat whose notes are an InferredRest is silence the extractor deduced
    from the meter rather than read from the page. It is written as
    `<forward>`, not as a rest (Rule 14): the position advances so the rest of
    the voice still sounds in the right place, while the measure's notes and
    rests genuinely fall short of its meter, so a Rule 8 check by any consumer
    reports the same defect this producer reports.

    The one thing it does refuse to write is a pitch MusicXML has no way to
    express - see is_representable, and unrepresentable_notes for the count a
    caller should report.

    No DOCTYPE is written. The MusicXML DTDs are deprecated as of version 4.0,
    the public DTD URL no longer resolves, and secure-by-default XML parsers
    refuse a document that carries an external DTD reference at all - so a
    DOCTYPE line costs interoperability instead of buying it. The
    xsi:noNamespaceSchemaLocation hint below is the current self-describing
    form and is inert for readers that do not validate.
    """
    tuning = list(tuning) if tuning else []
    if not tuning:
        raise ValueError("a tab part needs a tuning: one entry per string, lowest first")
    part_id = "P1"

    root = ET.Element("score-partwise", {
        "version": "4.0",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:noNamespaceSchemaLocation": "http://www.musicxml.org/xsd/musicxml.xsd",
    })
    # score-partwise' header is a sequence: work?, movement-number?,
    # movement-title?, identification?, defaults?, credit*, part-list.
    if title:
        work = _sub(root, "work")
        _sub(work, "work-title", title)
    identification = _sub(root, "identification")
    encoding = _sub(identification, "encoding")
    _sub(encoding, "software", "Fermata")
    _sub(encoding, "encoding-date", encoding_date or date.today().isoformat())
    _append_part_list(root, part_id, part_name)

    part = _sub(root, "part", id=part_id)
    in_effect = tuple(ts) if ts else None
    for index, measure_in in enumerate(measures):
        beats_in, measure_ts = _split_measure(measure_in, in_effect)
        measure = _sub(part, "measure", number=index + 1)
        opening = index == 0
        if opening or measure_ts != in_effect:
            _append_attributes(measure, measure_ts, fifths, tuning, capo, opening)
            in_effect = measure_ts
        if opening and tempo:
            _append_tempo(measure, tempo)

        written = 0
        for voice_number, voice in enumerate(voices_of(beats_in), start=1):
            if voice_number > 1 and written:
                backup = _sub(measure, "backup")
                _sub(backup, "duration", written)
            written = 0
            for duration_code, dots, notes in voice:
                duration = beat_divisions(duration_code, dots)
                if duration < _MIN_DURATION:
                    # <duration> is positive-divisions, so there is no way to
                    # write a zero-length beat. Nothing sounds for no time
                    # either, so dropping it loses nothing.
                    continue
                if not writes_a_note(duration_code, dots, notes):
                    # Everything writes_a_note rejects EXCEPT the zero-duration
                    # case, which the guard above has already skipped: silence
                    # this producer deduced rather than read. Written as
                    # `<forward>`, which holds the position without asserting a
                    # rest - see _append_forward and Rule 14. The predicate is
                    # shared with written_beats, so the beat count reported
                    # beside the file and the file itself cannot disagree.
                    _append_forward(measure, duration, voice_number)
                    written += duration
                    continue
                type_name = TYPE_NAMES.get(duration_code)
                dot_count = min(max(dots, 0), len(_DOT_FACTORS) - 1)
                # Notes whose pitch `<pitch>` cannot express are left out
                # (Rule 11). A beat that loses ALL of its notes that way still
                # keeps its place, as a rest of the same length: dropping the
                # beat outright would shorten its voice and break Rule 8 for
                # the whole measure, which turns one unwritable note into a
                # measure that reads as defective.
                writable = []
                for string, fret in notes or ():
                    string, fret = int(string), int(fret)
                    if not 1 <= string <= len(tuning):
                        continue
                    midi = open_string_midi(tuning, string, capo) + fret
                    if is_representable(midi, fifths):
                        writable.append((string, fret, midi))
                if not writable:
                    _append_note(measure, duration, type_name, dot_count, voice_number)
                else:
                    for position, (string, fret, midi) in enumerate(writable):
                        _append_note(
                            measure, duration, type_name, dot_count, voice_number,
                            string=string, fret=fret, midi=midi,
                            fifths=fifths, chord=position > 0,
                        )
                written += duration

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'
