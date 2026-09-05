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

import json
import re
from typing import Any

# The one drill that writes here today. A widened tuple, not a migration, is
# how a second one arrives - the table itself is drill-agnostic
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


# ---------------------------------------------------------------------------
# Chord flash cards (issue #28) - its own attempt shape, in
# trainer_chord_attempts rather than trainer_attempts. See db.py's comment
# on that table for why a chord (a SET of notes) does not fit the single
# pitch-class columns above, which the first paragraph of this module's own
# docstring already commits target_note/given_note to.
# ---------------------------------------------------------------------------

# A widened tuple, the same way DRILLS above is meant to widen (see that
# constant's own comment) - just in this table instead, since a chord drill
# is not a fret-to-note-shaped one.
CHORD_DRILLS = ("chord_flashcards",)

# shape_to_name: a fingering was shown, a chord name was chosen.
# name_to_shape: a chord was named, a shape was tapped.
CHORD_DIRECTIONS = ("shape_to_name", "name_to_shape")

# Interval steps from the root, in semitones - a triad for major and minor,
# a tetrad for the one seventh chord this drill names ("majors and minors
# first, then sevenths, then barre chords" - issue #28's own ordering).
# MIRRORED, CHARACTER FOR CHARACTER, in web/src/lib/trainer/chord-
# theory.js's QUALITIES - the same discipline PITCH_CLASSES above is held
# to against neck.js's table of the same name. server/tests/
# test_chord_theory.py and web/tests/unit/chord-theory.spec.js each check
# every one of the 36 (root, quality) chords this can build; a drift
# between the two would otherwise show up as a shape and its label
# disagreeing about what chord is on screen, not as a failing test.
CHORD_QUALITIES = {
    "major": (0, 4, 7),
    "minor": (0, 3, 7),
    "dominant7": (0, 4, 7, 10),
}


def chord_tones(root: str, quality: str) -> tuple[str, ...] | None:
    """The pitch classes a chord is built from, or None for an unknown root
    or quality - the server's own copy of chord-theory.js's chordTones,
    used to grade an attempt independently of whatever a client claims a
    shape or a chosen name sounds like."""
    if root not in PITCH_CLASSES or quality not in CHORD_QUALITIES:
        return None
    i = PITCH_CLASSES.index(root)
    return tuple(PITCH_CLASSES[(i + step) % 12] for step in CHORD_QUALITIES[quality])


def _chord_quality(value, field: str) -> str:
    if value not in CHORD_QUALITIES:
        raise ValueError(f"{field} must be one of {list(CHORD_QUALITIES)}")
    return value


