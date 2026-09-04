// Grouping stored fret-to-note attempts into "which positions get answered
// incorrectly" (issue #235) - client-side, over the existing filterable
// GET /api/trainer/attempts?correct=false, per docs/practice-data.md's
// decision against a bespoke aggregate endpoint: "a client asking which
// positions am I weak on filters correct=false and groups the results
// itself". No runes and no browser here - same pattern as fret-to-note.js:
// the grouping and the wording are worth getting right on their own,
// testable without a page.
//
// COUNTS ONLY - the same rule the rest of this drill already holds to (see
// fret-to-note.js's own header and docs/practice-data.md's "Counts, never a
// rate"). No percentage, no rate, no ranking beyond "these counts, largest
// first".

/** Only a position_to_note row has a target position at all - see
 * docs/practice-data.md's trainer_attempts table: target_string/target_fret
 * are NULL on note_to_position, where a question named a note and not a
 * single expected position. Grouping those in would invent a position that
 * was never asked about. */
function hasTargetPosition(attempt) {
  return attempt?.target_string != null && attempt?.target_fret != null;
}

/** Group a list of attempts (already filtered to `correct: false` by the
 * caller - see PositionCounts.svelte) by (target_string, target_fret),
 * largest count first. Ties broken by string then fret, ascending, so the
 * same rows always come back in the same order - nothing here is randomised,
 * so nothing reading it should look randomised either. */
export function positionCounts(attempts, { limit = 5 } = {}) {
  const counts = new Map();
  for (const attempt of attempts ?? []) {
    if (!hasTargetPosition(attempt)) continue;
    const key = `${attempt.target_string}:${attempt.target_fret}`;
    const entry = counts.get(key) ?? {
      string: attempt.target_string,
      fret: attempt.target_fret,
      count: 0,
    };
    entry.count += 1;
    counts.set(key, entry);
  }
  return [...counts.values()]
    .sort((a, b) => b.count - a.count || a.string - b.string || a.fret - b.fret)
    .slice(0, Math.max(0, Math.floor(Number(limit) || 0)));
}

/** One position's own line - a count, never a rate, matching
 * progressStatement's shape elsewhere in this drill. */
export function positionStatement({ string, fret, count }) {
  const times = count === 1 ? "1 time" : `${count} times`;
  return `String ${string}, fret ${fret} - answered incorrectly ${times}.`;
}

/** What the panel says when there is nothing to group yet. */
export const NO_POSITIONS_STATEMENT = "Nothing has been answered incorrectly yet.";
