// Fret to note, both directions (issue #27) - the first drill built on the
// neck component (#25). Show a position, name its note; or name a note, find
// a position that sounds it.
//
// No runes and no browser - the pattern is ear-training.js's, for the same
// reason: which question gets asked and how the answer is worded is worth
// getting right on its own, testable without a page.
//
// THE TONE RULES ARE practice.js's, same as ear-training.js, and bind the
// same way here: a wrong answer is the practice, not a shortfall. No
// accuracy percentage, no streak, no verdict word - only counts, stated.
// tests/unit/fret-to-note.spec.js checks every string this module produces
// against practice.js's own FORBIDDEN_WORDS list.
//
// STRUCTURED RESULTS, NOT A FREE-TEXT NOTE (issue #32). Unlike ear-training,
// which still logs its counts into the session's `note` (docs/practice-
// data.md says as much, deliberately, until a second trainer existed to
// decide otherwise), every question this drill asks is ALSO posted as one
// row to POST /api/trainer/attempts - see attemptPayload. The practice
// session this drill logs still carries the drill's own summary in its note,
// the same as every other activity, so a reader of the practice page is not
// left with a blank line; the STRUCTURED, QUERYABLE record lives in
// trainer_attempts instead of being the only place time was spent.
//
// SCOPING (string set, fret range - and now a key too) IS constraints.js's
// (issue #26), NOT THIS FILE'S. This drill had the first version of that
// arithmetic; it has been moved out so the chord flash cards (#28) can use
// the identical rule rather than a second copy of it, and this module keeps
// its own three names (scopePositions/scopeIsAskable/scopeLabel) as a
// re-export so nothing that already imports them - FretToNote.svelte, this
// file's own tests - has to change.
import { countOrNone, formatDuration } from "../practice.js";
import { scopeIsAskable, scopeLabel, scopePositions } from "./constraints.js";
import { noteAt, pitchClass } from "./neck.js";

export { scopeIsAskable, scopeLabel, scopePositions } from "./constraints.js";

export const POSITION_TO_NOTE = "position_to_note";
export const NOTE_TO_POSITION = "note_to_position";
export const DIRECTIONS = [POSITION_TO_NOTE, NOTE_TO_POSITION];

export const DRILL = "fret_to_note";

/** How the direction reads in a sentence. */
export function directionLabel(direction) {
  return direction === NOTE_TO_POSITION ? "note to position" : "position to note";
}

/** The next question: a position (position-to-note) or a note
 * (note-to-position), drawn uniformly from what the scope allows. Never
 * repeats the position/note just asked, the same rule pickTarget in
 * ear-training.js follows and for the same reason - a repeat reads as the
 * drill having failed to advance. Null when the scope has nothing to ask. */
export function pickQuestion(strings, scope, direction, previous = null, rand = Math.random) {
  const pool = scopePositions(strings, scope);
  if (!pool.length) return null;

  if (direction === POSITION_TO_NOTE) {
    const eligible = previous
      ? pool.filter((p) => !(p.string === previous.string && p.fret === previous.fret))
      : pool;
    const from = eligible.length ? eligible : pool;
    const picked = from[Math.min(from.length - 1, Math.floor(rand() * from.length))];
    return { direction, string: picked.string, fret: picked.fret, note: picked.note };
  }

  const notes = [...new Set(pool.map((p) => p.note))];
  const eligible = previous ? notes.filter((n) => n !== previous.note) : notes;
  const from = eligible.length ? eligible : notes;
  const note = from[Math.min(from.length - 1, Math.floor(rand() * from.length))];
  return { direction, note, string: null, fret: null };
}

/** position-to-note: was the chosen note the one asked about. */
export function checkPositionAnswer(question, chosenNote) {
  return chosenNote === question.note;
}

/** note-to-position: the note that SOUNDS at the tapped position, worked out
 * from the instrument's own tuning - never from what the player meant to
 * tap - and whether it is the one that was asked for. Mirrors ear-
 * training.js's own rule ("the question is built from what was SOUNDED"):
 * the tapped position is the ground truth, and the note it produces is what
 * gets graded and what gets sent to the server (see attemptPayload). */
export function checkTapAnswer(strings, question, tappedString, tappedFret) {
  const midi = noteAt(strings, tappedString, tappedFret);
  if (midi == null) return { correct: false, note: null };
  const note = pitchClass(midi);
  return { correct: note === question.note, note };
}

/** The structured row to POST to /api/trainer/attempts for one answered
 * question - issue #32's promise, kept per-question rather than folded into
 * the session's free-text note. `given.note` must already be resolved (via
 * checkPositionAnswer's chosen note, or checkTapAnswer's `note`) - this
 * function does no grading of its own, matching db.py/trainer.py's own rule
 * that `correct` is computed once, server-side, from target_note vs
 * given_note. */
export function attemptPayload({ sessionId = null, question, given, responseMs = null }) {
  const base = {
    session_id: sessionId,
    drill: DRILL,
    direction: question.direction,
    response_ms: responseMs ?? null,
  };
  if (question.direction === POSITION_TO_NOTE) {
    return {
      ...base,
      target_string: question.string,
      target_fret: question.fret,
      target_note: question.note,
      given_note: given.note,
    };
  }
  return {
    ...base,
    target_note: question.note,
    given_string: given.string,
    given_fret: given.fret,
    given_note: given.note,
  };
}

/** What to say about the answer just given - the note either way, and which
 * position when a tap was involved. No verdict word: the fact stated is what
 * teaches, same rule as ear-training's roundStatement. */
export function answerStatement(question, given, correct) {
  if (question.direction === POSITION_TO_NOTE) {
    const at = `String ${question.string}, fret ${question.fret} is ${question.note}.`;
    return correct ? at : `${at} You named ${given.note}.`;
  }
  const at = `String ${given.string}, fret ${given.fret} is ${given.note}.`;
  return correct ? at : `${at} ${question.note} is elsewhere on the neck.`;
}

/** How the drill has gone so far. Two counts, nothing else - no percentage,
 * no streak. Matches ear-training.js's progressStatement in shape. */
export function progressStatement({ asked = 0, correct = 0 } = {}) {
  const total = Math.max(0, Math.floor(Number(asked) || 0));
  if (!total) return "Nothing asked yet.";
  const questions = total === 1 ? "1 question" : `${total} questions`;
  return `${questions}, ${countOrNone(correct)} answered correctly.`;
}

/** What goes in the practice session's free-text `note` - a human-readable
 * summary beside the structured per-question rows this drill also writes
 * (see attemptPayload and the module docstring on why both exist).
 *
 * `scope: null` DROPS THE SCOPE SENTENCE, and is how a session that carries
 * a preset id asks for its note (issue #236). What was practised is then in
 * a column - practice_sessions.preset_id, joined to the named scope - and
 * repeating it here as prose would put the same fact in two places, one of
 * them the free text docs/practice-data.md's rule for this data layer exists
 * to keep facts out of. The counts stay either way: they are about how the
 * session went, not about what it was scoped to.
 *
 * An unnamed scope still gets the sentence, unchanged, because for that
 * session it is the only trace of what was narrowed. */
export function sessionNote({ asked = 0, correct = 0, direction, strings, scope } = {}) {
  const summary = `Fret to note, ${directionLabel(direction)}. ${progressStatement({ asked, correct })}`;
  if (scope === null) return summary;
  return `${summary} ${scopeLabel(strings, scope)}.`;
}

/** What was logged, said back once the drill has stopped. */
export function loggedStatement(seconds) {
  return `${formatDuration(seconds)} of fretboard practice is in your practice history.`;
}
