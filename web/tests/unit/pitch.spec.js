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
  draftReference,
  draftStrings,
  formatFrequency,
  isPlayable,
  pitchFrequency,
  pitchMidi,
  spellMidi,
} from "../../src/lib/pitch.js";

const STANDARD = ["E2", "A2", "D3", "G3", "B3", "E4"];
const guitar = (over = {}) => ({
  fretted: true,
  string_pitches: STANDARD,
  capo: 0,
  reference_pitch: 440,
  ...over,
});

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

test("the scale pivots on concert A", () => {
  // The first assertion in this loop is a tautology on its own: the exponent is
  // 0/12 whatever REFERENCE_MIDI happens to be, so it passes however wrong that
  // constant is. The two fixed notes are what actually pin it - A3 must be half
  // the reference and A5 double it, which only holds when REFERENCE_MIDI is 69.
  for (const reference of [392, 415, 430, 440, 442, 466]) {
    expect(pitchFrequency(REFERENCE_MIDI, reference)).toBeCloseTo(reference, 9);
    expect(pitchFrequency(57, reference)).toBeCloseTo(reference / 2, 9);
    expect(pitchFrequency(81, reference)).toBeCloseTo(reference * 2, 9);
  }
  expect(REFERENCE_MIDI).toBe(69);
  expect(pitchFrequency(57, 440)).toBeCloseTo(220, 9);
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

// ---------------------------------------------------------------- the capo
//
// draftStrings is where the capo term actually lives, so this is where it can be
// tested rather than restated. The previous test computed `midi + capo` itself
// and so proved only that spellMidi and pitchFrequency work - dropping or
// inverting the real capo term left it green.

test("no capo leaves the nominal and sounding pitches identical", () => {
  for (const rows of [draftStrings(guitar()), draftStrings(guitar({ capo: null }))]) {
    expect(rows.map((r) => r.pitch)).toEqual(STANDARD);
    expect(rows.map((r) => r.sounding_pitch)).toEqual(STANDARD);
    for (const r of rows) {
      expect(r.sounding_midi).toBe(r.midi);
      expect(r.sounding_frequency).toBe(r.frequency);
    }
  }
});

test("a capo raises every sounding pitch by its own number of semitones", () => {
  for (const capo of [1, 2, 5, 7, 12, 24]) {
    const rows = draftStrings(guitar({ capo }));
    for (const r of rows) {
      expect(r.sounding_midi - r.midi, `capo ${capo}, string ${r.number}`).toBe(capo);
      // and the nominal side is untouched, whatever the capo
      expect(r.pitch).toBe(STANDARD[STANDARD.length - r.number]);
    }
  }
});

test("a capo at the fifth fret is the tuning a guitarist expects", () => {
  const rows = draftStrings(guitar({ capo: 5 }));
  expect(rows.map((r) => `${r.pitch}->${r.sounding_pitch}`)).toEqual([
    "E2->A2",
    "A2->D3",
    "D3->G3",
    "G3->C4",
    "B3->E4",
    "E4->A4",
  ]);
  expect(rows.map((r) => formatFrequency(r.sounding_frequency))).toEqual([
    "110.00 Hz",
    "146.83 Hz",
    "196.00 Hz",
    "261.63 Hz",
    "329.63 Hz",
    "440.00 Hz",
  ]);
  // the nominal frequencies are still the open-string ones
  expect(rows.map((r) => formatFrequency(r.frequency))).toEqual([
    "82.41 Hz",
    "110.00 Hz",
    "146.83 Hz",
    "196.00 Hz",
    "246.94 Hz",
    "329.63 Hz",
  ]);
});

test("a capo at the twelfth fret doubles every frequency", () => {
  for (const r of draftStrings(guitar({ capo: 12 }))) {
    expect(r.sounding_frequency / r.frequency).toBeCloseTo(2, 9);
  }
});

test("an unfretted instrument has no capo term at all", () => {
  // A stale capo left on a draft that was switched to unfretted must move
  // nothing - there are no frets for it to be at.
  const rows = draftStrings({
    fretted: false,
    string_pitches: ["G3", "D4", "A4", "E5"],
    capo: 5,
    reference_pitch: 440,
  });
  for (const r of rows) expect(r.sounding_midi).toBe(r.midi);
  expect(rows.map((r) => r.sounding_pitch)).toEqual(["G3", "D4", "A4", "E5"]);
});

test("the capo and the reference pitch compose", () => {
  const rows = draftStrings(guitar({ capo: 5, reference_pitch: 415 }));
  expect(rows[0].sounding_frequency).toBeCloseTo(103.75, 6);
  expect(rows[0].frequency).toBeCloseTo(77.7247, 4);
});

test("a capo that pushes a string past MIDI makes it unplayable", () => {
  const rows = draftStrings({
    fretted: true,
    string_pitches: ["G9"],
    capo: 24,
    reference_pitch: 440,
  });
  expect(rows[0].midi).toBe(127);
  expect(rows[0].sounding_midi).toBe(151);
  expect(isPlayable(rows[0])).toBe(false);
  // an ordinary string is playable, so the check is about the capo
  expect(isPlayable(draftStrings(guitar())[0])).toBe(true);
});

test("string numbers run opposite to list order, reentrant or not", () => {
  const uke = draftStrings({
    fretted: true,
    string_pitches: ["G4", "C4", "E4", "A4"],
    capo: 0,
    reference_pitch: 440,
  });
  expect(uke.map((r) => r.number)).toEqual([4, 3, 2, 1]);
  // reentrant: string 4 sounds above string 3, and nothing may "correct" it
  expect(uke[0].sounding_midi).toBeGreaterThan(uke[1].sounding_midi);
});

test("a half-typed pitch name yields no numbers rather than wrong ones", () => {
  const rows = draftStrings(guitar({ string_pitches: ["E", "A2", "D3", "G3", "B3", "E4"] }));
  expect(rows[0].midi).toBeNull();
  expect(rows[0].sounding_midi).toBeNull();
  expect(rows[0].frequency).toBeNull();
  expect(rows[0].sounding_frequency).toBeNull();
  expect(isPlayable(rows[0])).toBe(false);
  expect(rows[1].midi).toBe(45);
});

// ------------------------------------------------- the reference pitch field

test("an empty reference pitch means unset, not invalid", () => {
  expect(draftReference({ reference_pitch: null })).toBe(DEFAULT_REFERENCE_HZ);
  expect(draftReference({ reference_pitch: "" })).toBe(DEFAULT_REFERENCE_HZ);
  expect(draftReference({})).toBe(DEFAULT_REFERENCE_HZ);
});

test("a reference pitch outside the bounds is refused, not defaulted", () => {
  // Quietly defaulting it showed every string at -18.73 Hz, left Save enabled,
  // and then stored 440 - screen and database disagreeing with nothing said.
  for (const bad of [-100, 0, 10, 299, 601, 5000, Number.NaN, Infinity]) {
    expect(draftReference({ reference_pitch: bad }), String(bad)).toBeNull();
  }
  for (const good of [300, 415, 440, 600]) {
    expect(draftReference({ reference_pitch: good }), String(good)).toBe(good);
  }
});

test("an unusable reference pitch produces no frequencies at all", () => {
  for (const r of draftStrings(guitar({ reference_pitch: -100 }))) {
    // the note names still parse - it is the reference that cannot be used
    expect(r.midi).not.toBeNull();
    expect(r.sounding_pitch).not.toBeNull();
    expect(r.frequency).toBeNull();
    expect(r.sounding_frequency).toBeNull();
  }
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
