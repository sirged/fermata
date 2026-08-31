// The arithmetic behind a fret/string/duration edit, with no DOM and no
// renderer in it - so it can be exercised directly by tests/unit/editor.spec.js
// the same way metronome.js and pitch.js are (see their headers). Everything
// that touches the MusicXML document lives in editor/document.js; everything
// that touches the renderer lives behind score-render.js's edit sub-API. This
// file is only the numbers that both of them need and neither should own a
// second, divergent copy of.
//
// docs/musicxml-tab-profile.md is the contract this implements:
//   - Rule 9: every sounding note carries <string>/<fret>.
//   - Rule 10: every sounding note carries <pitch>, and the pitch is the one
//     the string, the fret and the tuning determine together - so editing a
//     fret or a string means recomputing <pitch> here, not leaving the old one
//     to disagree with the new position (the exact "two facts, one edited"
//     divergence #10's own comment warns about).
//   - Rule 5: <string> numbers from the HIGHEST-pitched string (1 = highest),
//     while <staff-tuning line=> numbers from the LOWEST (1 = bottom staff
//     line). They are mirror images, and conflating them is a measured trap -
//     see stringToTuningLine below.

// Semitone of each pitch-class letter above C.
const STEP_SEMITONE = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

// The spelling written back out for each of the twelve pitch classes. Sharps
// throughout: the extractor emits no <accidental> and no key-aware spelling
// today (see the renderer-evaluation on #10 - "the emitter produces none"),
// and choosing flats vs sharps by key is the accidentals work this increment
// explicitly defers. A fretted position has one sounding pitch; how it is
// spelled enharmonically is a later, key-signature-aware decision. Every entry
// is [step, alter].
const SEMITONE_SPELLING = [
  ["C", 0], // 0
  ["C", 1], // 1  C#
  ["D", 0], // 2
  ["D", 1], // 3  D#
  ["E", 0], // 4
  ["F", 0], // 5
  ["F", 1], // 6  F#
  ["G", 0], // 7
  ["G", 1], // 8  G#
  ["A", 0], // 9
  ["A", 1], // 10 A#
  ["B", 0], // 11
];

/**
 * The MIDI note number of a written pitch. Middle C (C4) is 60, so the octave
 * offset is `12 * (octave + 1)` - the convention alphaTab's own `realValue`
 * uses, which is what lets a value computed here be compared straight against
 * the note the renderer derived from the same string and fret (the divergence
 * guard #10 asks for).
 */
export function midiOfPitch(step, octave, alter = 0) {
  const s = STEP_SEMITONE[String(step).toUpperCase()];
  if (s == null || !Number.isFinite(octave)) return null;
  return 12 * (octave + 1) + s + (Number(alter) || 0);
}

/**
 * The written pitch for a MIDI number - `{ step, octave, alter }`, spelled
 * with sharps (see SEMITONE_SPELLING). The inverse of midiOfPitch: feeding its
 * output back in returns the same MIDI number.
 */
export function pitchFromMidi(midi) {
  if (!Number.isFinite(midi)) return null;
  const m = Math.round(midi);
  const octave = Math.floor(m / 12) - 1;
  const [step, alter] = SEMITONE_SPELLING[((m % 12) + 12) % 12];
  return { step, octave, alter };
}

/**
 * The `<staff-tuning line=>` a `<string>` value maps to (Rule 5). `<string>` 1
 * is the highest-pitched string; staff line 1 is the bottom line, i.e. the
 * lowest string - so on a `count`-string staff they are mirror images:
 * `line = count + 1 - string`. Measured against Fermata's own emitter: a note
 * with `<string>1</string>` (highest) reads its tuning from
 * `<staff-tuning line="6">` (the top line's pitch) on a six-string staff.
 *
 * This is the SAME mirror alphaTab applies in the other direction between its
 * own bottom-up string numbering and MusicXML's - kept in one named function
 * so the trap has exactly one home.
 */
export function stringToTuningLine(stringCount, string) {
  return stringCount + 1 - string;
}

/**
 * The sounding MIDI of a fretted position: the open-string pitch (the tuning
 * of the staff line this `<string>` maps to) plus the fret in semitones.
 *
 * `tuningByLine` is `line -> midi`, exactly the map document.js reads out of
 * the `<staff-tuning>` elements. `string` is the MusicXML `<string>` value.
 * Returns null when the string has no tuning line (an out-of-range string -
 * the invalid edit #165's guard must not be handed).
 */
export function midiForStringFret(tuningByLine, stringCount, string, fret) {
  const open = tuningByLine.get(stringToTuningLine(stringCount, string));
  if (open == null || !Number.isFinite(fret) || fret < 0) return null;
  return open + fret;
}

// The written duration types this increment can set, coarsest to finest, each
// as a multiple of a quarter note. Tuplets, ties and dotted values are the
// deferred entry work (see #10 and this file's header); a plain type is the
// common correction - "this eighth should be a quarter" - and is what the
// duration control offers.
export const DURATION_TYPES = ["whole", "half", "quarter", "eighth", "16th", "32nd"];

const TYPE_QUARTERS = {
  whole: 4,
  half: 2,
  quarter: 1,
  eighth: 0.5,
  "16th": 0.25,
  "32nd": 0.125,
};

/**
 * The `<duration>` value (in the document's own divisions-per-quarter) for a
 * plain, undotted note of the given `<type>`. `divisions` is the document's
 * `<divisions>` - the number of duration units in one quarter note - so a
 * quarter is `divisions`, a half is `2 * divisions`, an eighth is
 * `divisions / 2`, and so on.
 *
 * Returns null for a type this increment does not write, or a divisions value
 * that cannot express the type as a whole number of units (a 32nd against a
 * divisions of 1, say) - the caller keeps the note as it was rather than
 * writing a rounded, wrong duration.
 */
export function durationForType(type, divisions) {
  const q = TYPE_QUARTERS[type];
  if (q == null || !Number.isFinite(divisions) || divisions <= 0) return null;
  const value = q * divisions;
  return Number.isInteger(value) ? value : null;
}
