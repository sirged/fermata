// The pitch arithmetic itself, called directly.
//
// This exists because the alternative was checking that the CONSTANTS matched
// the server's and calling the arithmetic covered. They are not the same thing:
// REFERENCE_MIDI could be 57 instead of 69 and a constants check would pass
// while every frequency in the editor came out a full octave low - 164.81 Hz
// for a guitar's low E - which is a player tuning to the wrong note with a
// green suite. So the values below are the real ones, from a table, and the
// function has to produce them.
//
// No browser is needed for any of it, which is why nothing here asks for a page
// fixture; pitch.js has no runes and no imports precisely so this can import it.
import { expect, test } from "@playwright/test";

import {
  DEFAULT_REFERENCE_HZ,
  MAX_MIDI,
  MIN_MIDI,
  REFERENCE_MIDI,
  formatFrequency,
  pitchFrequency,
  pitchMidi,
  spellMidi,
} from "../../src/lib/pitch.js";

// Equal-temperament frequencies at A440, to two decimals - the values a tuner
// or a reference table gives.
const AT_A440 = [
  ["E2", 40, 82.41],
  ["A2", 45, 110.0],
  ["D3", 50, 146.83],
  ["G3", 55, 196.0],
  ["B3", 59, 246.94],
  ["E4", 64, 329.63],
  ["A4", 69, 440.0],
  ["E5", 76, 659.26],
  ["C4", 60, 261.63],
  ["C0", 12, 16.35],
  ["G9", 127, 12543.85],
];

test("concert A is the reference pitch, whatever the reference pitch is", () => {
  // The single assertion REFERENCE_MIDI has to satisfy: the note it names comes
  // out at exactly the reference, so the scale pivots in the right place.
  for (const reference of [392, 415, 430, 440, 442, 466]) {
    expect(pitchFrequency(REFERENCE_MIDI, reference)).toBeCloseTo(reference, 9);
  }
  expect(REFERENCE_MIDI).toBe(69);
});

test("known frequencies at A440", () => {
  for (const [name, midi, hz] of AT_A440) {
    expect(pitchMidi(name), `${name} parses to MIDI ${midi}`).toBe(midi);
    expect(pitchFrequency(midi, 440), `${name} at A440`).toBeCloseTo(hz, 2);
    expect(pitchFrequency(midi), `${name} at the default reference`).toBeCloseTo(hz, 2);
  }
  expect(DEFAULT_REFERENCE_HZ).toBe(440);
});

test("an octave up is exactly double", () => {
  for (let midi = MIN_MIDI; midi + 12 <= MAX_MIDI; midi += 1) {
    expect(pitchFrequency(midi + 12)).toBeCloseTo(pitchFrequency(midi) * 2, 6);
  }
});

test("a semitone is the twelfth root of two", () => {
  const ratio = 2 ** (1 / 12);
  for (let midi = MIN_MIDI; midi < MAX_MIDI; midi += 1) {
    expect(pitchFrequency(midi + 1) / pitchFrequency(midi)).toBeCloseTo(ratio, 12);
  }
});

test("a period reference moves the whole scale with it", () => {
  // A415: concert A is 415/4 = 103.75 an octave and a bit down, and the low E
  // follows rather than staying at 82.41.
  expect(pitchFrequency(45, 415)).toBeCloseTo(103.75, 6);
  expect(pitchFrequency(40, 415)).toBeCloseTo(77.7247, 4);
  expect(formatFrequency(pitchFrequency(40, 415))).toBe("77.72 Hz");
  // and the ratio between any two strings is unchanged by it
  expect(pitchFrequency(45, 415) / pitchFrequency(40, 415)).toBeCloseTo(
    pitchFrequency(45, 440) / pitchFrequency(40, 440),
    12,
  );
});

test("accidentals and octaves are read the way they are written", () => {
  expect(pitchMidi("F#2")).toBe(42);
  expect(pitchMidi("Gb2")).toBe(42);
  expect(pitchMidi("Eb3")).toBe(51);
  expect(pitchMidi("D#3")).toBe(51);
  expect(pitchMidi("F##2")).toBe(43);
  expect(pitchMidi("Gbb2")).toBe(41);
  // case is not meaningful in a note name
  expect(pitchMidi("e2")).toBe(40);
  expect(pitchMidi(" E2 ")).toBe(40);
});

test("what is not a pitch name is refused", () => {
  for (const bad of ["", " ", "H2", "E", "E2x", "Ebb", "2E", null, undefined, {}]) {
    expect(pitchMidi(bad), JSON.stringify(bad)).toBeNull();
  }
});

test("the range stops where MIDI and MusicXML stop", () => {
  // C0 is the floor: MIDI reaches down to C-1, but MusicXML's octave type
  // starts at 0, so a tuning below C0 could never be written out.
  expect(pitchMidi("C0")).toBe(MIN_MIDI);
  expect(pitchMidi("B-1")).toBeNull();
  expect(pitchMidi("C-1")).toBeNull();
  // G9 is the ceiling: MIDI's own.
  expect(pitchMidi("G9")).toBe(MAX_MIDI);
  expect(pitchMidi("G#9")).toBeNull();
  expect(MIN_MIDI).toBe(12);
  expect(MAX_MIDI).toBe(127);
});

test("spelling a MIDI note and reading it back is a round trip", () => {
  for (let midi = MIN_MIDI; midi <= MAX_MIDI; midi += 1) {
    const name = spellMidi(midi);
    expect(pitchMidi(name), `${midi} spelled ${name}`).toBe(midi);
  }
});

test("a capo's sounding pitch is the string plus the fret", () => {
  // What the editor shows for a standard guitar with a capo at the fifth fret.
  const capo = 5;
  const sounding = [40, 45, 50, 55, 59, 64].map((midi) => spellMidi(midi + capo));
  expect(sounding).toEqual(["A2", "D3", "G3", "C4", "E4", "A4"]);
  expect(formatFrequency(pitchFrequency(40 + capo))).toBe("110.00 Hz");
});

test("a frequency is written to two decimals", () => {
  expect(formatFrequency(110)).toBe("110.00 Hz");
  expect(formatFrequency(82.4068892282175)).toBe("82.41 Hz");
  expect(formatFrequency(195.99771799087463)).toBe("196.00 Hz");
});

test("a frequency is rounded once, not twice", () => {
  // Why the server sends the unrounded value. Rounding to three decimals first
  // and then formatting to two disagrees with formatting the real number, for
  // 48 of the (note, reference) pairs in range - so if the server rounded and
  // the editor formatted, a saved instrument and the draft it came from would
  // show different frequencies for the same string.
  const twiceRounded = (hz) => formatFrequency(Number(hz.toFixed(3)));
  const c2at300 = pitchFrequency(36, 300);
  expect(formatFrequency(c2at300)).toBe("44.60 Hz");
  expect(twiceRounded(c2at300)).toBe("44.59 Hz");

  const b2at300 = pitchFrequency(47, 300);
  expect(formatFrequency(b2at300)).toBe("84.18 Hz");
  expect(twiceRounded(b2at300)).toBe("84.19 Hz");
});
