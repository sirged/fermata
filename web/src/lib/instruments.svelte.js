// Where components read and write the player's instrument definitions. Like
// `settings`, a definition lives on the server (server/fermata/api.py's
// /api/instruments) rather than in browser storage, so the instruments a
// person owns follow them from phone to tablet to desk. Loaded once here;
// components read the live object rather than each fetching their own copy,
// and writes go back through the same place.
//
// The pitch arithmetic lives in ./pitch.js, which has no runes and no imports
// so it can be tested on its own.
import { api } from "./api.js";
import {
  DEFAULT_REFERENCE_HZ,
  MAX_MIDI,
  MIN_MIDI,
  pitchFrequency,
  pitchMidi,
  spellMidi,
} from "./pitch.js";
import { playPitch } from "./score-render.js";

export {
  DEFAULT_REFERENCE_HZ,
  MAX_FRETS,
  MAX_NAME_CHARS,
  MAX_REFERENCE_HZ,
  MAX_STRINGS,
  MIN_FRETS,
  MIN_REFERENCE_HZ,
  MIN_STRINGS,
  formatFrequency,
} from "./pitch.js";

/** Each string of an unsaved draft, in the same shape the server sends for a
 * saved one, so one renderer displays either.
 *
 * This exists ONLY for a draft: a saved instrument's strings come from the
 * server. The capo is applied here for the same reason it is there - it raises
 * every string, so it decides what the instrument sounds, and the sounding
 * pitch is what gets played and matched by ear. `midi` is null for a name that
 * does not parse, which is the ordinary state of a half-typed one. */
export function draftStrings(draft) {
  const pitches = draft?.string_pitches ?? [];
  const reference = Number(draft?.reference_pitch) || DEFAULT_REFERENCE_HZ;
  const capo = (draft?.fretted && Number(draft.capo)) || 0;
  return pitches.map((pitch, index) => {
    const midi = pitchMidi(pitch);
    const sounding = midi == null ? null : midi + capo;
    return {
      number: pitches.length - index,
      pitch,
      midi,
      frequency: midi == null ? null : pitchFrequency(midi, reference),
      sounding_midi: sounding,
      sounding_pitch: sounding == null ? null : spellMidi(sounding),
      sounding_frequency: sounding == null ? null : pitchFrequency(sounding, reference),
    };
  });
}

/** Whether a string can be sounded. A nominal pitch is bounded by pitchMidi,
 * but a capo can push the sounding pitch off the top of MIDI - the server
 * refuses to store that, and until it is saved the draft has to show it as
 * unplayable rather than offer a button that does nothing. */
export function isPlayable(string) {
  const midi = string?.sounding_midi;
  return midi != null && midi >= MIN_MIDI && midi <= MAX_MIDI;
}

const store = $state({ list: [], presets: [], loaded: false, error: "" });

let loadPromise = null;

/** The live store: `list` is the saved instruments, `presets` the ones to start
 * from, `error` why the last load failed. Mutating any of it persists nothing. */
export function getInstruments() {
  return store;
}

export function loadInstruments() {
  if (loadPromise) return loadPromise;
  const attempt = Promise.all([api.instruments(), api.instrumentPresets()])
    .then(([list, presets]) => {
      store.list = list;
      store.presets = presets;
      store.error = "";
      store.loaded = true;
    })
    .catch((e) => {
      // A FAILURE IS NOT CACHED. Unlike `settings`, there is nothing usable to
      // fall back on: a preset is the only way to start a definition, so one
      // hiccup while the settings view was loading would leave an empty list
      // above an empty dropdown, with the feature dead until a full page
      // reload - navigating away and back would hand back this same rejected
      // promise. Clearing it lets the next caller (a remount, or the retry
      // button) actually try again, and the message gives something to retry
      // from rather than an unexplained "no instruments yet".
      if (loadPromise === attempt) loadPromise = null;
      store.loaded = true;
      store.error = e?.message ?? "Could not load your instruments.";
    });
  loadPromise = attempt;
  return attempt;
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

/** Forget an instrument. Returns how many scores stopped naming one as a
 * result, so the interface can say so instead of unlinking them silently. */
export async function removeInstrument(id) {
  const result = await api.deleteInstrument(id);
  store.list = store.list.filter((i) => i.id !== id);
  return result?.scores_unlinked ?? 0;
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
    kind: source?.kind ?? "string",
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
  const reference = Number(draft.reference_pitch);
  return {
    kind: draft.kind ?? "string",
    name: draft.name,
    fretted: draft.fretted,
    string_count: draft.string_pitches.length,
    string_pitches: draft.string_pitches,
    // Cleared rather than carried when unfretted: the server rejects a fret
    // count on an unfretted instrument outright, and the usual way to send one
    // is to pick a fretted preset and switch it over.
    fret_count: draft.fretted ? draft.fret_count : null,
    capo: draft.fretted ? (draft.capo ?? 0) : null,
    // An emptied number input reads back as null, which means UNSET, not
    // invalid - so it takes the same default a fresh definition would, which is
    // also the pitch the frequencies on screen were computed at. Forwarding the
    // null instead produced a 422 about a field the form was still showing
    // plausible numbers for.
    reference_pitch: reference > 0 ? reference : DEFAULT_REFERENCE_HZ,
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
