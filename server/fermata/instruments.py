"""Instrument definitions: what a player has in hand.

A definition is a name, whether the instrument is fretted, how many strings it
has and what each of them is tuned to, how many frets where that applies, and
the reference pitch the tuning is measured against. The same physical guitar in
standard tuning and in dropped D is two definitions, because a tuning is what
everything downstream actually needs to know.

STRING ORDER follows the rest of the codebase (tabextract.DEFAULT_TUNING,
musicxml.open_string_midi): the list runs from the highest string NUMBER to the
lowest, so entry 0 is a guitar's string 6 and its string 1 is last. That is not
the same as "in ascending pitch", and must not be validated as though it were -
a ukulele's reentrant GCEA tunes string 4 above string 3, and a player is free
to invent worse.

FRETS are the dividing line. A fret is a discrete, nameable position, so
position reasoning and tablature only mean anything on a fretted instrument. An
unfretted one still needs the definition - for pitch, for playback, for range,
and for the audible reference that stands in for a fret - but nothing that
computes positions may run against it. That is why fret_count and capo are
rejected outright rather than quietly ignored on an unfretted definition: a
fret count stored on a violin is a fret count something downstream will
eventually believe.

CAPO is stored but is deliberately NOT part of what is stored per string. What
`string_pitches` holds is the nominal, open, non-capo tuning, which is what
MusicXML's `<staff-tuning>` records and what a player is working from. The capo
enters at the point of use: it raises every string, so it decides what the
instrument actually SOUNDS, and therefore what the audition plays and what a
player hears when checking a tuning. Sounding an open E while a capo sits at
the fifth fret would teach a reference wrong by five semitones, which is worse
than offering no audition at all. See string_details, which computes both.
"""

import re

from . import musicxml

# What kind of instrument a definition describes. Only "string" is implemented -
# string_count, string_pitches and `fretted` all presuppose it - and the field
# exists anyway because `fretted: False` means an unfretted STRING instrument (a
# violin), not "not a string instrument", and would otherwise get pressed into
# service as though it did. See db.py's schema comment.
VALID_KINDS = {"string"}
DEFAULT_KIND = "string"

MAX_NAME_CHARS = 80
# The ceiling on the RAW name, before control characters are stripped and runs
# of whitespace collapsed. MAX_NAME_CHARS is the real limit and is applied to
# the cleaned name; this exists only so an absurd payload is refused before any
# of that work happens, and is generous enough that cleaning can still bring a
# legitimate name under the limit.
MAX_RAW_NAME_CHARS = 1000

MIN_STRINGS = 1
MAX_STRINGS = 24

MIN_FRETS = 1
MAX_FRETS = 36

# Reference pitches in real use run from about 392 Hz (French Baroque) through
# 415 and 430 to the 466 Hz of some Venetian instruments, with modern orchestras
# sitting a little above 440. These bounds are wider than that on both sides;
# they exist to reject a typo or a zero, not to legislate anyone's tuning.
MIN_REFERENCE_HZ = 300.0
MAX_REFERENCE_HZ = 600.0
DEFAULT_REFERENCE_HZ = 440.0

# The range a pitch has to fall in to be both playable and writable. A note
# outside MIDI's range cannot be sounded by the synthesiser the tuning is
# checked against, which is most of what a definition is for. The floor is
# tighter than MIDI's own: MIDI starts at C-1, but MusicXML's `octave` type
# starts at 0, so a tuning stored below C0 could never be written out by the
# emitter that will eventually read it - hence deriving it from
# musicxml.MIN_OCTAVE rather than restating 12. MAX_MIDI is MIDI's own ceiling,
# G9, which is comfortably inside musicxml.MAX_OCTAVE.
MIN_MIDI = 12 * (musicxml.MIN_OCTAVE + 1)
MAX_MIDI = 127

# Concert A - the note a reference pitch names.
REFERENCE_MIDI = 69

