// Chord flash cards, both directions (issue #28) - built on the neck (#25)
// and the constraint model (#26), the way fret-to-note (#27) is. Show a
// shape, name the chord; or name a chord, place its notes on the neck.
//
// No runes and no browser - the pattern is fret-to-note.js's and ear-
// training.js's, for the same reason: which question gets asked and how
// the answer is worded is worth getting right on its own, testable without
// a page.
//
// THE TONE RULES ARE practice.js's, same as every other drill in this
// application: a wrong answer is the practice, not a shortfall. No accuracy
// percentage, no streak, no verdict word - only counts, stated.
// tests/unit/chords.spec.js checks every string this module produces
// against practice.js's own FORBIDDEN_WORDS list, the same check #184's
// fret-to-note.spec.js runs, extended to this second drill.
//
// GRADING IS ONE RULE, EITHER DIRECTION - the same idea fret-to-note.js's
// module docstring states for a tapped note: a chord is its pitch-class
// SET (chord-theory.js's chordTones), so "was the right chord named" and
// "was the right chord played" both reduce to comparing two tone sets,
// never to matching one canonical fingering. Naming a chord compares the
// chosen (root, quality) pair's tones to the question's; placing one
// compares whatever the player actually tapped - worked out from the
// instrument's own tuning, never from what they meant to tap, mirroring
// checkTapAnswer's rule in fret-to-note.js - to the question's tones. A
// shape shown to identify (shape_to_name) is always a REAL fingering drawn
// from chord-shapes.js's library, but a shape being BUILT (name_to_shape)
// is graded on what it sounds, not on matching that or any other one
// canonical voicing - there is more than one right way to play G major.
//
// STRUCTURED RESULTS, NOT A FREE-TEXT NOTE (issue #32), same promise as
// fret-to-note.js's, in a table of its own (trainer_chord_attempts) rather
// than trainer_attempts - see server/fermata/trainer.py's module docstring
// for why a chord does not fit the single-note columns that table's first
// drill committed to, and db.py's comment on the new table for the honest
// alternative: a sibling table shaped for what a chord attempt actually is.
import { countOrNone, formatDuration } from "../practice.js";
import { chordName, chordTones, chordsMatch } from "./chord-theory.js";
import { allShapes, shapeMatchesChord, shapeNotes } from "./chord-shapes.js";
import { groupInScope, keyNotes, scopeLabel as regionLabel } from "./constraints.js";
import { DEFAULT_FRET_COUNT, noteAt, pitchClass } from "./neck.js";

export const SHAPE_TO_NAME = "shape_to_name";
export const NAME_TO_SHAPE = "name_to_shape";
export const DIRECTIONS = [SHAPE_TO_NAME, NAME_TO_SHAPE];

export const DRILL = "chord_flashcards";

// "Majors and minors first, then sevenths, then barre chords" (issue #28) -
// three presets rather than two independent axes (quality and shape family)
// a player would otherwise have to reconcile by hand. Each preset says both
// which qualities are in play and which shape families they are drawn from.
export const FAMILIES = {
  major_minor: {
    label: "Major & minor",
    qualities: ["major", "minor"],
    shapeFamilies: ["open"],
  },
  sevenths: {
    label: "Sevenths",
    qualities: ["dominant7"],
    shapeFamilies: ["open"],
  },
  barre: {
    label: "Barre chords",
    qualities: ["major", "minor"],
    shapeFamilies: ["barre-e-major", "barre-e-minor", "barre-a-major", "barre-a-minor"],
  },
};

export const FAMILY_LIST = Object.keys(FAMILIES);

/** How the direction reads in a sentence. */
export function directionLabel(direction) {
  return direction === NAME_TO_SHAPE ? "name to shape" : "shape to name";
}

/** How a family preset reads in a sentence. */
export function familyLabel(family) {
  return FAMILIES[family]?.label ?? FAMILIES[FAMILY_LIST[0]].label;
}

/** A shape's fretted positions, each carrying the note it actually sounds -
 * what groupInScope (constraints.js) needs to decide whether a WHOLE shape
 * fits a region, not only whether its declared root does. */
