// Chord music theory, called directly (issue #28) - which notes belong to a
// chord, exhaustively enough across every root and quality this module
// offers to trust it, since this is the arithmetic a shape's actual sounded
// notes (chord-shapes.spec.js) and a flash card's grading both stand on. No
// browser needed - see this module's own docstring for why.
import { expect, test } from "@playwright/test";

import {
  QUALITIES,
  QUALITY_LIST,
  ROOTS,
  chordName,
  chordTones,
  chordsMatch,
  spellChordTones,
} from "../../src/lib/trainer/chord-theory.js";
import { PITCH_CLASSES } from "../../src/lib/trainer/neck.js";

// ---------------------------------------------------------------- known chords, by hand

test("known major triads are exactly right", () => {
  expect(chordTones("C", "major")).toEqual(["C", "E", "G"]);
  expect(chordTones("G", "major")).toEqual(["G", "B", "D"]);
  expect(chordTones("D", "major")).toEqual(["D", "F#", "A"]);
  expect(chordTones("A", "major")).toEqual(["A", "C#", "E"]);
  // Ab, not G#: PITCH_CLASSES spells one sharp OR flat per pitch class (see
  // neck.js), and index 8 is "Ab" - the same table this chord's third is
  // drawn from, so this is the spelling the app uses everywhere else too.
  expect(chordTones("E", "major")).toEqual(["E", "Ab", "B"]);
  expect(chordTones("F", "major")).toEqual(["F", "A", "C"]);
});

test("known minor triads are exactly right", () => {
  expect(chordTones("A", "minor")).toEqual(["A", "C", "E"]);
  expect(chordTones("E", "minor")).toEqual(["E", "G", "B"]);
  expect(chordTones("D", "minor")).toEqual(["D", "F", "A"]);
  expect(chordTones("C", "minor")).toEqual(["C", "Eb", "G"]);
  expect(chordTones("F#", "minor")).toEqual(["F#", "A", "C#"]);
});

test("known dominant sevenths are exactly right", () => {
  expect(chordTones("G", "dominant7")).toEqual(["G", "B", "D", "F"]);
  expect(chordTones("C", "dominant7")).toEqual(["C", "E", "G", "Bb"]);
  expect(chordTones("A", "dominant7")).toEqual(["A", "C#", "E", "G"]);
  expect(chordTones("E", "dominant7")).toEqual(["E", "Ab", "B", "D"]);
  expect(chordTones("B", "dominant7")).toEqual(["B", "Eb", "F#", "A"]);
});

test("known minor sevenths are exactly right", () => {
  expect(chordTones("A", "minor7")).toEqual(["A", "C", "E", "G"]);
  expect(chordTones("D", "minor7")).toEqual(["D", "F", "A", "C"]);
  expect(chordTones("E", "minor7")).toEqual(["E", "G", "B", "D"]);
  expect(chordTones("C", "minor7")).toEqual(["C", "Eb", "G", "Bb"]);
});

test("known major sevenths are exactly right", () => {
  expect(chordTones("C", "major7")).toEqual(["C", "E", "G", "B"]);
  expect(chordTones("F", "major7")).toEqual(["F", "A", "C", "E"]);
  expect(chordTones("G", "major7")).toEqual(["G", "B", "D", "F#"]);
  expect(chordTones("E", "major7")).toEqual(["E", "Ab", "B", "Eb"]);
});

// ---------------------------------------------------------------- exhaustive, every root x quality

test("every one of the 60 (root, quality) chords has the right note count and no repeats", () => {
  expect(ROOTS).toHaveLength(12);
  expect(QUALITY_LIST).toEqual(["major", "minor", "dominant7", "minor7", "major7"]);
  for (const root of ROOTS) {
    for (const quality of QUALITY_LIST) {
      const tones = chordTones(root, quality);
      const expectedLength = QUALITIES[quality].intervals.length;
      expect(tones, `${root} ${quality}`).toHaveLength(expectedLength);
      expect(new Set(tones).size, `${root} ${quality} has no repeated tone`).toBe(expectedLength);
      expect(tones[0], `${root} ${quality} is spelled starting on its own root`).toBe(root);
    }
  }
});

