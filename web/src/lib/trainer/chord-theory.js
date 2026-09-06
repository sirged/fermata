// Chord music theory (issue #28) - which notes belong to a chord, from its
// root and its quality. Pure interval arithmetic, no strings and no frets:
// this is "what IS a C major chord", the fact a shape (chord-shapes.js) is
// checked against, and the whole reason a shape can be trusted rather than
// merely drawn to look right.
//
// No runes and no browser, for the same reason neck.js and pitch.js have
// none: this is the one piece of music-theory arithmetic that a fingering
// diagram, a flash card's grading, and a unit test all have to agree with,
// and disagreeing here is not a display bug - it is teaching a wrong chord.
//
// MIRRORED, CHARACTER FOR CHARACTER, IN server/fermata/trainer.py's
// CHORD_QUALITIES and chord_tones - the same pair neck.js's PITCH_CLASSES
// and trainer.py's own PITCH_CLASSES already are. tests/unit/chord-
// theory.spec.js checks every one of the 60 (root, quality) chords this
// module can build; server/tests/test_chord_theory.py checks the identical
// 60 on the Python side, so a drift between the two fails a test on
// whichever side changed rather than showing up as a shape and its label
// disagreeing about what chord is on screen. test_chord_theory.py also
// parses this module's own source to pin the two tables' intervals equal,
// key for key, rather than trusting the two lists to stay in step by hand.
import { PITCH_CLASSES } from "./neck.js";

// Interval steps from the root, in semitones - a triad for major and minor,
// a tetrad for every seventh chord this module names. "Majors and minors
// first, then sevenths" (issue #28, extended by #252 to the minor and major
// seventh alongside the dominant) is exactly this order.
export const QUALITIES = {
  major: { label: "major", suffix: "", intervals: [0, 4, 7] },
  minor: { label: "minor", suffix: "m", intervals: [0, 3, 7] },
  dominant7: { label: "7th", suffix: "7", intervals: [0, 4, 7, 10] },
  minor7: { label: "minor 7th", suffix: "m7", intervals: [0, 3, 7, 10] },
  major7: { label: "major 7th", suffix: "maj7", intervals: [0, 4, 7, 11] },
};

export const QUALITY_LIST = Object.keys(QUALITIES);

// The chord roots this module offers - exactly PITCH_CLASSES, under its own
// name here so a caller reasoning about chords is not reaching into neck.js
// for a list that happens to also be this one.
export const ROOTS = PITCH_CLASSES;

/** The pitch classes a chord is built from - "C" + "major" -> ["C","E","G"]
 * - or null when the root or the quality is not one this module knows. The
 * ONE place this arithmetic happens: chord-shapes.js's shapeNotes computes
 * what a fingering actually sounds independently, from string tuning and
 * fret arithmetic, and every shape in the library is checked against this
 * function's own answer for its declared root and quality. */
export function chordTones(root, quality) {
  const rootIndex = PITCH_CLASSES.indexOf(root);
  const q = QUALITIES[quality];
  if (rootIndex < 0 || !q) return null;
  return q.intervals.map((step) => PITCH_CLASSES[(rootIndex + step) % 12]);
}

/** "C major", "A minor", "G7", "Cm7", "Cmaj7" - how a chord is named
 * wherever one is shown, so the flash card's prompt, its answer choices,
 * and its structured attempt row can never spell the same chord two
 * different ways. Major and minor read as a word ("C major"); every
 * seventh reads as its own suffix run straight against the root, the
 * style a guitarist already reads a seventh chord's name in - this is why
 * QUALITIES carries a suffix at all rather than only a label. Null for an
 * unknown root or quality, the same as chordTones. */
export function chordName(root, quality) {
  const q = QUALITIES[quality];
  if (PITCH_CLASSES.indexOf(root) < 0 || !q) return null;
  if (quality === "major" || quality === "minor") return `${root} ${q.label}`;
  return `${root}${q.suffix}`;
}

