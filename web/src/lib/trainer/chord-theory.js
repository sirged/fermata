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
// theory.spec.js checks every one of the 36 (root, quality) chords this
// module can build; server/tests/test_chord_theory.py checks the identical
// 36 on the Python side, so a drift between the two fails a test on
// whichever side changed rather than showing up as a shape and its label
// disagreeing about what chord is on screen.
import { PITCH_CLASSES } from "./neck.js";

// Interval steps from the root, in semitones - a triad for major and minor,
// a tetrad for the one seventh chord this module names. "Majors and minors
// first, then sevenths" (issue #28) is exactly this order.
export const QUALITIES = {
  major: { label: "major", suffix: "", intervals: [0, 4, 7] },
  minor: { label: "minor", suffix: "m", intervals: [0, 3, 7] },
  dominant7: { label: "7th", suffix: "7", intervals: [0, 4, 7, 10] },
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

/** "C major", "A minor", "G7" - how a chord is named wherever one is shown,
 * so the flash card's prompt, its answer choices, and its structured
 * attempt row can never spell the same chord two different ways. Null for
 * an unknown root or quality, the same as chordTones. */
export function chordName(root, quality) {
  const q = QUALITIES[quality];
  if (PITCH_CLASSES.indexOf(root) < 0 || !q) return null;
  if (quality === "dominant7") return `${root}7`;
  return `${root} ${q.label}`;
}

/** Whether two chords - each a (root, quality) pair - are the SAME chord,
 * meaning the same set of pitch classes. Compares the tone sets rather than
 * the root/quality strings directly, which is the honest version of "is
 * this the chord that was asked for": two different (root, quality) pairs
 * that happened to name identical tone sets would otherwise grade as wrong
 * for a reason that has nothing to do with what was actually played or
 * chosen. With today's three qualities no such pair exists, but the rule
 * this drill grades by should not depend on that staying true. */
export function chordsMatch(rootA, qualityA, rootB, qualityB) {
  const a = chordTones(rootA, qualityA);
  const b = chordTones(rootB, qualityB);
  if (!a || !b) return false;
  if (a.length !== b.length) return false;
  const setB = new Set(b);
  return a.every((note) => setB.has(note));
}
