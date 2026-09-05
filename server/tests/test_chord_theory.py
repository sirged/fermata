"""Chord music theory (issue #28, extended to the minor and major seventh by
#252): trainer.chord_tones, checked exhaustively enough to trust it - this
is the arithmetic a chord flash card's grading stands on, mirrored
character-for-character from web/src/lib/trainer/chord-theory.js's
chordTones. See trainer.py's own module comment on why a drift between the
two sides is a wrong chord taught, not a display bug.
"""

import re
from pathlib import Path

from fermata import trainer

ROOTS = trainer.PITCH_CLASSES
QUALITIES = list(trainer.CHORD_QUALITIES)

# ---------------------------------------------------------------- known chords, by hand


def test_known_major_triads_are_exactly_right():
    assert trainer.chord_tones("C", "major") == ("C", "E", "G")
    assert trainer.chord_tones("G", "major") == ("G", "B", "D")
    assert trainer.chord_tones("D", "major") == ("D", "F#", "A")
    assert trainer.chord_tones("A", "major") == ("A", "C#", "E")
    # Ab, not G#: PITCH_CLASSES spells one sharp OR flat per pitch class.
    assert trainer.chord_tones("E", "major") == ("E", "Ab", "B")
    assert trainer.chord_tones("F", "major") == ("F", "A", "C")


def test_known_minor_triads_are_exactly_right():
    assert trainer.chord_tones("A", "minor") == ("A", "C", "E")
    assert trainer.chord_tones("E", "minor") == ("E", "G", "B")
    assert trainer.chord_tones("D", "minor") == ("D", "F", "A")
    assert trainer.chord_tones("C", "minor") == ("C", "Eb", "G")


def test_known_dominant_sevenths_are_exactly_right():
    assert trainer.chord_tones("G", "dominant7") == ("G", "B", "D", "F")
    assert trainer.chord_tones("C", "dominant7") == ("C", "E", "G", "Bb")
    assert trainer.chord_tones("A", "dominant7") == ("A", "C#", "E", "G")
    assert trainer.chord_tones("B", "dominant7") == ("B", "Eb", "F#", "A")


def test_known_minor_sevenths_are_exactly_right():
    assert trainer.chord_tones("A", "minor7") == ("A", "C", "E", "G")
    assert trainer.chord_tones("D", "minor7") == ("D", "F", "A", "C")
    assert trainer.chord_tones("E", "minor7") == ("E", "G", "B", "D")
    assert trainer.chord_tones("C", "minor7") == ("C", "Eb", "G", "Bb")


def test_known_major_sevenths_are_exactly_right():
    assert trainer.chord_tones("C", "major7") == ("C", "E", "G", "B")
    assert trainer.chord_tones("F", "major7") == ("F", "A", "C", "E")
    assert trainer.chord_tones("G", "major7") == ("G", "B", "D", "F#")
    assert trainer.chord_tones("E", "major7") == ("E", "Ab", "B", "Eb")


# ---------------------------------------------------------------- exhaustive, every root x quality


def test_every_root_and_quality_combination_has_the_right_note_count_and_no_repeats():
    assert len(ROOTS) == 12
    assert QUALITIES == ["major", "minor", "dominant7", "minor7", "major7"]
    for root in ROOTS:
        for quality in QUALITIES:
            tones = trainer.chord_tones(root, quality)
            expected_length = len(trainer.CHORD_QUALITIES[quality])
            assert len(tones) == expected_length, f"{root} {quality}"
            assert len(set(tones)) == expected_length, f"{root} {quality} has a repeated tone"
            assert tones[0] == root, f"{root} {quality} does not start on its own root"


def test_a_major_and_a_minor_triad_sharing_a_root_differ_in_exactly_the_third():
    for root in ROOTS:
        major = trainer.chord_tones(root, "major")
        minor = trainer.chord_tones(root, "minor")
        assert major[0] == minor[0]
        assert major[2] == minor[2]
        assert major[1] != minor[1]


def test_a_dominant_seventh_is_its_major_triad_plus_one_more_note():
    for root in ROOTS:
        major = trainer.chord_tones(root, "major")
        seventh = trainer.chord_tones(root, "dominant7")
        assert seventh[:3] == major
        assert len(seventh) == 4


def test_a_minor_seventh_is_its_minor_triad_plus_one_more_note():
    """Built on the MINOR triad, not the major one - the fact that
    distinguishes it from a dominant seventh, which shares a root's major
    triad. A mutation giving minor7 dominant7's own intervals (both are
    (root, +7 semitones)-shaped tetrads, so a bare note-count check would
    not catch it) fails right here, on the third."""
    for root in ROOTS:
        minor = trainer.chord_tones(root, "minor")
        seventh = trainer.chord_tones(root, "minor7")
        assert seventh[:3] == minor
        assert len(seventh) == 4


def test_a_major_seventh_is_its_major_triad_plus_one_more_note():
    """Same relationship, on the major triad plus the MAJOR seventh - a
    half step closer to the root than the dominant/minor seventh above,
    which is the interval that gives this chord its "maj7" suffix."""
    for root in ROOTS:
        major = trainer.chord_tones(root, "major")
        seventh = trainer.chord_tones(root, "major7")
        assert seventh[:3] == major
        assert len(seventh) == 4


def test_an_unknown_root_or_quality_names_no_chord():
    assert trainer.chord_tones("H", "major") is None
    assert trainer.chord_tones("C", "augmented") is None


# ---------------------------------------------------------------- cross-side parity (issue #252)


def test_chord_qualities_mirror_chord_theory_js_interval_for_interval():
    """CHORD_QUALITIES here and QUALITIES in chord-theory.js are two copies
    of the same table with nothing connecting them at runtime - a drift
    would show up as a shape and its label disagreeing about what chord is
    on screen, not as a failing test (see this module's own docstring, and
    trainer.py's). Parsing the frontend source is the cheapest way to keep
    them honest without wiring a real dependency between a Python service
    and a Svelte module - the same technique test_settings_api.py already
    uses for SCORE_THEMES and WEEK_STARTS."""
    js_path = (
        Path(__file__).resolve().parents[2]
        / "web"
        / "src"
        / "lib"
        / "trainer"
        / "chord-theory.js"
    )
    source = js_path.read_text(encoding="utf-8")
    match = re.search(r"export const QUALITIES = \{([\s\S]*?)\n\};", source)
    assert match, "could not find QUALITIES in chord-theory.js"
    body = match.group(1)
    frontend = {}
    for name, intervals in re.findall(r"(\w+):\s*\{[^}]*intervals:\s*\[([^\]]*)\]", body):
        frontend[name] = tuple(int(step.strip()) for step in intervals.split(","))
    assert frontend, "no qualities parsed out of chord-theory.js's QUALITIES"
    assert frontend == trainer.CHORD_QUALITIES
