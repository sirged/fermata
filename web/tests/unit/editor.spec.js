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
  accidentalName,
  durationForDots,
  durationForType,
  enharmonicSpellings,
  isWritablePitch,
  keyAlter,
  midiForStringFret,
  midiOfPitch,
  pitchFromMidi,
  spellPitch,
  spellWithAlter,
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

test("pitchFromMidi is the C-major spelling (sharp sharps, flat flats)", () => {
  // C major (fifths 0) is exactly spell_pitch at fifths 0 on the server:
  // C C# D Eb E F F# G Ab A Bb B - the sharp side spelled sharp, the flat side
  // flat, not sharps throughout.
  expect(pitchFromMidi(61)).toEqual({ step: "C", octave: 4, alter: 1 }); // C#
  expect(pitchFromMidi(66)).toEqual({ step: "F", octave: 4, alter: 1 }); // F#
  expect(pitchFromMidi(63)).toEqual({ step: "E", octave: 4, alter: -1 }); // Eb
  expect(pitchFromMidi(68)).toEqual({ step: "A", octave: 4, alter: -1 }); // Ab
  expect(pitchFromMidi(70)).toEqual({ step: "B", octave: 4, alter: -1 }); // Bb
});

// ---------------------------------------------------------- key-aware spelling (#185)

test("spellPitch preserves the sounding pitch in every key", () => {
  for (let f = -8; f <= 8; f++) {
    for (let m = MIN_WRITABLE_MIDI; m <= MAX_WRITABLE_MIDI; m++) {
      const p = spellPitch(m, f);
      expect(midiOfPitch(p.step, p.octave, p.alter)).toBe(m);
    }
  }
});

test("a flat key spells a black key flat and a sharp key sharp - same sound", () => {
  // MIDI 66 is one key on the instrument; the key signature decides its name.
  expect(spellPitch(66, -4)).toEqual({ step: "G", alter: -1, octave: 4 }); // Ab major -> Gb
  expect(spellPitch(66, 0)).toEqual({ step: "F", alter: 1, octave: 4 }); // C major -> F#
  // Both spellings sound the same note.
  expect(midiOfPitch("G", 4, -1)).toBe(66);
  expect(midiOfPitch("F", 4, 1)).toBe(66);
});

test("the octave follows the spelling, not the MIDI number alone", () => {
  // MIDI 60 is middle C; spelled B sharp it is octave 3, not 4.
  expect(spellPitch(60, 0)).toEqual({ step: "C", alter: 0, octave: 4 });
  expect(spellWithAlter(60, 1)).toEqual({ step: "B", alter: 1, octave: 3 });
});

test("keyAlter is what the key signature already puts on a letter", () => {
  expect(keyAlter("F", 1)).toBe(1); // G major sharps F
  expect(keyAlter("F", 0)).toBe(0); // C major does not
  expect(keyAlter("B", -1)).toBe(-1); // F major flats B
  expect(keyAlter("G", -4)).toBe(0); // Ab major leaves G natural
});

test("spellWithAlter fixes the accidental and solves for the letter, same sound", () => {
  expect(spellWithAlter(66, -1)).toEqual({ step: "G", alter: -1, octave: 4 }); // Gb
  expect(spellWithAlter(66, 1)).toEqual({ step: "F", alter: 1, octave: 4 }); // F#
  // A black key has no natural spelling.
  expect(spellWithAlter(66, 0)).toBeNull();
});

test("enharmonicSpellings are the flat/natural/sharp alternatives, same MIDI", () => {
  // F sharp <-> G flat.
  expect(enharmonicSpellings(66)).toEqual([
    { step: "G", alter: -1, octave: 4 },
    { step: "F", alter: 1, octave: 4 },
  ]);
  // C natural and B sharp (the octave moving with the spelling).
  expect(enharmonicSpellings(60)).toEqual([
    { step: "C", alter: 0, octave: 4 },
    { step: "B", alter: 1, octave: 3 },
  ]);
  for (let m = MIN_WRITABLE_MIDI; m <= MAX_WRITABLE_MIDI; m++) {
    for (const p of enharmonicSpellings(m)) expect(midiOfPitch(p.step, p.octave, p.alter)).toBe(m);
  }
});

test("accidentalName maps an alter to its printed accidental", () => {
  expect(accidentalName(-1)).toBe("flat");
  expect(accidentalName(0)).toBe("natural");
  expect(accidentalName(1)).toBe("sharp");
  expect(accidentalName(3)).toBeNull();
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

// ---------------------------------------------------------------- dotted durations (#183)

test("zero dots is the plain duration", () => {
  // No dots is exactly durationForType, so the same control can ask for either.
  expect(durationForDots("quarter", 480, 0)).toBe(480);
  expect(durationForDots("eighth", 480, 0)).toBe(240);
});

test("a dot adds half the value again, a second dot half of that", () => {
  // Dotted quarter = 480 * 3/2 = 720; double-dotted = 480 * 7/4 = 840.
  expect(durationForDots("quarter", 480, 1)).toBe(720);
  expect(durationForDots("quarter", 480, 2)).toBe(840);
  // Dotted eighth = 240 * 3/2 = 360; double-dotted = 240 * 7/4 = 420.
  expect(durationForDots("eighth", 480, 1)).toBe(360);
  expect(durationForDots("eighth", 480, 2)).toBe(420);
  // Dotted half = 960 * 3/2 = 1440 (three quarters, the classic 3/4-bar note).
  expect(durationForDots("half", 480, 1)).toBe(1440);
});

test("a dotted value that is not a whole number of divisions is refused", () => {
  // A dotted 16th at divisions 480 is 120 * 3/2 = 180 (fine); a double-dotted
  // 16th is 120 * 7/4 = 210 (fine). But halve the divisions to where the plain
  // type is already odd and the dot cannot land on a whole unit:
  expect(durationForDots("16th", 480, 1)).toBe(180);
  expect(durationForDots("16th", 480, 2)).toBe(210);
  // divisions 1: a quarter is 1 unit, and 1 * 3/2 is not a whole number.
  expect(durationForDots("quarter", 1, 1)).toBeNull();
  // A plain type that already has no duration stays null with any dots.
  expect(durationForDots("32nd", 1, 0)).toBeNull();
});

test("a dot count outside 0..2 is refused", () => {
  expect(durationForDots("quarter", 480, 3)).toBeNull();
  expect(durationForDots("quarter", 480, -1)).toBeNull();
  expect(durationForDots("quarter", 480, 1.5)).toBeNull();
});