function shapeFretsWithNotes(strings, shape) {
  return (shape?.frets ?? [])
    .map(({ string, fret }) => {
      const midi = noteAt(strings, string, fret);
      return midi == null ? null : { string, fret, note: pitchClass(midi) };
    })
    .filter(Boolean);
}

/** Whether every tone of a (root, quality) chord is a note of a key - the
 * meaning "scope to a key" carries for a chord (#26's key/note-set filter,
 * applied here rather than only to a single-note drill): a chord is only
 * offered when it is fully diatonic to the key, not merely rooted in it.
 * True with no key set. */
function chordInKey(root, quality, key) {
  if (!key) return true;
  const notes = keyNotes(key.root, key.quality ?? "major");
  const tones = chordTones(root, quality);
  if (!notes || !tones) return false;
  const set = new Set(notes);
  return tones.every((t) => set.has(t));
}

/** Every shape a scope and a family preset actually allow: drawn from
 * chord-shapes.js's library, narrowed to the preset's qualities and shape
 * families, to chords fully inside the scope's key (when one is set), and
 * to shapes that fit ENTIRELY inside the scope's string set and fret range
 * (constraints.js's groupInScope - a shape half inside a region is not a
 * shape the region allows). The one place both pickQuestion and "can this
 * even be asked" draw shapes from. */
export function chordPool(strings, scope = {}, family = FAMILY_LIST[0]) {
  const preset = FAMILIES[family] ?? FAMILIES[FAMILY_LIST[0]];
  const maxFret = Math.max(0, Number(scope.endFret ?? DEFAULT_FRET_COUNT));
  return allShapes(strings, { maxFret })
    .filter((s) => preset.shapeFamilies.includes(s.family) && preset.qualities.includes(s.quality))
    .filter((s) => chordInKey(s.root, s.quality, scope.key))
    .filter((s) => groupInScope(shapeFretsWithNotes(strings, s), scope));
}

/** Whether a scope and family preset have anything to ask about at all -
 * reachable the moment a region excludes every shape, or a key admits none
 * of the preset's chords. */
export function poolIsAskable(strings, scope, family) {
  return chordPool(strings, scope, family).length > 0;
}

/** Every distinct CHORD (root and quality, not every shape) a pool offers,
 * named and sorted - what shape_to_name's answer buttons are built from, so
 * the choices offered are exactly the chords this scope and family preset
 * can actually ask about rather than a fixed list unrelated to what is in
 * play. */
