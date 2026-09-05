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
  presetFromScope,
  scopeFromPreset,
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

// ---------------------------------------------------------------- named scopes (#236)
//
// The two shapes a scope now has - the live object the drills narrow with,
// and the row GET/POST /api/trainer/presets stores - and the whole of the
// translation between them. What these pin is that the round trip is lossless
// in every dimension a preset carries, and that the ONE place the two shapes
// genuinely disagree (an unfiltered string set) is resolved on purpose rather
// than by accident.

test("a scope with every dimension set survives the round trip through a preset row", () => {
  const scope = {
    startFret: 5,
    endFret: 9,
    stringNumbers: [3, 1, 2],
    key: { root: "G", quality: "minor" },
  };
  const row = presetFromScope("Middle of the neck", strings, scope);
  expect(row).toEqual({
    name: "Middle of the neck",
    start_fret: 5,
    end_fret: 9,
    // Sorted on the way out: a set has no order, and a stable one is what
    // lets two presets be compared without sorting first.
    strings: [1, 2, 3],
    key_root: "G",
    key_quality: "minor",
  });
  expect(scopeFromPreset({ ...row, id: 1 })).toEqual({
    startFret: 5,
    endFret: 9,
    stringNumbers: [1, 2, 3],
    key: { root: "G", quality: "minor" },
  });
});

test("an unfiltered string set is stored by naming every string, never as an empty set", () => {
  // THE ONE REAL DIFFERENCE between the two shapes. In a live scope an empty
  // or absent stringNumbers means "no filter at all" (stringInScope above); a
  // stored row cannot use that convention, because a preset with no strings
  // would be indistinguishable from one whose strings did not write. So the
  // instrument's own strings are spelled out.
  const named = presetFromScope("Everything", strings, { startFret: 0, endFret: 12 });
  expect(named.strings).toEqual([1, 2, 3, 4, 5, 6]);
  expect(
    presetFromScope("Everything", strings, { startFret: 0, endFret: 12, stringNumbers: [] })
      .strings,
  ).toEqual([1, 2, 3, 4, 5, 6]);
  // And what comes back still narrows nothing, which is the property that
  // actually matters: it offers every position the unscoped neck does.
  const restored = scopeFromPreset(named);
  expect(scopePositions(strings, restored).length).toBe(
    scopePositions(strings, { startFret: 0, endFret: 12 }).length,
  );
});

test("a scope with no key stores two nulls, and comes back with no key at all", () => {
  const row = presetFromScope("Open position", strings, { startFret: 0, endFret: 3 });
  expect(row.key_root).toBeNull();
  expect(row.key_quality).toBeNull();
  const restored = scopeFromPreset(row);
  // Not `key: null` - noteInScope would read that the same way, but a saved
  // every-note scope would then be a different SHAPE of object from a fresh
  // one, and something comparing the two would say they differ.
  expect("key" in restored).toBe(false);
  expect(noteInScope("C#", restored)).toBe(true);
});

test("a key whose quality was left implicit is stored as major rather than as nothing", () => {
  // A live scope may carry a key with no quality - constraints.js defaults it
  // to major everywhere it reads one. A ROW may not: key_root without
  // key_quality is refused by the server, so the default is applied here,
  // once, rather than left to be rejected on the way out.
  const row = presetFromScope("Key of D", strings, {
    startFret: 0,
    endFret: 12,
    key: { root: "D" },
  });
  expect(row.key_quality).toBe("major");
  expect(scopeFromPreset(row).key).toEqual({ root: "D", quality: "major" });
});

test("a restored preset narrows the position pool exactly as the scope it was saved from did", () => {
  // The claim the whole feature rests on, stated as an equality: what a
  // person practises after picking a saved scope is what they were
  // practising when they saved it.
  const scope = {
    startFret: 5,
    endFret: 7,
    stringNumbers: [6, 5],
    key: { root: "A", quality: "minor" },
  };
  const before = scopePositions(strings, scope);
  const after = scopePositions(
    strings,
    scopeFromPreset(presetFromScope("A minor box", strings, scope)),
  );
  expect(after.length).toBe(before.length);
  expect(before.length).toBeGreaterThan(0);
  expect(after.map((p) => `${p.string}:${p.fret}`).sort()).toEqual(
    before.map((p) => `${p.string}:${p.fret}`).sort(),
  );
});

test("nothing at all is not a preset - scopeFromPreset says so rather than inventing a scope", () => {
  expect(scopeFromPreset(null)).toBeNull();
  expect(scopeFromPreset(undefined)).toBeNull();
});
