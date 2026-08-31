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

// The letter that sits at each semitone of the octave, or undefined for the
// five semitones no letter names on its own (1, 3, 6, 8, 10). The inverse of
// STEP_SEMITONE, used to answer "which letter, at a given accidental, spells
// this pitch class" - the arithmetic behind setting an accidental explicitly
// and cycling the enharmonic alternatives, both of which fix the accidental and
// solve for the letter rather than the other way round.
const SEMITONE_STEP = { 0: "C", 2: "D", 4: "E", 5: "F", 7: "G", 9: "A", 11: "B" };

// The line of fifths, as note LETTERS at seven consecutive positions - the exact
// port of server/fermata/musicxml.py's _FIFTHS_LETTERS, so a pitch recomputed in
// the browser is spelled the way spell_pitch() would spell it on the server.
// Position n has letter FIFTHS_LETTERS[(n + 1) mod 7] and alter floor((n + 1) /
// 7): position -1 is F, 0 is C, 5 is B, 6 is F sharp, -2 is B flat.
const FIFTHS_LETTERS = ["F", "C", "G", "D", "A", "E", "B"];
// The single-accidental range spell_pitch considers: F flat (-8) through B sharp
// (12). Every pitch class has at least one spelling inside it and none is a
// double accidental, so no note comes out spelled with an accidental a guitarist
// would not expect.
const FIFTHS_MIN = -8;
const FIFTHS_MAX = 12;

// The letters carrying a sharp / a flat in a key of `fifths` accidentals, in the
// order accidentals are added to a signature (Fs Cs Gs...; Bb Eb Ab...). Used to
// decide whether a note's own alter differs from what the KEY already puts on
// that letter, which is exactly when a printed <accidental> is needed.
const SHARP_ORDER = "FCGDAEB";
const FLAT_ORDER = "BEADGCF";

// The <accidental> spelling for each alter this editor writes (-2..+2). An
// <alter> and its printed <accidental> name the same thing - a sharp is +1, a
// flat -1, a natural 0 - and Rule 10 requires them to stay mutually consistent,
// so the one place that maps between them lives here.
const ALTER_ACCIDENTAL = {
  "-2": "double-flat",
  "-1": "flat",
  0: "natural",
  1: "sharp",
  2: "double-sharp",
};

// Positive remainder, so the line-of-fifths and pitch-class arithmetic below
// matches Python's % (JS's % keeps the sign of the dividend, Python's does not).
function mod(n, m) {
  return ((n % m) + m) % m;
}

// The (step, alter) at position n on the line of fifths - _fifths_position on
// the server.
function fifthsPosition(n) {
  return [FIFTHS_LETTERS[mod(n + 1, 7)], Math.floor((n + 1) / 7)];
}

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

// The range a `<pitch>` can actually express, and so the range an edit is
// allowed to produce (Rule 11). MusicXML's `octave` is an integer 0-9, so the
// lowest writable pitch is C0 (MIDI 12) and the highest is B9 (MIDI 131). A
// note outside it cannot be written as some OTHER pitch instead - an `<octave>`
// of 10 makes the whole document unreadable to a validating consumer - so an
// edit that would land there is refused rather than written, exactly as the
// extractor refuses to emit one.
export const MIN_WRITABLE_MIDI = 12;
export const MAX_WRITABLE_MIDI = 131;

/** Whether a MIDI number can be written as a `<pitch>` at all (Rule 11). */
export function isWritablePitch(midi) {
  return Number.isFinite(midi) && midi >= MIN_WRITABLE_MIDI && midi <= MAX_WRITABLE_MIDI;
}

/**
 * Spell a MIDI number as MusicXML `{ step, alter, octave }` against a key
 * signature - the exact port of server/fermata/musicxml.py's `spell_pitch`, so
 * a pitch recomputed here is spelled the way the emitter would spell it (Rules
 * 12 and 13). The MIDI number is exact; which of its enharmonic spellings to
 * write is not, and `fifths` (the `<key><fifths>` in force) is what settles it.
 *
 * Both are resolved on the line of fifths, where a key signature IS a position:
 * a key of `fifths` accidentals centres on `fifths + 2`. Each pitch class's
 * spellings are twelve positions apart, so at most two fall in the
 * single-accidental range, and the one NEARER the key's centre is the one an
 * engraver writes. Ties (a pitch class a tritone from the centre) break first
 * toward the spelling needing no accidental, then toward the flat - the
 * `(abs(n - centre), abs(alter), n)` ranking, smallest wins, which is Rule 13's
 * documented default. The octave follows from the spelling, not the MIDI number
 * alone, so MIDI 60 spelled B sharp is octave 3, not 4.
 *
 * The inverse of midiOfPitch: feeding its output back in returns the same MIDI
 * number, in every key.
 */
export function spellPitch(midi, fifths = 0) {
  if (!Number.isFinite(midi)) return null;
  const m = Math.round(midi);
  const pitchClass = mod(m, 12);
  const centre = (Number(fifths) || 0) + 2;
  let best = null;
  for (let n = FIFTHS_MIN; n <= FIFTHS_MAX; n++) {
    if (mod(7 * n, 12) !== pitchClass) continue;
    const alter = fifthsPosition(n)[1];
    const rank = [Math.abs(n - centre), Math.abs(alter), n];
    if (best === null || rankLess(rank, best.rank)) best = { rank, n };
  }
  const [step, alter] = fifthsPosition(best.n);
  const octave = Math.floor((m - alter - STEP_SEMITONE[step]) / 12) - 1;
  return { step, alter, octave };
}

