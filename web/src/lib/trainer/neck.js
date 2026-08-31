// The neck: pure geometry and note arithmetic for the interactive fretboard
// (issue #25) - the shared foundation every fretboard trainer (#26, #27, #29)
// builds on.
//
// No runes and no browser, for the same reason pitch.js and ear-training.js
// have none: the part of this worth getting right is arithmetic - which note
// sounds at a string and a fret - and arithmetic is what a unit test can hold
// to account without a page. Neck.svelte draws what this computes; it adds no
// note-naming or position logic of its own.
//
// READS TUNING FROM THE INSTRUMENT, NOT A HARDCODED SIX STRINGS. An
// instrument definition (server/fermata/instruments.py, loaded here through
// instruments.svelte.js) already carries exactly what a neck needs: how many
// strings, what each is tuned to, and how many frets. stringsFromInstrument
// reads that - a seven-string guitar or a dropped tuning draws its own real
// strings - and falls back to a plain standard six-string guitar only when
// there is no instrument to read from at all (defaultStrings), which is a
// starting point rather than an assumption. An UNFRETTED instrument (a
// violin) has no frets and is deliberately not offered a neck at all; see
// stringsFromInstrument's own comment.
import { MAX_MIDI, MIN_MIDI, pitchMidi, spellMidi } from "../pitch.js";

export const MIN_FRETS = 1;

// The default when nothing else is known: standard guitar, EADGBE. Matches
// server/fermata/instruments.py's "guitar-standard" preset exactly, and is
// used only by defaultStrings/defaultFretCount below - anywhere an
// instrument is available, its own tuning is read instead.
const STANDARD_TUNING_PITCHES = ["E2", "A2", "D3", "G3", "B3", "E4"];
export const DEFAULT_FRET_COUNT = 12;

/** Standard six-string guitar strings, numbered 6 (low E) to 1 (high e) - the
 * STRING ORDER convention instruments.py documents, applied to a tuning that
 * exists independently of any saved instrument. The one hardcoded tuning in
 * this module, and only a fallback - see the module docstring. */
export function defaultStrings() {
  return STANDARD_TUNING_PITCHES.map((pitch, index) => ({
    number: STANDARD_TUNING_PITCHES.length - index,
    midi: pitchMidi(pitch),
  }));
}

/** The strings to draw a neck from: an instrument's own, when it has some to
 * give, or the standard six-string fallback.
 *
 * Reads `instrument.strings` - the shape instruments.svelte.js's saved rows
 * and pitch.js's draftStrings both already produce (each entry carrying at
 * least `number` and `midi`) - so a seven-string guitar, a dropped tuning, or
 * a four-string bass draws its real strings rather than six assumed ones.
 * Prefers `sounding_midi` over `midi` where present, because a capo changes
 * what a string actually sounds and that is what a fretboard drill has to
 * test against.
 *
 * An UNFRETTED instrument (fretted: false - a violin) has no frets, and
 * position reasoning does not apply to it any more than it does on the
 * server (see instruments.py's module docstring on why fret_count is
 * rejected outright there). Rather than inventing frets for one, this falls
 * back to the standard guitar - which is a known first-cut limitation, not a
 * silent guess: the fretboard trainer route only offers itself against a
 * fretted instrument or the fallback, and issue #29 (a "which instrument"
 * picker aware of this) is the filed follow-up. */
export function stringsFromInstrument(instrument) {
  const rows = instrument?.fretted ? instrument.strings : null;
  if (!Array.isArray(rows) || !rows.length) return defaultStrings();
  const strings = rows
    .map((s) => ({ number: s.number, midi: s.sounding_midi ?? s.midi }))
    .filter((s) => Number.isInteger(s.number) && Number.isFinite(s.midi));
  return strings.length ? strings : defaultStrings();
}

/** How many frets to draw: an instrument's own fret_count, or
 * DEFAULT_FRET_COUNT when there is none to read (no instrument, or an
 * unfretted one - see stringsFromInstrument). */
