// Where components read and write the player's instrument definitions. Like
// `settings`, a definition lives on the server (server/fermata/api.py's
// /api/instruments) rather than in browser storage, so the instruments a
// person owns follow them from phone to tablet to desk. Loaded once here;
// components read the live object rather than each fetching their own copy,
// and writes go back through the same place.
import { api } from "./api.js";
import { playPitch } from "./score-render.js";

// Mirrors of server/fermata/instruments.py's bounds, so a number input can
// offer the right range instead of letting a person type a value only the
// server will refuse. The server stays the authority - it revalidates
// everything and its message is what gets shown - and
// server/tests/test_instruments_api.py parses these four constants out of this
// file and fails if they ever drift from the Python ones.
export const MAX_STRINGS = 24;
export const MAX_FRETS = 36;
export const MIN_REFERENCE_HZ = 300;
export const MAX_REFERENCE_HZ = 600;

export const DEFAULT_REFERENCE_HZ = 440;

const STEP_SEMITONES = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const PITCH_NAME = /^([A-Ga-g])(#{1,2}|b{1,2}|)(-?\d+)$/;

// Concert A, the note a reference pitch names.
const REFERENCE_MIDI = 69;

/** A pitch name ("E2", "F#2", "Eb3") as a MIDI note number, or null if it is
 * not one. The same grammar server/fermata/musicxml.py's parse_pitch_name
 * accepts, because a name this rejects is a name the server would reject too
 * and the form should say so before a save is attempted. */
export function pitchMidi(name) {
  const m = PITCH_NAME.exec(String(name ?? "").trim());
  if (!m) return null;
  const accidentals = m[2];
  const alter = accidentals.startsWith("#") ? accidentals.length : -accidentals.length;
  const midi = 12 * (Number(m[3]) + 1) + STEP_SEMITONES[m[1].toUpperCase()] + alter;
  return midi >= 0 && midi <= 127 ? midi : null;
}

/** The sounding frequency of a MIDI note under a reference pitch. Equal
 * temperament, and the reference names concert A - so a period tuning moves
 * every string with it. This is the one piece of arithmetic that has to exist
 * on both sides: the server computes it for a stored definition, and the form
 * needs it for a draft that has not been saved yet. */
export function pitchFrequency(midi, referenceHz = DEFAULT_REFERENCE_HZ) {
  return referenceHz * 2 ** ((midi - REFERENCE_MIDI) / 12);
}

/** How a frequency is written wherever one is shown. Two decimals: a cent at
 * the bottom of a bass's range is about 0.03 Hz, so fewer would hide the
 * difference a reference pitch makes. */
export function formatFrequency(hz) {
  return `${hz.toFixed(2)} Hz`;
}

/** Each string of a draft definition, as the editor shows it: number, the name
 * as typed, and - when that name parses - its MIDI note and frequency. String
 * numbers run opposite to list order, matching the server and the rest of the
 * codebase (a guitar's string 6 is first). */
export function draftStrings(draft) {
  const pitches = draft?.string_pitches ?? [];
  const reference = Number(draft?.reference_pitch) || DEFAULT_REFERENCE_HZ;
  return pitches.map((pitch, index) => {
    const midi = pitchMidi(pitch);
    return {
      number: pitches.length - index,
      pitch,
      midi,
      frequency: midi == null ? null : pitchFrequency(midi, reference),
    };
  });
}

const store = $state({ list: [], presets: [], loaded: false });

let loadPromise = null;

/** The live store: `list` is the saved instruments, `presets` the ones to
 * start from. Mutating either does not persist anything. */
export function getInstruments() {
  return store;
}

export function loadInstruments() {
  if (loadPromise) return loadPromise;
  loadPromise = Promise.all([api.instruments(), api.instrumentPresets()])
    .then(([list, presets]) => {
      store.list = list;
      store.presets = presets;
      store.loaded = true;
    })
    .catch(() => {
      // backend not deployed, network down - an empty list with no presets is
      // a usable (if useless) view, and the editor surfaces the real error the
      // moment a save is attempted
      store.loaded = true;
    });
  return loadPromise;
}

/** Create a definition, or replace an existing one. The server answers with
 * what it actually stored - including each string's note and frequency - and
 * that answer, never the draft, is what goes into the list. */
export async function saveInstrument(id, definition) {
  const saved = id == null
    ? await api.createInstrument(definition)
    : await api.saveInstrument(id, definition);
  const at = store.list.findIndex((i) => i.id === saved.id);
  if (at === -1) store.list = [...store.list, saved];
  else store.list = store.list.map((i) => (i.id === saved.id ? saved : i));
  // the server orders by name, and a rename can move a row
  store.list = [...store.list].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) || a.id - b.id,
  );
  return saved;
}

export async function removeInstrument(id) {
  await api.deleteInstrument(id);
  store.list = store.list.filter((i) => i.id !== id);
}

/** Sound one string on its own, through the renderer's synthesiser. */
export function auditionPitch(midi) {
  return playPitch(midi);
}

/** A draft the editor can hold, from a preset or from a saved instrument.
 * Presets arrive with everything a definition needs, so both cases are the
 * same copy - which is also why a preset can be saved unchanged. */
export function draftFrom(source) {
  return {
    id: source?.id ?? null,
    name: source?.name ?? "",
    fretted: source?.fretted ?? true,
    string_pitches: [...(source?.string_pitches ?? [])],
    fret_count: source?.fret_count ?? null,
    capo: source?.capo ?? null,
    reference_pitch: source?.reference_pitch ?? DEFAULT_REFERENCE_HZ,
  };
}

/** The draft as the API wants it. string_count is derived from the pitches
 * rather than tracked beside them, so the two cannot be sent disagreeing -
 * the server checks anyway, but there is no reason for this end to be the
 * thing that gets it wrong. */
export function definitionFrom(draft) {
  return {
    name: draft.name,
    fretted: draft.fretted,
    string_count: draft.string_pitches.length,
    string_pitches: draft.string_pitches,
    // Cleared rather than carried when unfretted: the server rejects a fret
    // count on an unfretted instrument outright, and the usual way to send one
    // is to pick a fretted preset and switch it over.
    fret_count: draft.fretted ? draft.fret_count : null,
    capo: draft.fretted ? (draft.capo ?? 0) : null,
    reference_pitch: draft.reference_pitch,
  };
}

/** Add or remove strings from a draft, keeping the list the right length.
 *
 * Strings are added and removed at the low end, because that is where an
 * instrument actually gains them - a seven-string guitar is a six-string with
 * a low B, and a four-string bass is a five-string without its low one. A new
 * string copies the current lowest pitch, which is always a valid name and
 * obvious to edit. */
export function resizeStrings(pitches, count) {
  const next = [...pitches];
  while (next.length > count && next.length > 0) next.shift();
  while (next.length < count) next.unshift(next[0] ?? "E2");
  return next;
}
