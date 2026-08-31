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
} from "../../src/lib/trainer/chord-theory.js";

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

// ---------------------------------------------------------------- exhaustive, every root x quality

test("every one of the 36 (root, quality) chords has the right note count and no repeats", () => {
  expect(ROOTS).toHaveLength(12);
  expect(QUALITY_LIST).toEqual(["major", "minor", "dominant7"]);
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

// ---------------------------------------------------------------- naming

test("chordName spells every quality the way a flash card should read it", () => {
  expect(chordName("C", "major")).toBe("C major");
  expect(chordName("A", "minor")).toBe("A minor");
  expect(chordName("G", "dominant7")).toBe("G7");
});

test("an unknown root or quality names no chord at all", () => {
  expect(chordTones("H", "major")).toBeNull();
  expect(chordTones("C", "augmented")).toBeNull();
  expect(chordName("H", "major")).toBeNull();
  expect(chordName("C", "augmented")).toBeNull();
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
