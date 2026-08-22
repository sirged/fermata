// What a transcription READ off the page, and what it merely assumed.
//
// The extractor has always known the difference. It records how the meter and
// the key were obtained, and whether it recognised a tuning at all - and for a
// long time nothing in the interface read any of it, so a person looking at a
// transcription saw an assumed 4/4 and an assumed standard tuning presented
// exactly like a meter decoded off the printed digits (issue #103). 18% of
// first pages lose their printed meter (#90) and every score not literally
// labelled "Drop D" is read as standard tuning (#80), so this is the common
// case rather than the corner.
//
// The underlying detection is NOT fixed here and is not meant to be - #90 and
// #80 are owned elsewhere. This turns a guess that was already being made into
// a guess a person can see.
//
// Pure functions, in their own module, so the wording can be tested without a
// browser. What actually renders is in ScoreCompare.svelte; a test that only
// checked these strings would prove nothing about whether they reach a screen.

// The extractor's own vocabulary for how a value was obtained - see
// tabextract.py's ts_source / key_source. Matched exactly rather than
// pattern-guessed, in both directions:
//
//   - a source string this does not recognise reports NOTHING rather than
//     defaulting to either answer. Calling an unrecognised future source
//     "assumed" would be a false accusation and calling it "read" would be the
//     exact lie this file exists to prevent, so silence is the only safe
//     third answer.
const READ_SOURCES = new Set([
  // Decoded from the engraved glyphs themselves.
  "glyph-decoded",
  // Read off the page by the older digit-shape detector rather than the glyph
  // decoder. Still read from the page, which is the distinction being drawn.
  "auto-detected",
]);
// Supplied by the person, in the "Time signature" box beside the transcribe
// button. Neither read nor assumed, and worth its own line: it is the one
// case where a reader already knows where the number came from and seeing it
// echoed back confirms it was actually used.
const SUPPLIED_SOURCES = new Set(["manual override"]);
// "not detected", "not detected (assumed 4/4)", "not detected (assumed no key
// signature)" - the extractor states what it fell back to in the parenthesis,
// which is why this is a prefix rather than a set.
const ASSUMED_PREFIX = "not detected";

/** "read" | "assumed" | "supplied" | null (nothing recorded, or a source
 * string this version does not recognise). */
export function sourceKind(source) {
  if (typeof source !== "string" || !source) return null;
  if (READ_SOURCES.has(source)) return "read";
  if (SUPPLIED_SOURCES.has(source)) return "supplied";
  if (source.startsWith(ASSUMED_PREFIX)) return "assumed";
  return null;
}

/** The standard guitar tuning the extractor falls back to - tabextract.py's
 * DEFAULT_TUNING. Only used to decide whether "standard tuning" is a true
 * description of what was actually used; an unlabelled tuning that is NOT
 * this one is named by its own strings rather than described with a word that
 * would be wrong. */
const STANDARD_TUNING = ["E2", "A2", "D3", "G3", "B3", "E4"];

function isStandardTuning(tuning) {
  return (
    Array.isArray(tuning) &&
    tuning.length === STANDARD_TUNING.length &&
    tuning.every((note, i) => note === STANDARD_TUNING[i])
  );
}

/** A MusicXML `fifths` count as a person would say it. */
export function keySignatureLabel(fifths) {
  if (fifths === 0) return "no key signature";
  const count = Math.abs(fifths);
  const accidental = fifths > 0 ? "sharp" : "flat";
  return `${count} ${accidental}${count === 1 ? "" : "s"}`;
}

/**
 * A tuning as a person would say it, or null when there is nothing this can
 * say about it honestly.
 *
 * The null case is the interesting one: a tuning that is NEITHER labelled nor
 * the standard one. Today the extractor cannot produce that - it recognises
 * exactly one non-standard tuning and labels it - but the moment #80 widens
 * the recognition it can, and the answer must not be "standard tuning
 * assumed". A tuning that demonstrably differs from standard cannot have come
 * from an assumption of standard, so calling it assumed is a false accusation,
 * and nothing records where it did come from. Same rule sourceKind already
 * follows on a source string it does not recognise: report neither rather than
 * pick the safer-sounding wrong answer.
 */
export function tuningDescription(tuning, label) {
  if (typeof label === "string" && label) return `${label} tuning`;
  if (isStandardTuning(tuning)) return "standard tuning";
  return null;
}

/**
 * The three groups a transcription's meter, key and tuning fall into, each an
 * array of plain descriptions ready to read out.
 *
 * Takes the transcription object as the API presents it (see
 * _PROVENANCE_KEYS in server/fermata/api.py). Every field is optional: a
 * hand-edited row, or one extracted before the provenance was stored, records
 * none of this, and the honest answer there is three empty groups rather than
 * a claim in either direction.
 *
 * The value and its provenance always travel together - "4/4" appears in the
 * assumed group, never as a bare number somewhere else with the word
 * "assumed" a paragraph away. A reader who sees only one of the two learns
 * nothing they can act on.
 */
export function transcriptionProvenance(t) {
  const groups = { read: [], assumed: [], supplied: [] };
  if (!t) return groups;

  const ts = t.time_signature;
  if (Array.isArray(ts) && ts.length === 2) {
    const kind = sourceKind(t.time_signature_source);
    if (kind) groups[kind].push(`${ts[0]}/${ts[1]}`);
  }

  if (typeof t.key_fifths === "number") {
    const kind = sourceKind(t.key_signature_source);
    if (kind) groups[kind].push(keySignatureLabel(t.key_fifths));
  }

  // The tuning has no source field of its own, and does not need one: the
  // extractor recognises exactly one non-standard tuning, by finding the words
  // "Drop D" in the page text, and records the label when it does. So a label
  // IS the reading, and the absence of one ALONGSIDE THE STANDARD SIX STRINGS
  // is the assumption - which is #80's whole complaint. An unlabelled tuning
  // that is not the standard one is neither, and tuningDescription returns null
  // for it; so does an edited row, which records no tuning at all.
  const tuning = tuningDescription(t.tuning, t.tuning_label);
  if (tuning) groups[t.tuning_label ? "read" : "assumed"].push(tuning);

  return groups;
}
