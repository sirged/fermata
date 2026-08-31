// The note editor's arithmetic, called directly - no DOM, no renderer, no
// page - the same way tests/unit/pitch.spec.js and metronome.spec.js exercise
// their modules. editor/notes.js has no imports beyond itself precisely so
// this can. The document model and the renderer seam are exercised end to end
// by tests/browser/score-editor.spec.js instead, where a real DOM and a real
// alphaTab render exist.
import { expect, test } from "@playwright/test";

import {
  DURATION_TYPES,
  MAX_WRITABLE_MIDI,
  MIN_WRITABLE_MIDI,
  durationForType,
  isWritablePitch,
  midiForStringFret,
  midiOfPitch,
  pitchFromMidi,
  stringToTuningLine,
} from "../../src/lib/editor/notes.js";

// Standard six-string guitar, as Fermata's own emitter writes it: staff-tuning
// line 1 is the lowest string (E2), line 6 the highest (E4). This is the
// `line -> midi` map document.js builds from the <staff-tuning> elements.
const STANDARD = new Map([
  [1, 40], // E2
  [2, 45], // A2
  [3, 50], // D3
  [4, 55], // G3
  [5, 59], // B3
  [6, 64], // E4
]);
const STRINGS = 6;

// ---------------------------------------------------------------- pitch <-> midi

test("middle C is MIDI 60 and E4 is 64", () => {
  expect(midiOfPitch("C", 4)).toBe(60);
  expect(midiOfPitch("E", 4)).toBe(64);
  expect(midiOfPitch("A", 2)).toBe(45);
});

test("an alter shifts the pitch by that many semitones", () => {
  expect(midiOfPitch("C", 4, 1)).toBe(61);
  expect(midiOfPitch("D", 4, -1)).toBe(61);
});

test("pitchFromMidi round-trips every MIDI number back to the same number", () => {
  for (let m = 21; m <= 108; m++) {
    const p = pitchFromMidi(m);
    expect(midiOfPitch(p.step, p.octave, p.alter)).toBe(m);
  }
});

test("pitchFromMidi spells a black key as a sharp", () => {
  expect(pitchFromMidi(61)).toEqual({ step: "C", octave: 4, alter: 1 });
  expect(pitchFromMidi(66)).toEqual({ step: "F", octave: 4, alter: 1 });
});

// ---------------------------------------------------------------- the Rule 5 mirror

test("string 1 is the highest staff line and string 6 the lowest", () => {
  // <string> numbers from the highest-pitched string; <staff-tuning line=>
  // from the bottom line. On six strings they mirror.
  expect(stringToTuningLine(6, 1)).toBe(6);
  expect(stringToTuningLine(6, 6)).toBe(1);
  expect(stringToTuningLine(6, 3)).toBe(4);
});

// ---------------------------------------------------------------- fretted pitch

test("an open string sounds its own tuning", () => {
  expect(midiForStringFret(STANDARD, STRINGS, 1, 0)).toBe(64); // E4
  expect(midiForStringFret(STANDARD, STRINGS, 6, 0)).toBe(40); // E2
});

test("a fret raises the open string by that many semitones", () => {
  expect(midiForStringFret(STANDARD, STRINGS, 6, 3)).toBe(43); // E2 + 3 = G2
  expect(midiForStringFret(STANDARD, STRINGS, 2, 2)).toBe(61); // B3 + 2 = C#4
});

test("an out-of-range string or a negative fret has no pitch", () => {
  expect(midiForStringFret(STANDARD, STRINGS, 7, 0)).toBeNull();
  expect(midiForStringFret(STANDARD, STRINGS, 0, 0)).toBeNull();
  expect(midiForStringFret(STANDARD, STRINGS, 1, -1)).toBeNull();
});

// ---------------------------------------------------------------- Rule 11 range

test("a pitch is writable only inside MusicXML's octave range (MIDI 12-131)", () => {
  // <octave> is 0-9, so C0 (12) and B9 (131) are the extremes and octave 10
  // (132) is unwritable - the value that would make a validating consumer
  // reject the whole document.
  expect(MIN_WRITABLE_MIDI).toBe(12);
  expect(MAX_WRITABLE_MIDI).toBe(131);
  expect(isWritablePitch(64)).toBe(true);
  expect(isWritablePitch(12)).toBe(true);
  expect(isWritablePitch(131)).toBe(true);
  expect(isWritablePitch(132)).toBe(false);
  expect(isWritablePitch(11)).toBe(false);
  // The high E string (E4 = 64): fret 67 reaches B9 (131, the top of the
  // range) and is still writable; fret 68 would be octave 10 and is not.
  expect(isWritablePitch(midiForStringFret(STANDARD, STRINGS, 1, 67))).toBe(true);
  expect(isWritablePitch(midiForStringFret(STANDARD, STRINGS, 1, 68))).toBe(false);
});

// ---------------------------------------------------------------- durations

test("a quarter is one division-per-quarter's worth of duration", () => {
  expect(durationForType("quarter", 480)).toBe(480);
  expect(durationForType("half", 480)).toBe(960);
  expect(durationForType("whole", 480)).toBe(1920);
  expect(durationForType("eighth", 480)).toBe(240);
  expect(durationForType("16th", 480)).toBe(120);
});

test("a duration that cannot be expressed as a whole number of units is refused", () => {
  // A 32nd against a divisions of 1 would be a quarter of a unit - not
  // writable, so kept as it was rather than rounded.
  expect(durationForType("32nd", 1)).toBeNull();
  expect(durationForType("quarter", 1)).toBe(1);
});

test("an unknown type has no duration", () => {
  expect(durationForType("breve", 480)).toBeNull();
  expect(DURATION_TYPES).toContain("quarter");
});
