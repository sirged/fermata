"""Chord music theory (issue #28): trainer.chord_tones, checked exhaustively
enough to trust it - this is the arithmetic a chord flash card's grading
stands on, mirrored character-for-character from web/src/lib/trainer/chord-
theory.js's chordTones. See trainer.py's own module comment on why a drift
between the two sides is a wrong chord taught, not a display bug.
"""

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


# ---------------------------------------------------------------- exhaustive, every root x quality


def test_every_root_and_quality_combination_has_the_right_note_count_and_no_repeats():
    assert len(ROOTS) == 12
    assert QUALITIES == ["major", "minor", "dominant7"]
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


def test_an_unknown_root_or_quality_names_no_chord():
    assert trainer.chord_tones("H", "major") is None
    assert trainer.chord_tones("C", "augmented") is None
