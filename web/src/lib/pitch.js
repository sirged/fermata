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

/** The reference pitch a draft's frequencies should be computed at, or null when
 * the field holds something unusable.
 *
 * Three cases, and they are not the same: an EMPTY field is unset and takes the
 * default (a number input reads back as null when cleared, and the server would
 * default it too). A number inside the bounds is used. Anything else - negative,
 * zero, out of range - is neither unset nor usable and must NOT quietly become
 * the default: doing that showed every string at -18.73 Hz, left Save enabled,
 * and then stored 440, so the screen and the database disagreed with nothing
 * said. */
export function draftReference(draft) {
  const raw = draft?.reference_pitch;
  if (raw == null || raw === "") return DEFAULT_REFERENCE_HZ;
  const hz = Number(raw);
  if (!Number.isFinite(hz)) return null;
  return hz >= MIN_REFERENCE_HZ && hz <= MAX_REFERENCE_HZ ? hz : null;
}

/** Each string of an unsaved draft, in the same shape the server sends for a
 * saved one, so one renderer displays either.
 *
 * This exists ONLY for a draft: a saved instrument's strings come from the
 * server. The capo is applied here for the same reason it is there - it raises
 * every string, so it decides what the instrument sounds, and the sounding pitch
 * is what gets played and matched by ear.
 *
 * `midi` is null for a name that does not parse, which is the ordinary state of
 * a half-typed one; the frequencies are null for that, and also when the
 * reference pitch itself is unusable. */
export function draftStrings(draft) {
  const pitches = draft?.string_pitches ?? [];
  const reference = draftReference(draft);
  const capo = (draft?.fretted && Number(draft.capo)) || 0;
  return pitches.map((pitch, index) => {
    const midi = pitchMidi(pitch);
    const sounding = midi == null ? null : midi + capo;
    const measurable = reference != null;
    return {
      number: pitches.length - index,
      pitch,
      midi,
      frequency: midi != null && measurable ? pitchFrequency(midi, reference) : null,
      sounding_midi: sounding,
      sounding_pitch: sounding == null ? null : spellMidi(sounding),
      sounding_frequency:
        sounding != null && measurable ? pitchFrequency(sounding, reference) : null,
    };
  });
}

/** Whether a string can be sounded. A nominal pitch is bounded by pitchMidi, but
 * a capo can push the sounding pitch off the top of MIDI - the server refuses to
 * store that, and until it is saved the draft has to show it as unplayable
 * rather than offer a button that does nothing. */
export function isPlayable(string) {
  const midi = string?.sounding_midi;
  return midi != null && midi >= MIN_MIDI && midi <= MAX_MIDI;
}
