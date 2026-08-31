"""Per-attempt results from a fretboard drill (issue #27, building on the neck
component of issue #25) - structured rows, not a free-text note.

docs/practice-data.md has always said the per-attempt schema would wait for a
second trainer to decide what the unit is: a position, an interval, a pitch.
Fret to note is that second trainer (ear training, the first, still logs only
a `note` string - see EarTraining.svelte). What it decided: the unit is one
QUESTION, and every question - whichever direction it was asked in - reduces
to the same two facts, a note being tested and a note the answer named. See
db.py's own comment on trainer_attempts for why that lets one `correct` rule
cover both directions without this module ever needing to know a tuning.

Nothing here is a session. `practice_sessions` still carries the drill's
TIME, exactly the way ear_training does (api.log_session, activity
'fretboard') - this module only validates and stores the per-question detail
that sits beside it. See api.py's /trainer/attempts routes for where the two
meet.
"""

from typing import Any

# The one drill that writes here today. A widened tuple, not a migration, is
# how a second one (#26, #29) arrives - the table itself is drill-agnostic
# already (db.py's _TRAINER_ATTEMPTS_COLUMNS).
DRILLS = ("fret_to_note",)

# Which way the question ran. 'position_to_note': a position was shown, a
# note was named. 'note_to_position': a note was named, a position was
# tapped. See the module docstring for why both reduce to the same grading
# rule.
DIRECTIONS = ("position_to_note", "note_to_position")

# The twelve pitch classes an attempt may name - one sharp OR flat per class,
# never both, and never an octave. This is character-for-character the table
# web/src/lib/trainer/neck.js's pitchClass spells (which is itself derived
# from pitch.js's spellMidi, so the two cannot drift into different
# spellings for the same MIDI note without a test somewhere failing to
# notice). Fixing the set here, rather than accepting any string a client
# calls a note, is what keeps "which notes get missed" a GROUP BY instead of
# a query that first has to fold "Db" and "C#" together.
PITCH_CLASSES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")

# Generous bounds on a position, wide enough for anything instruments.py
# would accept (MIN/MAX_STRINGS is 1-24, MIN/MAX_FRETS is 1-36) plus fret 0
# for an open string, without importing that module just to restate its two
# numbers - a trainer attempt is checked against the QUESTION it answered,
# never against a live instrument row, so there is no definition here to stay
# in lockstep with.
MIN_STRING_NUMBER = 1
MAX_STRING_NUMBER = 24
MIN_FRET = 0
MAX_FRET = 36

# How long a response may claim to have taken. Milliseconds, optional, and
# purely informational - nothing here computes from it - so the bound exists
# only to catch a units mistake (a value in seconds, or a timestamp) rather
# than to mean anything about how fast is fast. Ten minutes is far longer
# than anyone deliberating one fretboard question.
MAX_RESPONSE_MS = 10 * 60 * 1000


def _position(string_value, fret_value, *, string_field: str, fret_field: str):
    """A (string, fret) pair, both present or both absent. Raises ValueError
    naming whichever field is the problem.

    Never ONE of the two: a position with a string but no fret, or the
    reverse, is not a partial position, it is not a position - and letting
    one arrive alone would mean some rows answer "was a position given" with
    a value that depends on which column happens to be non-null.
    """
    if string_value is None and fret_value is None:
        return None, None
    if string_value is None or fret_value is None:
        raise ValueError(f"{string_field} and {fret_field} must both be given, or neither")
    if isinstance(string_value, bool) or not isinstance(string_value, int):
        raise ValueError(f"{string_field} must be a whole number")
    if isinstance(fret_value, bool) or not isinstance(fret_value, int):
        raise ValueError(f"{fret_field} must be a whole number")
    if not MIN_STRING_NUMBER <= string_value <= MAX_STRING_NUMBER:
        raise ValueError(
            f"{string_field} must be between {MIN_STRING_NUMBER} and {MAX_STRING_NUMBER}"
        )
    if not MIN_FRET <= fret_value <= MAX_FRET:
        raise ValueError(f"{fret_field} must be between {MIN_FRET} and {MAX_FRET}")
    return string_value, fret_value


def _note(value, field: str) -> str:
    if value not in PITCH_CLASSES:
        raise ValueError(f"{field} must be one of {list(PITCH_CLASSES)}")
    return value


def normalise_attempt(
    *,
    drill,
    direction,
    target_string=None,
    target_fret=None,
    target_note,
    given_string=None,
    given_fret=None,
    given_note,
    response_ms=None,
) -> dict[str, Any]:
    """Check one attempt and return the row to store, `correct` included.

    Raises ValueError with a message meant for a person. `correct` is
    computed here and is NEVER accepted from a caller - see the module
    docstring for why `given_note == target_note` is the whole of the rule,
    the same rule regardless of which direction the question was asked in.
    """
    if drill not in DRILLS:
        raise ValueError(f"drill must be one of {list(DRILLS)}")
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {list(DIRECTIONS)}")

    target_string, target_fret = _position(
        target_string, target_fret, string_field="target_string", fret_field="target_fret"
    )
    given_string, given_fret = _position(
        given_string, given_fret, string_field="given_string", fret_field="given_fret"
    )
    target_note = _note(target_note, "target_note")
    given_note = _note(given_note, "given_note")

    if direction == "position_to_note":
        # The question NAMED a position; the answer named a note. A tap on
        # the neck answers the other direction, not this one.
        if target_string is None:
            raise ValueError(
                "position_to_note needs target_string and target_fret - the position asked about"
            )
        if given_string is not None:
            raise ValueError(
                "position_to_note is answered with a note, not a tapped position - "
                "given_string/given_fret must be omitted"
            )
    else:
        # note_to_position: the question named a note only. There is no
        # single target position to record - see db.py's comment on why
        # target_string/target_fret stay NULL here - and the answer must be
        # an actual tap, not a note choice.
        if target_string is not None:
            raise ValueError(
                "note_to_position has no single target position - "
                "target_string/target_fret must be omitted"
            )
        if given_string is None:
            raise ValueError(
                "note_to_position is answered by tapping a position - "
                "given_string and given_fret are required"
            )

    if response_ms is not None:
        if isinstance(response_ms, bool) or not isinstance(response_ms, int):
            raise ValueError("response_ms must be a whole number")
        if not 0 <= response_ms <= MAX_RESPONSE_MS:
            raise ValueError(f"response_ms must be between 0 and {MAX_RESPONSE_MS}")

    return {
        "drill": drill,
        "direction": direction,
        "target_string": target_string,
        "target_fret": target_fret,
        "target_note": target_note,
        "given_string": given_string,
        "given_fret": given_fret,
        "given_note": given_note,
        # The one place this row's verdict is decided. Not stored from
        # anywhere else and not trusted from a request body.
        "correct": given_note == target_note,
        "response_ms": response_ms,
    }


def attempt_dict(row) -> dict:
    """One attempt as the API presents it - the stored row, with `correct`
    turned back into a real bool (SQLite hands integers back)."""
    d = dict(row)
    d["correct"] = bool(d["correct"])
    return d
