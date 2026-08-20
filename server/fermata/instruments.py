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
"""

from . import musicxml

MAX_NAME_CHARS = 80

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

# A string outside MIDI's own range cannot be sounded by the synthesiser the
# tuning is checked against, which is most of what a definition is for.
MIN_MIDI = 0
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
                "string_pitches": pitches,
                "string_count": len(pitches),
                "capo": 0 if preset["fretted"] else None,
                "reference_pitch": DEFAULT_REFERENCE_HZ,
                "strings": string_details(pitches, DEFAULT_REFERENCE_HZ),
            }
        )
    return out


def frequency(midi: int, reference_hz: float = DEFAULT_REFERENCE_HZ) -> float:
    """The sounding frequency of a MIDI note under a given reference pitch.

    Twelve-tone equal temperament: a semitone is the twelfth root of two, and
    the reference pitch names concert A, so moving the reference moves the whole
    scale with it. That is the entire reason it is stored per instrument rather
    than assumed - at A415 a guitar's low E sounds 77.78 Hz, not 82.41.
    """
    return reference_hz * 2 ** ((midi - REFERENCE_MIDI) / 12)


def string_details(string_pitches, reference_hz: float) -> list[dict]:
    """Each string's number, pitch name, MIDI note and sounding frequency.

    Computed here rather than in the browser so that the note name a player
    reads and the frequency beside it can never come from two different pieces
    of arithmetic. String numbers run opposite to list order - see STRING ORDER.
    """
    count = len(string_pitches)
    details = []
    for index, pitch in enumerate(string_pitches):
        midi = musicxml.tuning_midi(pitch)
        details.append(
            {
                "number": count - index,
                "pitch": pitch,
                "midi": midi,
                "frequency": round(frequency(midi, reference_hz), 3),
            }
        )
    return details


def normalise(
    *,
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
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    if len(name) > MAX_NAME_CHARS:
        raise ValueError(f"name must be at most {MAX_NAME_CHARS} characters")

    fretted = bool(fretted)

    if not MIN_STRINGS <= string_count <= MAX_STRINGS:
        raise ValueError(f"string_count must be between {MIN_STRINGS} and {MAX_STRINGS}")

    pitches = [str(p).strip() for p in (string_pitches or [])]
    if len(pitches) != string_count:
        raise ValueError(
            f"string_count is {string_count} but {len(pitches)} string pitch(es) were given"
        )
    canonical = []
    for index, pitch in enumerate(pitches):
        try:
            step, alter, octave = musicxml.parse_pitch_name(pitch)
        except ValueError:
            raise ValueError(
                f"string {string_count - index}: {pitch!r} is not a pitch name (E2, F#2, Eb3)"
            ) from None
        midi = musicxml.pitch_midi(step, alter, octave)
        if not MIN_MIDI <= midi <= MAX_MIDI:
            raise ValueError(
                f"string {string_count - index}: {pitch} is outside the playable range"
            )
        canonical.append(pitch)

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
        if capo is not None:
            raise ValueError("capo does not apply to an unfretted instrument")

    if not MIN_REFERENCE_HZ <= reference_pitch <= MAX_REFERENCE_HZ:
        raise ValueError(
            f"reference_pitch must be between {MIN_REFERENCE_HZ} and {MAX_REFERENCE_HZ} Hz"
        )

    return {
        "name": name,
        "fretted": fretted,
        "string_count": string_count,
        "string_pitches": canonical,
        "fret_count": fret_count if fretted else None,
        "capo": capo if fretted else None,
        "reference_pitch": float(reference_pitch),
    }