test("a major and a minor triad sharing a root differ in exactly the third", () => {
  // The one semitone that makes a chord happy or sad, at every root - the
  // root and the fifth never move; only the third does.
  for (const root of ROOTS) {
    const major = chordTones(root, "major");
    const minor = chordTones(root, "minor");
    expect(major[0]).toBe(minor[0]); // root
    expect(major[2]).toBe(minor[2]); // fifth
    expect(major[1]).not.toBe(minor[1]); // third
  }
});

test("a dominant seventh is its major triad plus one more note, at every root", () => {
  for (const root of ROOTS) {
    const major = chordTones(root, "major");
    const seventh = chordTones(root, "dominant7");
    expect(seventh.slice(0, 3)).toEqual(major);
    expect(seventh).toHaveLength(4);
  }
});

// A minor seventh is built on the MINOR triad, not the major one - this is
// what distinguishes it from a dominant seventh, which shares a root's
// triad but not its seventh. A mutation that gave minor7 dominant7's own
// intervals (both are (root, +7 semitones)-shaped tetrads, so a bare note-
// count check would not catch it) fails right here, on the third.
test("a minor seventh is its minor triad plus one more note, at every root", () => {
  for (const root of ROOTS) {
    const minor = chordTones(root, "minor");
    const seventh = chordTones(root, "minor7");
    expect(seventh.slice(0, 3)).toEqual(minor);
    expect(seventh).toHaveLength(4);
  }
});

// Same relationship, on the major triad plus the MAJOR seventh (a half
// step closer to the root than the dominant/minor seventh above) - the
// interval that gives this chord its "maj7" suffix rather than a plain "7".
test("a major seventh is its major triad plus one more note, at every root", () => {
  for (const root of ROOTS) {
    const major = chordTones(root, "major");
    const seventh = chordTones(root, "major7");
    expect(seventh.slice(0, 3)).toEqual(major);
    expect(seventh).toHaveLength(4);
  }
});

// ---------------------------------------------------------------- naming

test("chordName spells every quality the way a flash card should read it", () => {
  expect(chordName("C", "major")).toBe("C major");
  expect(chordName("A", "minor")).toBe("A minor");
  expect(chordName("G", "dominant7")).toBe("G7");
  expect(chordName("C", "minor7")).toBe("Cm7");
  expect(chordName("C", "major7")).toBe("Cmaj7");
});

test("an unknown root or quality names no chord at all", () => {
  expect(chordTones("H", "major")).toBeNull();
  expect(chordTones("C", "augmented")).toBeNull();
  expect(chordName("H", "major")).toBeNull();
  expect(chordName("C", "augmented")).toBeNull();
});

// ---------------------------------------------------------------- spelled tones (issue #269)

// The seven letters, each letter's own natural pitch class as a semitone,
// and a reducer that turns a spelled tone ("G#", "Bb", "E") back into the
// pitch class chordTones would name for the same semitone - independent of
// spellChordTones' own implementation, so this test would catch the
// function computing the wrong semitone, not merely restate its own math.
const LETTER_NATURAL_SEMITONE = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
function pitchClassOf(spelledTone) {
  const letter = spelledTone[0];
  const accidental = spelledTone.slice(1);
  let semitone = LETTER_NATURAL_SEMITONE[letter];
  for (const ch of accidental) semitone += ch === "#" ? 1 : -1;
  semitone = ((semitone % 12) + 12) % 12;
  return PITCH_CLASSES[semitone];
}

const LETTERS = ["A", "B", "C", "D", "E", "F", "G"];
const LETTER_OFFSETS = [0, 2, 4, 6];

test("A major 7 spells G#, not the table's Ab; B major spells D#, not Eb", () => {
  // The two strings #263's review measured directly off main, and #269's
  // whole reason to exist - see this repo's issue #269.
  expect(spellChordTones("A", "major7")).toEqual(["A", "C#", "E", "G#"]);
  expect(spellChordTones("B", "major")).toEqual(["B", "D#", "F#"]);
});

