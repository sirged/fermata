// The constraint model (issue #26) - scoping ANY drill built on the neck to
// what a player is actually working on: particular strings, a fret range,
// and (new here) a key or note set. Shared infrastructure, not a per-game
// setting, so the same scope object narrows fret-to-note (#27) and the chord
// flash cards (#28) alike.
//
// FACTORED OUT OF fret-to-note.js, NOT REWRITTEN. Issue #27 built the first
// version of string+fret-range scoping directly inside that drill; this
// module is that same arithmetic moved here so a second drill does not have
// to re-derive it, plus one addition (a key/note-set filter) that neither
// drill exposed before. fret-to-note.js now imports scopePositions,
// scopeIsAskable and scopeLabel from here rather than defining its own - see
// that file's own note - so the drill that already had tests covering this
// arithmetic keeps proving it, unchanged.
//
// A "scope" is a plain object:
//   stringNumbers  string numbers to allow, or omitted/empty for "every
//                  string offered" - never treated as "no strings".
//   startFret      the lowest fret to allow (default 0).
//   endFret        the highest fret to allow (default DEFAULT_FRET_COUNT).
//   key            { root, quality } - a pitch class and "major" or "minor"
//                  - or omitted for "every note". Narrows to the notes of
//                  that key, the same idea as a capo narrows to a fret
//                  range: fewer choices, chosen on purpose rather than
//                  offered because nobody said not to.
import { DEFAULT_FRET_COUNT, PITCH_CLASSES, positions as neckPositions } from "./neck.js";

// The two scales a key can name. Interval steps from the root, in semitones
// - the plain major (Ionian) and natural minor (Aeolian) scales, which is
// what "a key" means to a guitarist scoping practice rather than a jazz
// player choosing a mode. A third scale is a wider constraint model than
// this issue asks for; these two are enough to make "practice only what's in
// the key of G" a real, testable option.
export const KEY_QUALITIES = {
  major: [0, 2, 4, 5, 7, 9, 11],
  minor: [0, 2, 3, 5, 7, 8, 10],
};

export const KEY_QUALITY_LIST = Object.keys(KEY_QUALITIES);

/** Every pitch class in a key, root and quality - "G major" -> ["G","A","B",
 * "C","D","E","F#"] - or null when either is not one this module knows. */
export function keyNotes(root, quality = "major") {
  const rootIndex = PITCH_CLASSES.indexOf(root);
  const intervals = KEY_QUALITIES[quality];
  if (rootIndex < 0 || !intervals) return null;
  return intervals.map((step) => PITCH_CLASSES[(rootIndex + step) % 12]);
}

/** Whether a fret falls inside a scope's fret range. */
export function fretInScope(fret, scope = {}) {
  const start = Math.max(0, Number(scope.startFret ?? 0));
  const end = Number(scope.endFret ?? DEFAULT_FRET_COUNT);
  return fret >= start && fret <= end;
}

/** Whether a string number is one a scope allows - every string, when none
 * are named (an empty or missing list is "no filter", never "no strings" -
 * see fret-to-note.js's toggleString for why a caller must not let that
 * state be reached by unchecking the last box). */
export function stringInScope(stringNumber, scope = {}) {
  return !Array.isArray(scope.stringNumbers) || !scope.stringNumbers.length
    || scope.stringNumbers.includes(stringNumber);
}

/** Whether a pitch class is inside a scope's key - true for every note when
 * no key is set. */
export function noteInScope(note, scope = {}) {
  if (!scope.key) return true;
  const notes = keyNotes(scope.key.root, scope.key.quality ?? "major");
  return notes ? notes.includes(note) : true;
}

/** Whether one (string, fret, note) position satisfies every dimension of a
 * scope at once - the single test scopePositions and a chord shape's
 * region check both reduce to. */
export function positionInScope(stringNumber, fret, note, scope = {}) {
  return stringInScope(stringNumber, scope)
    && fretInScope(fret, scope)
    && noteInScope(note, scope);
}

/** Whether every position in a GROUP - a chord shape's fretted notes, not a
 * single tap - fits inside a scope. Empty groups are never in scope: a shape
 * with nothing fretted (all strings muted) is not a shape a scope can be
 * said to allow. */
export function groupInScope(positionsGroup, scope = {}) {
  const group = positionsGroup ?? [];
  if (!group.length) return false;
  return group.every((p) => positionInScope(p.string, p.fret, p.note, scope));
}

/** Every position a scope actually allows: `strings` narrowed to
 * `scope.stringNumbers` (all of them when omitted or empty),
 * `scope.startFret`..`scope.endFret`, and `scope.key` when set. The one
 * place both a position-picking drill and the "can this even be asked"
 * check draw positions from. */
export function scopePositions(strings, scope = {}) {
  const start = Math.max(0, Number(scope.startFret ?? 0));
  const end = Number(scope.endFret ?? DEFAULT_FRET_COUNT);
  return neckPositions(strings, start, end).filter((p) =>
    positionInScope(p.string, p.fret, p.note, scope),
  );
}

/** Whether a scope has anything to ask about at all - reachable through the
 * ordinary interface the moment every string is deselected, a fret range is
 * narrowed to nothing, or a key excludes every note the strings can sound. */
export function scopeIsAskable(strings, scope) {
  return scopePositions(strings, scope).length > 0;
}

/** "Strings 1, 2, frets 0-5, key of G major" - the scope, named. Every
 * string is not stated (it is the ordinary case); a narrowed set is. */
export function scopeLabel(strings, scope = {}) {
  const start = Math.max(0, Number(scope.startFret ?? 0));
  const end = Number(scope.endFret ?? DEFAULT_FRET_COUNT);
  const all = (strings ?? []).map((s) => s.number).sort((a, b) => a - b);
  const selected = Array.isArray(scope.stringNumbers) && scope.stringNumbers.length
    ? [...scope.stringNumbers].sort((a, b) => a - b)
    : all;
  const parts = [];
  if (selected.length !== all.length) {
    parts.push(`string${selected.length === 1 ? "" : "s"} ${selected.join(", ")}`);
  }
  parts.push(`frets ${start}-${end}`);
  if (scope.key) {
    parts.push(`key of ${scope.key.root} ${scope.key.quality ?? "major"}`);
  }
  return parts.join(", ");
}
