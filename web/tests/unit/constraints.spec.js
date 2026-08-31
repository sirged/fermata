// The constraint model (issue #26), called directly - string set, fret
// range, and (its new addition here) a key/note-set filter, plus the
// group-scope test a chord shape's fretted notes are checked against. No
// browser needed for any of it, for the same reason neck.js and
// fret-to-note.js need none - see this module's own docstring.
import { expect, test } from "@playwright/test";

import { defaultStrings } from "../../src/lib/trainer/neck.js";
import {
  KEY_QUALITIES,
  fretInScope,
  groupInScope,
  keyNotes,
  noteInScope,
  positionInScope,
  scopeIsAskable,
  scopeLabel,
  scopePositions,
  stringInScope,
} from "../../src/lib/trainer/constraints.js";

const strings = defaultStrings();

// ---------------------------------------------------------------- strings & frets

test("the full scope covers every string across the fret range", () => {
  const pool = scopePositions(strings, { startFret: 0, endFret: 12 });
  expect(pool.length).toBe(6 * 13);
  expect(new Set(pool.map((p) => p.string))).toEqual(new Set([1, 2, 3, 4, 5, 6]));
});

test("narrowing to particular strings narrows the pool to exactly those", () => {
  const pool = scopePositions(strings, { stringNumbers: [6, 1], startFret: 0, endFret: 5 });
  expect(new Set(pool.map((p) => p.string))).toEqual(new Set([6, 1]));
});

test("an empty stringNumbers list means no filter, not 'select nothing'", () => {
  const full = scopePositions(strings, { startFret: 0, endFret: 3 });
  const empty = scopePositions(strings, { stringNumbers: [], startFret: 0, endFret: 3 });
  expect(empty).toEqual(full);
});

test("a fret range that excludes everything asks nothing - degenerate, not a crash", () => {
  expect(scopeIsAskable(strings, { startFret: 20, endFret: 3 })).toBe(false);
  expect(scopePositions(strings, { startFret: 20, endFret: 3 })).toEqual([]);
});

test("stringInScope and fretInScope agree with scopePositions on individual positions", () => {
  const scope = { stringNumbers: [6, 5], startFret: 2, endFret: 4 };
  expect(stringInScope(6, scope)).toBe(true);
  expect(stringInScope(3, scope)).toBe(false);
  expect(fretInScope(3, scope)).toBe(true);
  expect(fretInScope(5, scope)).toBe(false);
});

// ---------------------------------------------------------------- key/note scoping

test("keyNotes names the diatonic pitch classes of a major and a minor key", () => {
  expect(keyNotes("C", "major")).toEqual(["C", "D", "E", "F", "G", "A", "B"]);
  expect(keyNotes("G", "major")).toEqual(["G", "A", "B", "C", "D", "E", "F#"]);
  expect(keyNotes("A", "minor")).toEqual(["A", "B", "C", "D", "E", "F", "G"]);
  // Every key has seven distinct notes, both qualities, every one of the
  // twelve roots - the exhaustive check the arithmetic above is a sample of.
  for (const root of ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]) {
    for (const quality of Object.keys(KEY_QUALITIES)) {
      const notes = keyNotes(root, quality);
      expect(notes, `${root} ${quality}`).toHaveLength(7);
      expect(new Set(notes).size, `${root} ${quality} has no repeated note`).toBe(7);
      expect(notes[0], `${root} ${quality} starts on its own root`).toBe(root);
    }
  }
});

test("an unknown root or quality is refused with null, not a guess", () => {
  expect(keyNotes("H", "major")).toBeNull();
  expect(keyNotes("C", "dorian")).toBeNull();
});

test("noteInScope allows every note with no key set, and only the key's notes with one", () => {
  expect(noteInScope("F#", {})).toBe(true);
  const scope = { key: { root: "C", quality: "major" } };
  expect(noteInScope("E", scope)).toBe(true);
  expect(noteInScope("Eb", scope)).toBe(false);
});

test("a key scope narrows scopePositions to only the notes of that key", () => {
  const scope = { key: { root: "C", quality: "major" }, startFret: 0, endFret: 12 };
  const pool = scopePositions(strings, scope);
  expect(pool.length).toBeGreaterThan(0);
  const inKey = new Set(keyNotes("C", "major"));
  for (const p of pool) expect(inKey.has(p.note), `${p.note} in C major`).toBe(true);
  // A key that shares no note with what the strings can sound in range is
  // the same degenerate "nothing to ask" state a narrow fret range reaches.
  expect(
    scopeIsAskable(strings, { key: { root: "C", quality: "major" }, startFret: 1, endFret: 0 }),
  ).toBe(false);
});

test("positionInScope is the single test every dimension reduces to at once", () => {
  const scope = { stringNumbers: [6], startFret: 0, endFret: 3, key: { root: "E", quality: "minor" } };
  // String 6 open is E - in the string set, in range, and in E minor.
  expect(positionInScope(6, 0, "E", scope)).toBe(true);
  // Right string and range, wrong key (C# is not in E minor).
  expect(positionInScope(6, 1, "F", scope)).toBe(false);
  // Right note and range, wrong string.
  expect(positionInScope(5, 0, "A", scope)).toBe(false);
});

// ---------------------------------------------------------------- labelling

test("scopeLabel states a narrowed scope and stays quiet about the default one", () => {
  const label = scopeLabel(strings, { stringNumbers: [6, 5], startFret: 0, endFret: 3 });
  expect(label).toBe("strings 5, 6, frets 0-3");
  const full = scopeLabel(strings, { startFret: 0, endFret: 12 });
  expect(full).toBe("frets 0-12");
  expect(full).not.toContain("string");
});

test("scopeLabel names the key when one is set", () => {
  const label = scopeLabel(strings, {
    startFret: 0,
    endFret: 12,
    key: { root: "G", quality: "major" },
  });
  expect(label).toBe("frets 0-12, key of G major");
});

// ---------------------------------------------------------------- group scope (a chord shape's fretted notes)

test("groupInScope requires every member of the group inside the scope, and a group of one", () => {
  const scope = { stringNumbers: [5, 4, 3], startFret: 0, endFret: 3 };
  const cMajorTriadOpenish = [
    { string: 5, fret: 3, note: "C" },
    { string: 4, fret: 2, note: "E" },
    { string: 3, fret: 0, note: "G" },
  ];
  expect(groupInScope(cMajorTriadOpenish, scope)).toBe(true);
  // One member outside the string set breaks the whole group, not only that
  // member - a chord half inside a scope is not a chord the scope allows.
  const withOneOutside = [...cMajorTriadOpenish, { string: 1, fret: 0, note: "E" }];
  expect(groupInScope(withOneOutside, scope)).toBe(false);
});

test("an empty group is never in scope - a shape with nothing fretted is not a shape", () => {
  expect(groupInScope([], { startFret: 0, endFret: 12 })).toBe(false);
});