// A hand-written table for one quality (major) across all twelve roots -
// the twelve results a reviewer can check by eye against a staff, not only
// against this module's own reduction logic below. The four roots the
// twelve-name table already spells with a sharp (C#, F#) read their thirds
// and sevenths as sharps too (E#, A#, B#) rather than switching to a flat
// spelling mid-chord - the same "roots stay as the table names them" rule
// issue #269 states, applied consistently within a chord.
test("major triads/sevenths, all twelve roots: a hand-written spelling table", () => {
  expect(spellChordTones("C", "major")).toEqual(["C", "E", "G"]);
  expect(spellChordTones("C#", "major")).toEqual(["C#", "E#", "G#"]);
  expect(spellChordTones("D", "major")).toEqual(["D", "F#", "A"]);
  expect(spellChordTones("Eb", "major")).toEqual(["Eb", "G", "Bb"]);
  expect(spellChordTones("E", "major")).toEqual(["E", "G#", "B"]);
  expect(spellChordTones("F", "major")).toEqual(["F", "A", "C"]);
  expect(spellChordTones("F#", "major")).toEqual(["F#", "A#", "C#"]);
  expect(spellChordTones("G", "major")).toEqual(["G", "B", "D"]);
  expect(spellChordTones("Ab", "major")).toEqual(["Ab", "C", "Eb"]);
  expect(spellChordTones("A", "major")).toEqual(["A", "C#", "E"]);
  expect(spellChordTones("Bb", "major")).toEqual(["Bb", "D", "F"]);
  expect(spellChordTones("B", "major")).toEqual(["B", "D#", "F#"]);
});

test("every one of the 60 spelled chords reduces to chordTones' own pitch classes", () => {
  for (const root of ROOTS) {
    for (const quality of QUALITY_LIST) {
      const tones = chordTones(root, quality);
      const spelled = spellChordTones(root, quality);
      expect(spelled, `${root} ${quality}`).toHaveLength(tones.length);
      expect(spelled[0], `${root} ${quality} root keeps the table's own name`).toBe(root);
      for (let i = 0; i < tones.length; i++) {
        expect(pitchClassOf(spelled[i]), `${root} ${quality} tone ${i}: ${spelled[i]}`).toBe(tones[i]);
      }
    }
  }
});

test("every spelled tone's letter ascends from the root by its interval degree", () => {
  for (const root of ROOTS) {
    for (const quality of QUALITY_LIST) {
      const spelled = spellChordTones(root, quality);
      const rootLetterIndex = LETTERS.indexOf(root[0]);
      for (let degree = 1; degree < spelled.length; degree++) {
        const expectedLetter = LETTERS[(rootLetterIndex + LETTER_OFFSETS[degree]) % 7];
        expect(spelled[degree][0], `${root} ${quality} tone ${degree}`).toBe(expectedLetter);
      }
    }
  }
});

test("no chord among the 60 needs a double accidental", () => {
  for (const root of ROOTS) {
    for (const quality of QUALITY_LIST) {
      for (const tone of spellChordTones(root, quality)) {
        expect(tone.slice(1).length, `${root} ${quality}: ${tone}`).toBeLessThanOrEqual(1);
      }
    }
  }
});

test("an unknown root or quality spells no chord at all", () => {
  expect(spellChordTones("H", "major")).toBeNull();
  expect(spellChordTones("C", "augmented")).toBeNull();
});

// ---------------------------------------------------------------- chord equality

test("chordsMatch is true for the same chord and false for a different one", () => {
  expect(chordsMatch("C", "major", "C", "major")).toBe(true);
  expect(chordsMatch("C", "major", "C", "minor")).toBe(false);
  expect(chordsMatch("C", "major", "G", "major")).toBe(false);
});

test("chordsMatch is false, not thrown, against an unknown chord on either side", () => {
  expect(chordsMatch("H", "major", "C", "major")).toBe(false);
  expect(chordsMatch("C", "major", "C", "augmented")).toBe(false);
});