export function chordChoices(strings, scope, family) {
  const seen = new Map();
  for (const shape of chordPool(strings, scope, family)) {
    const key = `${shape.root}:${shape.quality}`;
    if (!seen.has(key)) {
      seen.set(key, { root: shape.root, quality: shape.quality, name: chordName(shape.root, shape.quality) });
    }
  }
  return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** "Major & minor, strings 1-6, frets 0-3" - the scope AND the family
 * preset, named together, since both narrow what this drill asks. */
export function scopeLabel(strings, scope, family) {
  return `${familyLabel(family)}, ${regionLabel(strings, scope)}`;
}

/** The next question: shape_to_name shows a real fingering and asks for its
 * name; name_to_shape names a chord and asks for it to be placed. Never
 * repeats the same CHORD (root and quality, not the exact shape - two
 * different barre positions naming the same chord back to back would still
 * read as the drill failing to advance) just asked, the same rule
 * fret-to-note.js's pickQuestion follows. Null when the pool has nothing to
 * ask. */
export function pickQuestion(strings, scope, family, direction, previous = null, rand = Math.random) {
  const pool = chordPool(strings, scope, family);
  if (!pool.length) return null;
  const eligible = previous
    ? pool.filter((s) => !chordsMatch(s.root, s.quality, previous.root, previous.quality))
    : pool;
  const from = eligible.length ? eligible : pool;
  const shape = from[Math.min(from.length - 1, Math.floor(rand() * from.length))];
  return {
    direction,
    root: shape.root,
    quality: shape.quality,
    // Only shape_to_name shows an actual fingering; name_to_shape asks for
    // one to be built, and grades whatever the player taps rather than one
    // canonical answer - see the module docstring.
    shape: direction === SHAPE_TO_NAME ? shape : null,
    // A playable fingering for THIS chord either way - "hear it" (issue
    // #28) needs real positions to turn into MIDI notes regardless of
    // direction, even on name_to_shape where nothing is shown yet.
    sound: shape.frets,
  };
}

/** shape_to_name: was the chosen chord the one shown - compared as tone
 * sets (chord-theory.js's chordsMatch), never as matching label strings. */
export function checkNameAnswer(question, givenRoot, givenQuality) {
  return chordsMatch(question.root, question.quality, givenRoot, givenQuality);
}

/** name_to_shape: does what was actually TAPPED sound the question's chord
 * - every required tone present, nothing extra - worked out from the
 * instrument's own tuning, never from which shape the player meant to
 * play. `tapped` is [{string, fret}, ...]. Returns the resolved notes
 * alongside `correct` so the caller can both show and log what actually
 * sounded, the same shape checkTapAnswer returns in fret-to-note.js. */
export function checkShapeAnswer(strings, question, tapped) {
  const positions = tapped ?? [];
  const notes = [...new Set(shapeNotes(strings, { frets: positions }))].sort();
  const correct = positions.length > 0
    && shapeMatchesChord(strings, { frets: positions }, question.root, question.quality);
  return { correct, notes };
}

/** The structured row to POST to /api/trainer/chord-attempts for one
 * answered question - issue #32's promise, kept per-question. `given` is
 * already resolved (checkNameAnswer's chosen root/quality, or
 * checkShapeAnswer's tapped positions/notes) - this function does no
 * grading of its own, matching trainer.py's rule that `correct` is
 * computed once, server-side. */
export function attemptPayload({ sessionId = null, question, given, responseMs = null }) {
  const base = {
    session_id: sessionId,
    drill: DRILL,
    direction: question.direction,
    target_root: question.root,
    target_quality: question.quality,
    response_ms: responseMs ?? null,
  };
  if (question.direction === SHAPE_TO_NAME) {
    return {
      ...base,
      target_shape: question.shape ? question.shape.frets.map(({ string, fret }) => ({ string, fret })) : null,
      given_root: given.root,
      given_quality: given.quality,
    };
  }
  return {
    ...base,
    given_notes: given.notes,
    given_shape: (given.positions ?? []).map(({ string, fret }) => ({ string, fret })),
  };
}

/** What to say about the answer just given - the chord's name either way,
 * and what was actually played when a shape was tapped. No verdict word:
 * the fact stated is what teaches, same rule as fret-to-note's
 * answerStatement. */
export function answerStatement(question, given, correct) {
  const name = chordName(question.root, question.quality);
  if (question.direction === SHAPE_TO_NAME) {
    const givenName = chordName(given.root, given.quality);
    return correct ? `That shape is ${name}.` : `That shape is ${name}. You named ${givenName ?? "nothing"}.`;
  }
  if (!given.notes?.length) return `${name} is ${chordTones(question.root, question.quality).join(", ")}.`;
  return correct
    ? `${name} is ${chordTones(question.root, question.quality).join(", ")} - that's what sounded.`
    : `${name} is ${chordTones(question.root, question.quality).join(", ")}. What was tapped sounded ${given.notes.join(", ")}.`;
}

/** How the drill has gone so far. Two counts, nothing else - no percentage,
 * no streak. Matches fret-to-note.js's progressStatement in shape. */
export function progressStatement({ asked = 0, correct = 0 } = {}) {
  const total = Math.max(0, Math.floor(Number(asked) || 0));
  if (!total) return "Nothing asked yet.";
  const questions = total === 1 ? "1 chord" : `${total} chords`;
  return `${questions}, ${countOrNone(correct)} answered correctly.`;
}

/** What goes in the practice session's free-text `note` - a human-readable
 * summary beside the structured per-question rows this drill also writes
 * (see attemptPayload and the module docstring on why both exist). */
export function sessionNote({ asked = 0, correct = 0, direction, strings, scope, family } = {}) {
  return `Chord flash cards, ${directionLabel(direction)}. ${progressStatement({ asked, correct })} ${scopeLabel(
    strings,
    scope,
    family,
  )}.`;
}

/** What was logged, said back once the drill has stopped. */
export function loggedStatement(seconds) {
  return `${formatDuration(seconds)} of chord practice is in your practice history.`;
}
