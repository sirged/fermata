// What a transcription READ off the page, and what it merely assumed.
//
// The extractor has always known the difference. It records how the meter and
// the key were obtained, and whether it recognised a tuning at all - and for a
// long time nothing in the interface read any of it, so a person looking at a
// transcription saw an assumed 4/4 presented exactly like a meter decoded off
// the printed digits (issue #103). 18% of first pages lose their printed meter
// (#90), so this is the common case rather than the corner.
//
// The underlying detection is NOT fixed here and is not meant to be - #90 and
// #80 are owned elsewhere. This turns a guess that was already being made into
// a guess a person can see.
//
// TWO WORDS, USED EVERYWHERE. `read` is "the source told us this"; `assumed` is
// "we chose this". An interface that spends a different word on each site -
// read, assumed, default, inferred, transcribed - teaches a reader none of
// them, because learning one says nothing about the next. So the tempo control
// says "assumed" where it used to say "default", the practice page says
// "assumed" where it used to say "inferred", and this file says both. The one
// survivor is "marked", which is not a synonym: a tempo the engraver printed IS
// a marking, and the word is the domain's own.
//
// THE TUNING IS NOT IN THAT SCHEME, and that is the interesting decision here.
// See tuningStatement.
//
// Pure functions, in their own module, so the wording can be tested without a
// browser. What actually renders is in ScoreCompare.svelte; a test that only
// checked these strings would prove nothing about whether they reach a screen.

// The extractor's own vocabulary for how a value was obtained - see
// tabextract.py's ts_source / key_source. Matched exactly rather than
// pattern-guessed, in both directions:
//
//   - a source string this does not recognise reports NOTHING rather than
//     defaulting to either answer. Calling an unrecognised future source "read"
//     would be the exact lie this file exists to prevent and calling it
//     "assumed" would be a false accusation, so silence is the only safe third
//     answer.
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

/** A MusicXML `fifths` count as a person would say it. */
export function keySignatureLabel(fifths) {
  if (fifths === 0) return "no key signature";
  const count = Math.abs(fifths);
  const accidental = fifths > 0 ? "sharp" : "flat";
  return `${count} ${accidental}${count === 1 ? "" : "s"}`;
}

/**
 * The meter and the key, each in the group naming how it was obtained.
 *
 * Takes the transcription object as the API presents it (see _PROVENANCE_KEYS
 * in server/fermata/api.py). Every field is optional: a hand-edited row, or one
 * extracted before the provenance was stored, records none of this, and the
 * honest answer there is three empty groups rather than a claim in either
 * direction.
 *
 * The value and its provenance always travel together - "4/4" appears in the
 * assumed group, never as a bare number somewhere else with the word "assumed"
 * a paragraph away. A reader who sees only one of the two learns nothing they
 * can act on.
 *
 * THE TUNING IS DELIBERATELY NOT HERE. It was, and it was wrong twice over -
 * see tuningStatement.
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

  return groups;
}

/** The standard guitar tuning the extractor falls back to - tabextract.py's
 * DEFAULT_TUNING. */
const STANDARD_TUNING = ["E2", "A2", "D3", "G3", "B3", "E4"];

function isStandardTuning(tuning) {
  return (
    Array.isArray(tuning) &&
    tuning.length === STANDARD_TUNING.length &&
    tuning.every((note, i) => note === STANDARD_TUNING[i])
  );
}

/**
 * What can honestly be said about the tuning, or null for "nothing" - which is
 * the answer on most scores.
 *
 * WHY THIS IS NOT IN THE READ/ASSUMED GROUPS ABOVE. Two reasons, both found by
 * measuring the library rather than by reasoning about it.
 *
 * 1. "READ" WAS NOT TRUE. The extractor finds a tuning by looking for the words
 *    "Drop D" in the page text. 100 scores match, and all sampled matches are
 *    genuine printed instructions - but 41 of those 100 carry a FURTHER printed
 *    instruction the extractor discards: 9 say to tune every string down a half
 *    step, so the recorded array is a semitone out, and 32 name a capo, so every
 *    sounding pitch is out. Describing those as "read from the page" turned a
 *    silent partial reading into a stated one, which is worse than saying
 *    nothing. A text match on one tuning name is recognition of a LABEL; it is
 *    not a reading of the tuning, and it cannot be called one while the same
 *    page carries instructions nobody parsed.
 *
 * 2. "ASSUMED" WAS TRUE BUT NOT WORTH SAYING. An unlabelled tuning is the
 *    standard six strings by assumption on 193 of 293 scores. Stating that on
 *    two thirds of the library put the unverified mark on two thirds of the
 *    library, which is the desensitisation the whole always-on argument exists
 *    to avoid, one level down - and its commonest instance was also its least
 *    informative.
 *
 * So the tuning speaks only when there is something real to say:
 *
 *   - an instruction we could not read     -> the reading is incomplete
 *   - a recognised name, nothing unread    -> the name was recognised, and that
 *                                             is all that was
 *   - standard strings, nothing recognised -> silence
 *
 * `kind` is "incomplete" or "recognised"; only "incomplete" carries the
 * unverified mark, so the mark stays rare enough to mean something.
 */
export function tuningStatement(t) {
  if (!t) return null;
  const unread = Array.isArray(t.tuning_unread) ? t.tuning_unread.filter(Boolean) : [];
  const label = typeof t.tuning_label === "string" && t.tuning_label ? t.tuning_label : null;

  // The tuning came from the instrument the player assigned this score (issue
  // #72). Said first and plainly, because it is the strongest source there is -
  // an explicit choice about the physical instrument, not something inferred
  // from the page - and it is worth confirming precisely because a player who
  // pointed a score at the wrong instrument would see the wrong strings here and
  // catch it. Not an "incomplete" mark: nothing was assumed, so nothing is
  // caveated.
  if (t.tuning_source === "instrument") {
    const strings = Array.isArray(t.tuning) ? t.tuning.join(" ") : "";
    return {
      kind: "recognised",
      text:
        `Tuning: from the instrument assigned to this score` +
        (strings ? ` (${strings})` : "") + `.`,
    };
  }

  if (unread.length) {
    const instructions = unread.join(" and ");
    const lead = label
      ? `the page names ${label}, and also says ${instructions}`
      : `the page says ${instructions}`;
    return {
      kind: "incomplete",
      text:
        `Tuning: ${lead}, which Fermata does not read — so the pitches sounded ` +
        `are not the pitches printed.`,
    };
  }

  if (label) {
    return {
      kind: "recognised",
      text: `Tuning: the page names ${label}. Nothing else about the tuning was read.`,
    };
  }

  // Standard strings and no name found. True, uninformative, and on two thirds
  // of the library - see above. The staff below draws the tuning it is using,
  // which is where a player who wants to check it looks.
  if (isStandardTuning(t.tuning)) return null;

  // Neither labelled nor standard. Reachable once #80 widens the recognition,
  // and there is nothing recorded about where it came from: "assumed" would be
  // a false accusation about a tuning that demonstrably differs from the one
  // that would have been assumed. Same rule sourceKind follows on a source it
  // does not recognise.
  return null;
}