// Lexicographic compare of two (a, b, c) rank tuples, matching Python's tuple
// `<`: smaller first element wins, ties broken by the next, then the next.
function rankLess(x, y) {
  for (let i = 0; i < x.length; i++) {
    if (x[i] !== y[i]) return x[i] < y[i];
  }
  return false;
}

/**
 * The written pitch for a MIDI number spelled in C major (no key signature) -
 * `{ step, octave, alter }`. Kept as the zero-key shorthand for callers that
 * have no key to spell against; it is exactly `spellPitch(midi, 0)`, which is
 * the same C-major spelling the server and the MusicXML emitter produce (C, C#,
 * D, Eb, E, F, F#, G, Ab, A, Bb, B - not sharps throughout).
 */
export function pitchFromMidi(midi) {
  const p = spellPitch(midi, 0);
  return p && { step: p.step, octave: p.octave, alter: p.alter };
}

/**
 * The alteration a KEY of `fifths` accidentals already puts on a letter: +1 for
 * a sharped letter, -1 for a flatted one, 0 otherwise. This is the alteration
 * "in force" on a step from the key signature alone (before any accidental
 * printed earlier in the bar), and so decides whether a note's own alter needs a
 * printed <accidental> to override it - a natural that the key would sharpen or
 * flatten must be written as one (Rule 12/standard notation).
 */
export function keyAlter(step, fifths) {
  const f = Number(fifths) || 0;
  if (f > 0) return SHARP_ORDER.slice(0, f).includes(step) ? 1 : 0;
  if (f < 0) return FLAT_ORDER.slice(0, -f).includes(step) ? -1 : 0;
  return 0;
}

/** The `<accidental>` name for an alter (-2..+2), or null for one out of range. */
export function accidentalName(alter) {
  return ALTER_ACCIDENTAL[String(alter)] ?? null;
}

/**
 * The `{ step, alter, octave }` that spells `midi` with exactly the accidental
 * `alter` (-2..+2), or null when no letter names that pitch class at that
 * accidental (e.g. there is no natural spelling of a black key) or the octave
 * falls outside MusicXML's 0..9. The sounding pitch is unchanged by
 * construction: midiOfPitch of the result is `midi`. This is what an explicit
 * accidental choice resolves to - the player fixes the accidental, and the
 * letter and octave follow.
 */
export function spellWithAlter(midi, alter) {
  if (!Number.isFinite(midi) || !Number.isInteger(alter) || alter < -2 || alter > 2) return null;
  const m = Math.round(midi);
  const step = SEMITONE_STEP[mod(m - alter, 12)];
  if (!step) return null;
  const octave = Math.floor((m - alter - STEP_SEMITONE[step]) / 12) - 1;
  if (octave < 0 || octave > 9) return null;
  return { step, alter, octave };
}

/**
 * The enharmonic spellings of a sounding pitch that use a single accidental or
 * none - flat, natural, sharp - in that fixed order, each a
 * `{ step, alter, octave }` with the SAME MIDI as `midi`. F sharp 4 and G flat 4
 * both appear for MIDI 66; C natural 4 and B sharp 3 both appear for MIDI 60
 * (the octave moving with the spelling). Double accidentals are deliberately
 * left out - they are not the alternatives a guitarist cycles between. This is
 * the ordered set the enharmonic-cycle control steps through, and every member
 * sounds identical.
 */
export function enharmonicSpellings(midi) {
  const out = [];
  for (const alter of [-1, 0, 1]) {
    const spelled = spellWithAlter(midi, alter);
    if (spelled) out.push(spelled);
  }
  return out;
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

/**
 * The `<duration>` value for a note of the given `<type>` carrying `dots`
 * augmentation dots (#183). A dot adds half the value again, a second dot half
 * of THAT again: one dot is 3/2 the plain value, two dots 7/4 - in general the
 * multiplier is (2^(dots+1) - 1) / 2^dots. So a dotted quarter is 1.5x a
 * quarter and a double-dotted quarter 1.75x, and the written `<dot/>`
 * element(s) and this `<duration>` stay exactly consistent (the divergence the
 * re-import round-trip checks for).
 *
 * `dots` is 0, 1 or 2 - the values this increment writes. Returns null when the
 * plain type has no duration at this divisions (see durationForType), when the
 * dotted value is not a whole number of units (a dotted 16th against a divisions
 * too coarse to halve again), or when `dots` is outside 0..2 - in every such
 * case the caller keeps the note as it was rather than writing a rounded,
 * wrong duration.
 */
export function durationForDots(type, divisions, dots = 0) {
  if (!Number.isInteger(dots) || dots < 0 || dots > 2) return null;
  const base = durationForType(type, divisions);
  if (base == null) return null;
  const numerator = 2 ** (dots + 1) - 1; // 1, 3, 7
  const denominator = 2 ** dots; //          1, 2, 4
  const value = (base * numerator) / denominator;
  return Number.isInteger(value) ? value : null;
}