// The seven letter names, in alphabetical (staff-step) order - not pitch-
// class order, since spelling a chord is about how far a tone sits from the
// root on the STAFF (a letter apart), never about semitone distance. Used
// only by spellChordTones; chordTones and chordsMatch never touch a letter.
const LETTERS = ["A", "B", "C", "D", "E", "F", "G"];

// Each natural letter's own pitch class, as a semitone (C=0 .. B=11) - the
// zero-accidental reference spellChordTones measures every accidental
// against. Independent of PITCH_CLASSES's own sharp-or-flat choices.
const NATURAL_SEMITONES = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

// How many letters a tone sits above the root, by its position in
// QUALITIES' interval list - a third is two letters up (skip one), a fifth
// four, a seventh six. Triads only use the first three; a tetrad's fourth
// tone (the seventh) is index 3, hence offset 6. The root itself (index 0,
// offset 0) is never computed this way - see spellChordTones below.
const LETTER_OFFSETS = [0, 2, 4, 6];

/** How a chord's tones are named to the player, root by root - unlike
 * chordTones (identity, one fixed spelling per pitch class, used for
 * grading), this spells each tone by LETTER from the root, choosing
 * whichever accidental makes that letter's pitch class agree with
 * chordTones' answer. "A major 7" is A, C#, E, G# this way (G, not chordTones'
 * own Ab, is the seventh's letter four letters plus a fifth above... two
 * letters past the fifth, i.e. offset 6 from A: A-B-C-D-E-F-G lands on G,
 * sharped to reach the same pitch class Ab already names) - see the module
 * docstring and issue #269 for why a fixed twelve-name table is right for
 * identity but wrong for what a learner reads.
 *
 * The ROOT keeps the table's own name (Eb, Ab, Bb, C#, F# spelled as the
 * table spells them - these are ids, not chord-specific spellings, same
 * rule chordTones already follows). Every other tone is spelled fresh: null
 * for an unknown root or quality, matching chordTones.
 *
 * Checked (unit test) to need at most a single sharp or flat across all 60
 * (root, quality) chords this module builds - no double accidental. If a
 * future quality or root ever did need one, the fix belongs here (extend
 * the accidental to "##"/"bb"), not in a special-cased table lookup. */
export function spellChordTones(root, quality) {
  const tones = chordTones(root, quality);
  if (!tones) return null;
  const rootLetterIndex = LETTERS.indexOf(root[0]);
  return tones.map((tone, degree) => {
    if (degree === 0) return root;
    const letter = LETTERS[(rootLetterIndex + LETTER_OFFSETS[degree]) % 7];
    const natural = NATURAL_SEMITONES[letter];
    const actual = PITCH_CLASSES.indexOf(tone);
    let accidentalSteps = ((actual - natural) % 12 + 12) % 12;
    if (accidentalSteps > 6) accidentalSteps -= 12;
    const accidental = accidentalSteps === 0
      ? ""
      : accidentalSteps > 0
        ? "#".repeat(accidentalSteps)
        : "b".repeat(-accidentalSteps);
    return `${letter}${accidental}`;
  });
}

/** Whether two chords - each a (root, quality) pair - are the SAME chord,
 * meaning the same set of pitch classes. Compares the tone sets rather than
 * the root/quality strings directly, which is the honest version of "is
 * this the chord that was asked for": two different (root, quality) pairs
 * that happened to name identical tone sets would otherwise grade as wrong
 * for a reason that has nothing to do with what was actually played or
 * chosen. With today's five qualities no such pair exists (checked: 60 distinct
 * tone sets), but the rule
 * this drill grades by should not depend on that staying true. */
export function chordsMatch(rootA, qualityA, rootB, qualityB) {
  const a = chordTones(rootA, qualityA);
  const b = chordTones(rootB, qualityB);
  if (!a || !b) return false;
  if (a.length !== b.length) return false;
  const setB = new Set(b);
  return a.every((note) => setB.has(note));
}
