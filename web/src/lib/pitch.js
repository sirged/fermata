// Pitch arithmetic for the instruments editor: names to MIDI notes, MIDI notes
// to frequencies, and back.
//
// Deliberately a plain module with no runes and no imports, so it can be tested
// directly rather than only through the interface that uses it - see
// web/tests/unit/pitch.spec.js. That matters more here than anywhere else in
// the frontend: this is the one piece of arithmetic that has to exist on both
// sides of the wire. The server computes it for a stored definition
// (server/fermata/instruments.py), and this computes it for a draft the server
// has never seen. A mistake on this side is not a rendering glitch - it is a
// player tuning an instrument to the wrong pitch.
//
// The bounds below mirror server/fermata/instruments.py, and
// server/tests/test_instruments_api.py parses them out of this file so a drift
// fails a test rather than a tuning. The regex guard can only ever check that
// the NUMBERS agree, which is why the arithmetic using them has its own tests.

export const MIN_STRINGS = 1;
export const MAX_STRINGS = 24;
export const MIN_FRETS = 1;
export const MAX_FRETS = 36;
export const MIN_REFERENCE_HZ = 300;
export const MAX_REFERENCE_HZ = 600;
export const DEFAULT_REFERENCE_HZ = 440;
export const MAX_NAME_CHARS = 80;
// MIDI's range, floored at C0 rather than MIDI's own C-1 because MusicXML's
// octave type starts at 0 - see instruments.py.
export const MIN_MIDI = 12;
export const MAX_MIDI = 127;
// Concert A: the note a reference pitch names, and so the note the whole scale
// pivots around. Wrong by twelve here and every frequency is out by an octave.
export const REFERENCE_MIDI = 69;

const STEP_SEMITONES = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const PITCH_NAME = /^([A-Ga-g])(#{1,2}|b{1,2}|)(-?\d+)$/;

// How each pitch class is spelled with no key signature. Not an arbitrary
// choice of accidentals: this is exactly what musicxml.spell_pitch returns at
// fifths=0, so a sounding pitch is named here the way the server and the
// MusicXML emitter would name it. A test walks every MIDI note and fails if the
// two ever disagree. None of these spellings crosses an octave boundary (no B
// sharp, no C flat), which is why the octave can come straight off the MIDI
// number below.
const PITCH_CLASS_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];

/** A pitch name ("E2", "F#2", "Eb3") as a MIDI note number, or null if it is
 * not one, or falls outside the range a definition may hold. The same grammar
 * server/fermata/musicxml.py's parse_pitch_name accepts, because a name this
 * rejects is a name the server would reject too and the form should say so
 * before a save is attempted. */
export function pitchMidi(name) {
  const m = PITCH_NAME.exec(String(name ?? "").trim());
  if (!m) return null;
  const accidentals = m[2];
  const alter = accidentals.startsWith("#") ? accidentals.length : -accidentals.length;
  const midi = 12 * (Number(m[3]) + 1) + STEP_SEMITONES[m[1].toUpperCase()] + alter;
  return midi >= MIN_MIDI && midi <= MAX_MIDI ? midi : null;
}

/** A MIDI note as a pitch name - the inverse of pitchMidi, for naming the pitch
 * a capo produces. */
export function spellMidi(midi) {
  return `${PITCH_CLASS_NAMES[((midi % 12) + 12) % 12]}${Math.floor(midi / 12) - 1}`;
}

/** The sounding frequency of a MIDI note under a reference pitch. Twelve-tone
 * equal temperament: a semitone is the twelfth root of two, and the reference
 * names concert A, so moving the reference moves the whole scale with it.
 *
 * Returned unrounded, like the server's. formatFrequency is the only place a
 * frequency becomes text. */
export function pitchFrequency(midi, referenceHz = DEFAULT_REFERENCE_HZ) {
  return referenceHz * 2 ** ((midi - REFERENCE_MIDI) / 12);
}

/** How a frequency is written wherever one is shown - the ONLY place, so a
 * saved instrument's server-computed number and an unsaved draft's locally
 * computed one can never be rounded differently. Two decimals: a cent at the
 * bottom of a bass's range is about 0.03 Hz, so fewer would hide the difference
 * a reference pitch makes. */
export function formatFrequency(hz) {
  return `${hz.toFixed(2)} Hz`;
}
