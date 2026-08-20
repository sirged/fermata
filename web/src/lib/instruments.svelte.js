// Where components read and write the player's instrument definitions. Like
// `settings`, a definition lives on the server (server/fermata/api.py's
// /api/instruments) rather than in browser storage, so the instruments a
// person owns follow them from phone to tablet to desk. Loaded once here;
// components read the live object rather than each fetching their own copy,
// and writes go back through the same place.
//
// All the pitch and draft arithmetic lives in ./pitch.js, which has no runes
// and no imports so every part of it can be tested without a browser. Only the
// server-backed state is here.
import { api } from "./api.js";
import { DEFAULT_REFERENCE_HZ, pitchFrequency, spellMidi } from "./pitch.js";
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
  draftReference,
  draftStrings,
  formatFrequency,
  isPlayable,
} from "./pitch.js";

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
      // `||`, not `??`: an Error with an empty message is common enough (an
      // aborted fetch is one) and would otherwise render as nothing at all,
      // which is the silent failure this whole branch exists to avoid.
      store.error = e?.message || "Could not load your instruments.";
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
  // Re-read the list rather than splicing the row in and re-sorting it here.
  // The order is the server's - ORDER BY name COLLATE NOCASE - and no client
  // comparator reproduces it: localeCompare puts "Élan" next to "E", while
  // NOCASE folds only ASCII and sorts it after "z". Getting that wrong means a
  // row sitting in one place after a save and a different place after a
  // reload, which reads as the list having lost track of itself.
  store.list = await api.instruments();
  return saved;
}

/** Forget an instrument. Returns how many scores stopped naming one as a
 * result, so the interface can say so instead of unlinking them silently. */
export async function removeInstrument(id) {
  const result = await api.deleteInstrument(id);
  store.list = store.list.filter((i) => i.id !== id);
  return result?.scores_unlinked ?? 0;
}

/** Sound one string on its own, through the renderer's synthesiser.
 *
 * Resolves with the MIDI note the synthesiser was actually handed, not with a
 * success flag - callers publish what comes back rather than what they meant to
 * send. That is the difference between an interface that can be observed to
 * play the right pitch and one that only reports the pitch it intended: an
 * audition that passed the open string instead of the capo'd one used to leave
 * every assertion passing, because they all read the row rather than the
 * argument. Null means the number was not one the synthesiser can sound. */
export function auditionPitch(midi) {
  return playPitch(midi);
}

/** A played MIDI note, named and measured. Built from the value that came BACK
 * from the synthesiser, so what the interface displays and publishes is
 * evidence of what was played rather than a restatement of the request. */
export function playedPitch(midi, referenceHz = DEFAULT_REFERENCE_HZ) {
  return {
    midi,
    pitch: spellMidi(midi),
    frequency: pitchFrequency(midi, referenceHz),
  };
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