export function fretCountFromInstrument(instrument, fallback = DEFAULT_FRET_COUNT) {
  const count = instrument?.fretted ? Number(instrument.fret_count) : null;
  return Number.isFinite(count) && count > 0 ? count : fallback;
}

/** The MIDI note sounding at a string and a fret, or null when that string
 * number is not one of `strings` or the result falls outside MIDI's own
 * range (pitch.js's MIN_MIDI/MAX_MIDI - the same bound an instrument
 * definition is checked against server-side). */
export function noteAt(strings, stringNumber, fret) {
  const string = (strings ?? []).find((s) => s.number === stringNumber);
  if (!string || !Number.isFinite(string.midi)) return null;
  const midi = string.midi + fret;
  return midi >= MIN_MIDI && midi <= MAX_MIDI ? midi : null;
}

/** A MIDI note's pitch class - the note on its own, with no octave, which is
 * how a fretboard position is ordinarily named ("that's a C", not "that's a
 * C4"). Derived from pitch.js's spellMidi by dropping the trailing octave
 * digits rather than from a second spelling table, so this can never
 * disagree with the one place pitch.js already spells a note - and PITCH_
 * CLASSES below is exactly the twelve values it can produce. */
export function pitchClass(midi) {
  return spellMidi(midi).replace(/-?\d+$/, "");
}

// The twelve pitch classes pitchClass can produce, in that order - one sharp
// OR flat per class, matching spellMidi's own table (which is itself
// musicxml.spell_pitch at fifths=0). Exported so a drill's note-name choices
// offer exactly these, and server/fermata/trainer.py's PITCH_CLASSES is kept
// to the identical list by hand, checked by tests/unit/neck.spec.js reading
// both.
export const PITCH_CLASSES = [
  "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B",
];

/** A stable key for one position, used to key a marker map or compare two
 * positions for equality. Not meant to be parsed back apart - callers that
 * need the numbers keep them separately. */
export function posKey(stringNumber, fret) {
  return `${stringNumber}:${fret}`;
}

/** Every playable position across `strings`, from `startFret` to `endFret`
 * inclusive, each with its note. The one place both Neck.svelte and a
 * drill's question generator draw positions from, so "every position on the
 * neck" cannot mean something different to the renderer than it means to a
 * question. Positions that fall outside MIDI's range (noteAt returning null)
 * are simply not included, rather than raising - the neck draws what is
 * playable and says nothing about what is not. */
export function positions(strings, startFret = 0, endFret = DEFAULT_FRET_COUNT) {
  const out = [];
  for (const string of strings ?? []) {
    for (let fret = Math.max(0, startFret); fret <= endFret; fret++) {
      const midi = noteAt(strings, string.number, fret);
      if (midi == null) continue;
      out.push({ string: string.number, fret, midi, note: pitchClass(midi) });
    }
  }
  return out;
}

/** Every position (within the given range, on the given strings) that sounds
 * a given pitch class - what "highlight this note across the neck" means,
 * and what a note-to-position question's full answer is once revealed. */
export function positionsForNote(strings, note, startFret = 0, endFret = DEFAULT_FRET_COUNT) {
  return positions(strings, startFret, endFret).filter((p) => p.note === note);
}

// Frets a real fretboard marks with an inlay dot: the ordinary single-dot
// positions, and the double dot at the octave. Not tied to any one
// instrument's fret count - a dot beyond fretCount is simply never drawn,
// the same way a position beyond it is never in `positions`.
const SINGLE_DOTS = new Set([3, 5, 7, 9, 15, 17, 19, 21]);
const DOUBLE_DOTS = new Set([12, 24]);

/** How many inlay dots a fret carries: 0, 1, or 2. */
export function inlayDots(fret) {
  if (DOUBLE_DOTS.has(fret)) return 2;
  if (SINGLE_DOTS.has(fret)) return 1;
  return 0;
}