# Presets carry no string_count: it is derived from string_pitches wherever one
# is read, so the two can never be written out of agreement here. Fret counts
# are the ordinary case for each instrument rather than the only one - a player
# who picks a preset is expected to adjust it.
PRESETS = [
    {
        "key": "guitar-standard",
        "name": "Guitar (standard)",
        "fretted": True,
        "string_pitches": ["E2", "A2", "D3", "G3", "B3", "E4"],
        "fret_count": 22,
    },
    {
        "key": "guitar-drop-d",
        "name": "Guitar (dropped D)",
        "fretted": True,
        "string_pitches": ["D2", "A2", "D3", "G3", "B3", "E4"],
        "fret_count": 22,
    },
    {
        "key": "guitar-dadgad",
        "name": "Guitar (DADGAD)",
        "fretted": True,
        "string_pitches": ["D2", "A2", "D3", "G3", "A3", "D4"],
        "fret_count": 22,
    },
    {
        "key": "guitar-open-g",
        "name": "Guitar (open G)",
        "fretted": True,
        "string_pitches": ["D2", "G2", "D3", "G3", "B3", "D4"],
        "fret_count": 22,
    },
    {
        "key": "guitar-seven-string",
        "name": "Seven-string guitar",
        "fretted": True,
        "string_pitches": ["B1", "E2", "A2", "D3", "G3", "B3", "E4"],
        "fret_count": 24,
    },
    {
        "key": "bass-four-string",
        "name": "Bass (four string)",
        "fretted": True,
        "string_pitches": ["E1", "A1", "D2", "G2"],
        "fret_count": 24,
    },
    {
        "key": "bass-five-string",
        "name": "Bass (five string)",
        "fretted": True,
        "string_pitches": ["B0", "E1", "A1", "D2", "G2"],
        "fret_count": 24,
    },
    {
        # Reentrant: string 4 is the G above string 3's C, which is why the
        # list is not in ascending pitch. See STRING ORDER above.
        "key": "ukulele",
        "name": "Ukulele (soprano)",
        "fretted": True,
        "string_pitches": ["G4", "C4", "E4", "A4"],
        "fret_count": 12,
    },
    {
        "key": "violin",
        "name": "Violin",
        "fretted": False,
        "string_pitches": ["G3", "D4", "A4", "E5"],
        "fret_count": None,
    },
    {
        "key": "viola",
        "name": "Viola",
        "fretted": False,
        "string_pitches": ["C3", "G3", "D4", "A4"],
        "fret_count": None,
    },
    {
        "key": "cello",
        "name": "Cello",
        "fretted": False,
        "string_pitches": ["C2", "G2", "D3", "A3"],
        "fret_count": None,
    },
]


def presets() -> list[dict]:
    """The presets as a client sees them, with the strings each one implies
    already worked out at the default reference pitch - so picking one shows
    real note names and frequencies before anything is saved."""
    out = []
    for preset in PRESETS:
        pitches = list(preset["string_pitches"])
        out.append(
            {
                **preset,
                "kind": DEFAULT_KIND,
                "string_pitches": pitches,
                "string_count": len(pitches),
                "capo": 0 if preset["fretted"] else None,
                "reference_pitch": DEFAULT_REFERENCE_HZ,
                "strings": string_details(pitches, DEFAULT_REFERENCE_HZ, 0),
            }
        )
    return out


def frequency(midi: int, reference_hz: float = DEFAULT_REFERENCE_HZ) -> float:
    """The sounding frequency of a MIDI note under a given reference pitch.

    Twelve-tone equal temperament: a semitone is the twelfth root of two, and
    the reference pitch names concert A, so moving the reference moves the whole
    scale with it. That is the entire reason it is stored per instrument rather
    than assumed - at A415 a guitar's low E sounds 77.72 Hz, not 82.41.

    Returned unrounded. Rounding here would put a second opinion about
    precision between this and whatever displays the number; there is exactly
    one place that formats a frequency for a person to read, and it is not here.
    """
    return reference_hz * 2 ** ((midi - REFERENCE_MIDI) / 12)


def pitch_name(step: str, alter: int, octave: int) -> str:
    """A parsed pitch back as a name. One spelling per (step, alter, octave), so
    what gets stored does not depend on how it was typed."""
    accidental = "#" * alter if alter > 0 else "b" * -alter
    return f"{step}{accidental}{octave}"


def spell_midi(midi: int) -> str:
    """A MIDI note as a pitch name ("A2", "F#3").

    Spelled through musicxml.spell_pitch with no key signature, so the sounding
    pitch under a capo is named the way the emitter would eventually write it
    rather than by a second table of accidentals kept in step by hand.
    """
    return pitch_name(*musicxml.spell_pitch(midi))


def string_details(string_pitches, reference_hz: float, capo: int | None = 0) -> list[dict]:
    """Each string's number, its nominal tuning, and what it actually sounds.

    Two pitches per string, because a capo makes them two different questions:

    - `pitch`/`midi`/`frequency` are NOMINAL - the open, non-capo tuning, which
      is what is stored, what `<staff-tuning>` records, and the tuning a player
      is working from.
    - `sounding_*` is what comes out of the instrument. This is what the
      audition has to play and what a player matches by ear, because a capo
      raises every string.

    With no capo the two collapse, which is the ordinary case. String numbers
    run opposite to list order - see STRING ORDER.
    """
    count = len(string_pitches)
    capo = capo or 0
    details = []
    for index, pitch in enumerate(string_pitches):
        midi = musicxml.tuning_midi(pitch)
        sounding = midi + capo
        details.append(
            {
                "number": count - index,
                "pitch": pitch,
                "midi": midi,
                "frequency": frequency(midi, reference_hz),
                "sounding_pitch": spell_midi(sounding),
                "sounding_midi": sounding,
                "sounding_frequency": frequency(sounding, reference_hz),
            }
        )
    return details


