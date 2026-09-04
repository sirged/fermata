// The weak-positions panel's own arithmetic and phrasing (issue #235),
// called directly - no browser needed, same pattern as fret-to-note.spec.js.
import { expect, test } from "@playwright/test";

import { forbiddenWord } from "../../src/lib/practice.js";
import {
  NO_POSITIONS_STATEMENT,
  positionCounts,
  positionStatement,
} from "../../src/lib/trainer/position-counts.js";

function attempt(target_string, target_fret, over = {}) {
  return {
    id: over.id ?? Math.random(),
    drill: "fret_to_note",
    direction: "position_to_note",
    target_string,
    target_fret,
    target_note: "C",
    given_note: "D",
    correct: false,
    ...over,
  };
}

// ------------------------------------------------------------- the grouping

test("no attempts groups to nothing", () => {
  expect(positionCounts([])).toEqual([]);
  expect(positionCounts(null)).toEqual([]);
  expect(positionCounts(undefined)).toEqual([]);
});

test("three incorrect answers on one position count as three, ahead of a position answered incorrectly once", () => {
  const attempts = [
    attempt(2, 5),
    attempt(2, 5),
    attempt(2, 5),
    attempt(4, 0),
  ];
  const found = positionCounts(attempts);
  expect(found).toEqual([
    { string: 2, fret: 5, count: 3 },
    { string: 4, fret: 0, count: 1 },
  ]);
});

test("a note_to_position row has no target position and is not grouped in", () => {
  // docs/practice-data.md: target_string/target_fret are NULL on
  // note_to_position, where a question named a note rather than one
  // position - counting that in would invent a position nobody was asked
  // about.
  const attempts = [
    attempt(2, 5),
    { ...attempt(null, null), direction: "note_to_position", target_string: null, target_fret: null },
  ];
  expect(positionCounts(attempts)).toEqual([{ string: 2, fret: 5, count: 1 }]);
});

test("ties are broken by string then fret, ascending, so the same rows always come back in the same order", () => {
  const attempts = [attempt(3, 2), attempt(1, 9), attempt(1, 2)];
  const found = positionCounts(attempts);
  expect(found.map((r) => [r.string, r.fret])).toEqual([
    [1, 2],
    [1, 9],
    [3, 2],
  ]);
});

test("a limit caps how many positions are returned, largest counts kept", () => {
  const attempts = [
    ...Array(3).fill(attempt(1, 0)),
    ...Array(2).fill(attempt(2, 0)),
    ...Array(1).fill(attempt(3, 0)),
  ];
  const found = positionCounts(attempts, { limit: 2 });
  expect(found.map((r) => r.string)).toEqual([1, 2]);
});

// -------------------------------------------------------------- the wording

test("a position's own line states a count, never a rate", () => {
  expect(positionStatement({ string: 2, fret: 5, count: 3 })).toBe(
    "String 2, fret 5 - answered incorrectly 3 times.",
  );
  expect(positionStatement({ string: 6, fret: 0, count: 1 })).toBe(
    "String 6, fret 0 - answered incorrectly 1 time.",
  );
  expect(positionStatement({ string: 2, fret: 5, count: 3 })).not.toMatch(/%/);
});

test("every string this module produces passes the vocabulary check", () => {
  expect(forbiddenWord(NO_POSITIONS_STATEMENT), NO_POSITIONS_STATEMENT).toBeNull();
  const stated = positionStatement({ string: 2, fret: 5, count: 3 });
  expect(forbiddenWord(stated), stated).toBeNull();
  const one = positionStatement({ string: 6, fret: 0, count: 1 });
  expect(forbiddenWord(one), one).toBeNull();
});