def _shape(value, field: str, *, allow_empty: bool = True):
    """A list of {string, fret} positions - a fingering shown, or one
    tapped - validated the same bounds `_position` above checks a single
    position against. None passes through as None (the field was not
    given); anything else must be a (possibly empty, unless `allow_empty`
    is False) list of whole-number positions within bounds."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of positions")
    if not value and not allow_empty:
        raise ValueError(f"{field} must not be empty")
    out = []
    for entry in value:
        string_value = entry.get("string") if isinstance(entry, dict) else getattr(entry, "string", None)
        fret_value = entry.get("fret") if isinstance(entry, dict) else getattr(entry, "fret", None)
        if isinstance(string_value, bool) or not isinstance(string_value, int):
            raise ValueError(f"{field} entries need a whole-number string")
        if isinstance(fret_value, bool) or not isinstance(fret_value, int):
            raise ValueError(f"{field} entries need a whole-number fret")
        if not MIN_STRING_NUMBER <= string_value <= MAX_STRING_NUMBER:
            raise ValueError(f"{field} string must be between {MIN_STRING_NUMBER} and {MAX_STRING_NUMBER}")
        if not MIN_FRET <= fret_value <= MAX_FRET:
            raise ValueError(f"{field} fret must be between {MIN_FRET} and {MAX_FRET}")
        out.append({"string": string_value, "fret": fret_value})
    return out


def _notes_list(value, field: str):
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of pitch classes")
    return [_note(n, field) for n in value]


def normalise_chord_attempt(
    *,
    drill,
    direction,
    target_root,
    target_quality,
    target_shape=None,
    given_root=None,
    given_quality=None,
    given_notes=None,
    given_shape=None,
    response_ms=None,
) -> dict[str, Any]:
    """Check one chord attempt and return the row to store, `correct`
    included.

    Raises ValueError with a message meant for a person. `correct` is
    computed here, from TONE SETS - never accepted from a caller, and never
    decided by comparing root/quality strings, so two different (root,
    quality) pairs that happened to name the same notes would still grade
    as the same chord (see chord-theory.js's chordsMatch for why that
    matters more than it looks like it should).
    """
    if drill not in CHORD_DRILLS:
        raise ValueError(f"drill must be one of {list(CHORD_DRILLS)}")
    if direction not in CHORD_DIRECTIONS:
        raise ValueError(f"direction must be one of {list(CHORD_DIRECTIONS)}")

    target_root = _note(target_root, "target_root")
    target_quality = _chord_quality(target_quality, "target_quality")
    target_tones = set(chord_tones(target_root, target_quality) or ())

    target_shape = _shape(target_shape, "target_shape", allow_empty=False)
    given_shape = _shape(given_shape, "given_shape")

    if direction == "shape_to_name":
        # The question NAMED a shape (shown a real fingering); the answer
        # named a chord. Tapping a shape answers the OTHER direction.
        if target_shape is None:
            raise ValueError("shape_to_name needs target_shape - the fingering that was shown")
        if given_shape is not None or given_notes is not None:
            raise ValueError(
                "shape_to_name is answered with a chord name, not a shape - "
                "given_notes/given_shape must be omitted"
            )
        if given_root is None or given_quality is None:
            raise ValueError(
                "shape_to_name needs given_root and given_quality - the chord that was chosen"
            )
        given_root = _note(given_root, "given_root")
        given_quality = _chord_quality(given_quality, "given_quality")
        given_tones = set(chord_tones(given_root, given_quality) or ())
        correct = bool(target_tones) and given_tones == target_tones
        given_notes_json = None
        given_shape_json = None
    else:
        # name_to_shape: the question named a chord only - there is no
        # shape to show, and the answer must be an actual tap, not a
        # chosen name.
        if target_shape is not None:
            raise ValueError("name_to_shape has no shape to show - target_shape must be omitted")
        if given_root is not None or given_quality is not None:
            raise ValueError(
                "name_to_shape is answered by tapping a shape, not choosing a name - "
                "given_root/given_quality must be omitted"
            )
        if given_notes is None or given_shape is None:
            raise ValueError(
                "name_to_shape needs given_notes and given_shape - what was tapped and what it sounded"
            )
        given_notes = _notes_list(given_notes, "given_notes")
        correct = bool(target_tones) and set(given_notes) == target_tones
        given_notes_json = json.dumps(given_notes)
        given_shape_json = json.dumps(given_shape)
        given_root = None
        given_quality = None

    if response_ms is not None:
        if isinstance(response_ms, bool) or not isinstance(response_ms, int):
            raise ValueError("response_ms must be a whole number")
        if not 0 <= response_ms <= MAX_RESPONSE_MS:
            raise ValueError(f"response_ms must be between 0 and {MAX_RESPONSE_MS}")

    return {
        "drill": drill,
        "direction": direction,
        "target_root": target_root,
        "target_quality": target_quality,
        "target_shape": json.dumps(target_shape) if target_shape is not None else None,
        "given_root": given_root,
        "given_quality": given_quality,
        "given_notes": given_notes_json,
        "given_shape": given_shape_json,
        # The one place this row's verdict is decided. Not stored from
        # anywhere else and not trusted from a request body.
        "correct": correct,
        "response_ms": response_ms,
    }


def chord_attempt_dict(row) -> dict:
    """One chord attempt as the API presents it - the stored row, with
    `correct` turned back into a real bool and the JSON-encoded shape/notes
    columns turned back into lists (SQLite, and this table, only ever hold
    the text)."""
    d = dict(row)
    d["correct"] = bool(d["correct"])
    for field in ("target_shape", "given_shape", "given_notes"):
        d[field] = json.loads(d[field]) if d[field] is not None else None
    return d


# ---------------------------------------------------------------------------
# Named drill scopes (issue #236) - the validation half of
# trainer_scope_presets / trainer_scope_preset_strings.
#
# A SCOPE is what narrows a drill to what somebody is actually working on:
# which strings, which frets, and optionally which key. Both drills have
# always had one (web/src/lib/trainer/constraints.js); until #236 it lived
# only in the browser and vanished on reload. This module validates the saved
# form of it, the same way normalise_attempt validates an answered question,
# and for the same reason: the rules belong beside the data rather than in a
# route handler.
#
# CHECKED AGAINST THE BOUNDS ANY INSTRUMENT COULD HAVE, never against one
# live instrument row - exactly the rule MIN_STRING_NUMBER/MAX_FRET above
# already state for an attempt, and here it is not a convenience but the
# design: a preset is SHARED infrastructure (db.py's note on why there is no
# `drill` column says the same about drills). A scope named while a
# seven-string guitar was selected is still the scope somebody wants when
# they pick the six-string up, and pinning it to whichever instrument
# happened to be chosen when Save was pressed would make half a person's
# presets refuse to load for reasons they never asked about. What a drill
# does with a string its current tuning does not have is a rendering
# question, answered where the neck is drawn (constraints.js's stringInScope
# simply never matches it), not a reason to refuse the row.
# ---------------------------------------------------------------------------

# The two scales a key can name, character for character
# web/src/lib/trainer/constraints.js's KEY_QUALITIES. Fixed here rather than
# accepting any string for the same reason PITCH_CLASSES is fixed: "the key of
# G major" has to be a GROUP BY, not a value each client spells its own way.
KEY_QUALITIES = ("major", "minor")

# Long enough for any name a person would type, bounded so a stored name stays
# a label - the same rule, and the same number, api.MAX_SETLIST_NAME_CHARS
# applies to a setlist's.
MAX_PRESET_NAME_CHARS = 200

# How many strings one preset may name. The bound is MAX_STRING_NUMBER,
# because naming every string of the widest instrument this app accepts is a
# real scope ("all of them") and naming more than that is a mistake.
MAX_PRESET_STRINGS = MAX_STRING_NUMBER


def _preset_name(name) -> str:
    """A preset's name, cleaned the way api._clean_setlist_name cleans a
    setlist's: unprintable characters dropped, runs of whitespace collapsed,
    the ends trimmed, the length bounded. A name that was only whitespace is a
    ValueError rather than a stored blank - an unnamed entry in a list of
    named scopes is one nobody can pick on purpose."""
    if not isinstance(name, str):
        raise ValueError("a preset needs a name")
    cleaned = re.sub(r"\s+", " ", "".join(ch for ch in name if ch.isprintable())).strip()
    if not cleaned:
        raise ValueError("a preset needs a name")
    return cleaned[:MAX_PRESET_NAME_CHARS]


def _preset_strings(string_numbers) -> list[int]:
    """The string set, deduplicated and sorted.

    EMPTY IS REFUSED, and that is the one rule here worth spelling out.
    Everywhere else in the scope model an empty string list means "no filter
    at all" rather than "no strings" (constraints.js's stringInScope, and
    FretToNote.svelte's toggleString, which will not let the last box be
    unchecked for exactly this reason). A SAVED preset cannot use that
    convention: a row with no strings would be indistinguishable from a row
    whose strings failed to write, so "every string" is stored by naming every
    string, and nothing downstream has to guess which of the two was meant.
    """
    if not isinstance(string_numbers, (list, tuple)):
        raise ValueError("strings must be a list of string numbers")
    numbers = set()
    for value in string_numbers:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("every string number must be a whole number")
        if not MIN_STRING_NUMBER <= value <= MAX_STRING_NUMBER:
            raise ValueError(
                f"every string number must be between {MIN_STRING_NUMBER} "
                f"and {MAX_STRING_NUMBER}"
            )
        numbers.add(value)
    if not numbers:
        raise ValueError("a preset needs at least one string")
    if len(numbers) > MAX_PRESET_STRINGS:
        raise ValueError(f"a preset may name at most {MAX_PRESET_STRINGS} strings")
    return sorted(numbers)


def _preset_key(key_root, key_quality) -> tuple[str | None, str | None]:
    """The key, or no key at all. Both fields or neither: a root with no
    quality does not name a key, and a quality with no root names nothing -
    the same both-or-neither rule _position applies to a string and a fret,
    and for the same reason (otherwise "does this scope have a key" is
    answered by whichever column happens to be non-null)."""
    if key_root is None and key_quality is None:
        return None, None
    if key_root is None or key_quality is None:
        raise ValueError("key_root and key_quality must both be given, or neither")
    if key_root not in PITCH_CLASSES:
        raise ValueError(f"key_root must be one of {list(PITCH_CLASSES)}")
    if key_quality not in KEY_QUALITIES:
        raise ValueError(f"key_quality must be one of {list(KEY_QUALITIES)}")
    return key_root, key_quality


def normalise_preset(
    *, name, start_fret, end_fret, strings, key_root=None, key_quality=None
) -> dict:
    """Check a named scope and return what to store: the preset's own row
    under 'preset', and its string set under 'strings' (one row each, in the
    child table - db.py says why a set is not a column).

    Raises ValueError with a message meant for a person to read, the same
    contract normalise_attempt has.
    """
    for value, field in ((start_fret, "start_fret"), (end_fret, "end_fret")):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field} must be a whole number")
        if not MIN_FRET <= value <= MAX_FRET:
            raise ValueError(f"{field} must be between {MIN_FRET} and {MAX_FRET}")
    if start_fret > end_fret:
        # Not a range at all. Stored, it would be a preset that can never ask
        # a question, and nothing downstream could tell that from a scope that
        # is merely narrow.
        raise ValueError("start_fret must not be past end_fret")
    root, quality = _preset_key(key_root, key_quality)
    return {
        "preset": {
            "name": _preset_name(name),
            "start_fret": start_fret,
            "end_fret": end_fret,
            "key_root": root,
            "key_quality": quality,
        },
        "strings": _preset_strings(strings),
    }


def preset_dict(row, string_numbers) -> dict:
    """One preset as the API presents it - the stored row plus its string set,
    which lives in its own table and so is passed in rather than read off the
    row. Sorted ascending, always: a set has no order of its own, and a stable
    one is what lets a client compare two presets without sorting first."""
    d = dict(row)
    d["strings"] = sorted(string_numbers)
    return d
