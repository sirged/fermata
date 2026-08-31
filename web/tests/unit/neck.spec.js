// The neck's arithmetic, called directly (issue #25) - which note sounds at
// a string and a fret, for the standard tuning and for an arbitrary
// instrument, and the geometry helpers a drill's question generator shares
// with Neck.svelte. No browser needed for any of it - see neck.js's own
// docstring for why.
import { expect, test } from "@playwright/test";

import { spellMidi } from "../../src/lib/pitch.js";
import {
  DEFAULT_FRET_COUNT,
  PITCH_CLASSES,
  defaultStrings,
  fretCountFromInstrument,
  inlayDots,
  noteAt,
  pitchClass,
  posKey,
  positions,
  positionsForNote,
  stringsFromInstrument,
} from "../../src/lib/trainer/neck.js";

// ---------------------------------------------------------------- standard tuning

test("the standard six strings are numbered 6 (low) to 1 (high), open, spelled correctly", () => {
  const strings = defaultStrings();
  expect(strings.map((s) => s.number)).toEqual([6, 5, 4, 3, 2, 1]);
  expect(strings.map((s) => spellMidi(s.midi))).toEqual([
    "E2", "A2", "D3", "G3", "B3", "E4",
  ]);
});

test("open strings sound their own tuning - the simplest falsifiable claim a neck can make", () => {
  const strings = defaultStrings();
  const open = { 6: "E2", 5: "A2", 4: "D3", 3: "G3", 2: "B3", 1: "E4" };
  for (const [number, expected] of Object.entries(open)) {
    const midi = noteAt(strings, Number(number), 0);
    expect(spellMidi(midi), `string ${number} open`).toBe(expected);
  }
});

test("the fifth-fret relationship holds between every adjacent pair except G to B", () => {
  // A guitarist's own check when tuning by ear: fret 5 on one string matches
  // the next string open - except between the third and second strings,
  // where standard tuning is a major third (4 frets) rather than a fourth.
  const strings = defaultStrings();
  const fourths = [
    [6, 5], [5, 4], [4, 3], [2, 1],
  ];
  for (const [lower, higher] of fourths) {
    const fretted = noteAt(strings, lower, 5);
    const openHigher = noteAt(strings, higher, 0);
    expect(fretted, `string ${lower} fret 5 vs string ${higher} open`).toBe(openHigher);
  }
  // The one exception, at fret 4 instead of 5.
  expect(noteAt(strings, 3, 4)).toBe(noteAt(strings, 2, 0));
  expect(noteAt(strings, 3, 5)).not.toBe(noteAt(strings, 2, 0));
});

test("the twelfth fret is an octave above the open string, on every string", () => {
  const strings = defaultStrings();
  for (const string of strings) {
    expect(noteAt(strings, string.number, 12)).toBe(string.midi + 12);
  }
});

test("a known fretted note reads correctly - third fret, sixth string, is G", () => {
  const strings = defaultStrings();
  expect(spellMidi(noteAt(strings, 6, 3))).toBe("G2");
  expect(pitchClass(noteAt(strings, 6, 3))).toBe("G");
});

// ---------------------------------------------------------------- reading a tuning

test("stringsFromInstrument reads a saved instrument's own tuning, not a hardcoded six", () => {
  const sevenString = {
    fretted: true,
    fret_count: 24,
    strings: [
      { number: 7, midi: 35 }, // B1
      { number: 6, midi: 40 }, // E2
      { number: 5, midi: 45 },
      { number: 4, midi: 50 },
      { number: 3, midi: 55 },
      { number: 2, midi: 59 },
      { number: 1, midi: 64 },
    ],
  };
  const strings = stringsFromInstrument(sevenString);
  expect(strings).toHaveLength(7);
  expect(strings.map((s) => s.number)).toContain(7);
  expect(fretCountFromInstrument(sevenString)).toBe(24);
});

test("a capo'd instrument's SOUNDING pitch is what the neck reads, not the nominal one", () => {
  const capoed = {
    fretted: true,
    fret_count: 22,
    strings: [{ number: 1, midi: 64, sounding_midi: 66 }],
  };
  expect(stringsFromInstrument(capoed)[0].midi).toBe(66);
});

test("an unfretted instrument (a violin) falls back to the standard guitar rather than inventing frets", () => {
  const violin = { fretted: false, strings: [{ number: 4, midi: 55 }] };
  expect(stringsFromInstrument(violin)).toEqual(defaultStrings());
  expect(fretCountFromInstrument(violin)).toBe(DEFAULT_FRET_COUNT);
});

test("no instrument at all falls back to the standard guitar", () => {
  expect(stringsFromInstrument(null)).toEqual(defaultStrings());
  expect(fretCountFromInstrument(undefined)).toBe(DEFAULT_FRET_COUNT);
});

// ---------------------------------------------------------------- pitch classes

test("pitchClass matches spellMidi with the octave stripped, for every semitone in an octave", () => {
  for (let midi = 40; midi < 40 + 12; midi++) {
    expect(pitchClass(midi)).toBe(spellMidi(midi).replace(/-?\d+$/, ""));
  }
});

test("PITCH_CLASSES is exactly the twelve spellings pitchClass ever produces, in pitch order", () => {
  const produced = Array.from({ length: 12 }, (_, i) => pitchClass(60 + i));
  expect(produced).toEqual(PITCH_CLASSES);
  expect(new Set(PITCH_CLASSES).size).toBe(12);
});

// ---------------------------------------------------------------- positions / scoping

test("positions lists every playable spot in a fret range, each with its note", () => {
  const strings = defaultStrings();
  const found = positions(strings, 0, 3);
  expect(found).toHaveLength(6 * 4); // 6 strings, frets 0-3
  const openLowE = found.find((p) => p.string === 6 && p.fret === 0);
  expect(openLowE.note).toBe("E");
  expect(openLowE.midi).toBe(40);
});

test("positionsForNote finds every place a pitch class sounds, and only that class", () => {
  const strings = defaultStrings();
  const cs = positionsForNote(strings, "C", 0, 12);
  expect(cs.length).toBeGreaterThan(0);
  for (const p of cs) expect(p.note).toBe("C");
  // A known one: fret 1, string 5 (A2 + 3 = C3).
  expect(cs.some((p) => p.string === 5 && p.fret === 3)).toBe(true);
});

test("posKey distinguishes every position and is stable for the same one", () => {
  expect(posKey(6, 3)).toBe(posKey(6, 3));
  expect(posKey(6, 3)).not.toBe(posKey(3, 6));
});

// ---------------------------------------------------------------- inlay dots

test("inlay dots land on the ordinary frets, single except the octave", () => {
  const singles = [3, 5, 7, 9, 15, 17, 19, 21];
  for (const fret of singles) expect(inlayDots(fret), `fret ${fret}`).toBe(1);
  expect(inlayDots(12)).toBe(2);
  expect(inlayDots(24)).toBe(2);
  for (const fret of [0, 1, 2, 4, 6, 8, 10, 11, 13]) {
    expect(inlayDots(fret), `fret ${fret}`).toBe(0);
  }
});