# C0 and C1 control characters. A NUL or an embedded newline is not part of an
# instrument's name, and stored verbatim it travels into every place the name is
# later shown or logged.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _clean_name(name) -> str:
    """Controls removed, runs of whitespace collapsed, ends trimmed. Done before
    the length check, so the limit applies to what is actually stored."""
    without_controls = _CONTROL_CHARS.sub("", str(name or ""))
    return re.sub(r"\s+", " ", without_controls).strip()


def normalise(
    *,
    kind,
    name,
    fretted,
    string_count,
    string_pitches,
    fret_count,
    capo,
    reference_pitch,
) -> dict:
    """Check a definition and return the canonical values to store.

    Raises ValueError with a message meant for a person. Every write goes
    through here - create and update apply the same rules, because an
    instrument edited into an impossible state is no better than one created
    that way.
    """
    kind = kind or DEFAULT_KIND
    if kind not in VALID_KINDS:
        raise ValueError(
            f"kind must be one of {sorted(VALID_KINDS)} - nothing else is implemented yet"
        )

    name = _clean_name(name)
    if not name:
        raise ValueError("name is required")
    if len(name) > MAX_NAME_CHARS:
        raise ValueError(f"name must be at most {MAX_NAME_CHARS} characters")

    fretted = bool(fretted)

    if not MIN_STRINGS <= string_count <= MAX_STRINGS:
        raise ValueError(f"string_count must be between {MIN_STRINGS} and {MAX_STRINGS}")

    # Frets and capo are settled BEFORE the strings, because the capo raises
    # every string and so is part of each one's sounding pitch - which is the
    # pitch the range check below has to be applied to.
    if fretted:
        if fret_count is None:
            raise ValueError("fret_count is required on a fretted instrument")
        if not MIN_FRETS <= fret_count <= MAX_FRETS:
            raise ValueError(f"fret_count must be between {MIN_FRETS} and {MAX_FRETS}")
        capo = 0 if capo is None else capo
        if not 0 <= capo <= fret_count:
            raise ValueError(f"capo must be between 0 and the fret count ({fret_count})")
    else:
        # Rejected, not ignored: see the module docstring. Naming the field in
        # the message matters, because the usual way to arrive here is picking
        # a fretted preset, switching it to unfretted and leaving the fret
        # count behind.
        if fret_count is not None:
            raise ValueError("fret_count does not apply to an unfretted instrument")
        # A capo of zero is not a claim that a violin has a capo - it is a
        # client that always sends the field saying "no capo". Only an actual
        # fret position is contradictory.
        if capo not in (None, 0):
            raise ValueError("capo does not apply to an unfretted instrument")
        capo = None

    pitches = [str(p).strip() for p in (string_pitches or [])]
    if len(pitches) != string_count:
        raise ValueError(
            f"string_count is {string_count} but {len(pitches)} string pitch(es) were given"
        )
    canonical = []
    for index, pitch in enumerate(pitches):
        number = string_count - index
        try:
            step, alter, octave = musicxml.parse_pitch_name(pitch)
        except ValueError:
            raise ValueError(
                f"string {number}: {pitch!r} is not a pitch name (E2, F#2, Eb3)"
            ) from None
        midi = musicxml.pitch_midi(step, alter, octave)
        if not MIN_MIDI <= midi <= MAX_MIDI:
            raise ValueError(f"string {number}: {pitch} is outside the playable range")
        # The capo's contribution is checked here rather than left to the
        # separate 0 <= capo <= fret_count bound, which says nothing about
        # where the capo puts a string that is already near the top.
        if capo and not MIN_MIDI <= midi + capo <= MAX_MIDI:
            raise ValueError(
                f"string {number}: {pitch} with a capo at fret {capo} "
                "sounds outside the playable range"
            )
        # Stored as one spelling per pitch, not as typed: "e2" and "E2" are the
        # same string, and storing the first means the editor and the summary
        # render it lowercase and any later comparison by name - against a
        # preset, or against tabextract.DEFAULT_TUNING - misses. The choice of
        # ACCIDENTAL is kept as written, because E flat and D sharp are a real
        # distinction to whoever typed one.
        canonical.append(pitch_name(step, alter, octave))

    if not MIN_REFERENCE_HZ <= reference_pitch <= MAX_REFERENCE_HZ:
        raise ValueError(
            f"reference_pitch must be between {MIN_REFERENCE_HZ} and {MAX_REFERENCE_HZ} Hz"
        )

    return {
        "kind": kind,
        "name": name,
        "fretted": fretted,
        "string_count": string_count,
        "string_pitches": canonical,
        "fret_count": fret_count if fretted else None,
        "capo": capo if fretted else None,
        "reference_pitch": float(reference_pitch),
    }
