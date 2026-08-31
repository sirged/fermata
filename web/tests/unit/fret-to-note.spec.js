// The fret-to-note drill's own arithmetic and phrasing (issue #27), called
// directly - no browser needed, same pattern as ear-training.spec.js.
import { expect, test } from "@playwright/test";

import { forbiddenWord } from "../../src/lib/practice.js";
import { defaultStrings } from "../../src/lib/trainer/neck.js";
import {
  NOTE_TO_POSITION,
  POSITION_TO_NOTE,
  answerStatement,
  attemptPayload,
  checkPositionAnswer,
  checkTapAnswer,
  pickQuestion,
  progressStatement,
  scopeIsAskable,
  scopeLabel,
  scopePositions,
  sessionNote,
} from "../../src/lib/trainer/fret-to-note.js";

const strings = defaultStrings();
const fullScope = { startFret: 0, endFret: 12 };

// ---------------------------------------------------------------- scoping

test("the full scope covers every string across the fret range", () => {
  const found = scopePositions(strings, fullScope);
  expect(found).toHaveLength(6 * 13);
  expect(scopeIsAskable(strings, fullScope)).toBe(true);
});

test("narrowing to particular strings narrows the pool to exactly those", () => {
  const scope = { startFret: 0, endFret: 5, stringNumbers: [6, 5] };
  const found = scopePositions(strings, scope);
  expect(new Set(found.map((p) => p.string))).toEqual(new Set([6, 5]));
  expect(found).toHaveLength(2 * 6);
});

test("an empty stringNumbers list means no filter, not 'select nothing'", () => {
  // The UI never lets every string be deselected (FretToNote.svelte keeps at
  // least one checked) - this is what that guarantee rests on: an empty
  // array reads as "nothing excluded" rather than "nothing included".
  expect(scopePositions(strings, { startFret: 0, endFret: 0, stringNumbers: [] })).toHaveLength(6);
});

test("a fret range that excludes everything asks nothing - the degenerate state is real, not a crash", () => {
  expect(scopeIsAskable(strings, { startFret: 30, endFret: 20 })).toBe(false);
  expect(scopePositions(strings, { startFret: 30, endFret: 20 })).toEqual([]);
});

test("scopeLabel states a narrowed scope and stays quiet about the default one", () => {
  expect(scopeLabel(strings, { startFret: 0, endFret: 12 })).toBe("frets 0-12");
  expect(scopeLabel(strings, { startFret: 0, endFret: 5, stringNumbers: [6, 5] })).toBe(
    "strings 5, 6, frets 0-5",
  );
});

// ---------------------------------------------------------------- questions

test("a position-to-note question names a real position and its real note", () => {
  const q = pickQuestion(strings, fullScope, POSITION_TO_NOTE, null, () => 0);
  expect(q.direction).toBe(POSITION_TO_NOTE);
  expect(typeof q.string).toBe("number");
  expect(typeof q.fret).toBe("number");
  expect(typeof q.note).toBe("string");
});

test("a note-to-position question names a note and no position", () => {
  const q = pickQuestion(strings, fullScope, NOTE_TO_POSITION, null, () => 0);
  expect(q.direction).toBe(NOTE_TO_POSITION);
  expect(q.string).toBeNull();
  expect(q.fret).toBeNull();
  expect(typeof q.note).toBe("string");
});

test("a question never immediately repeats the previous one", () => {
  const rand = (() => {
    let calls = 0;
    // Deterministic: always "pick the first" unless that is excluded, in
    // which case the eligible-filtering is what has to do the excluding.
    return () => {
      calls += 1;
      return 0;
    };
  })();
  const scope = { startFret: 0, endFret: 0, stringNumbers: [6] }; // exactly one position
  const first = pickQuestion(strings, scope, POSITION_TO_NOTE, null, rand);
  // Only one position exists, so a second question MUST repeat it - proving
  // the fallback (repeat rather than return null) rather than the ordinary
  // no-repeat path, which the wider-scope test below covers.
  const second = pickQuestion(strings, scope, POSITION_TO_NOTE, first, rand);
  expect(second).toEqual(first);

  const wider = { startFret: 0, endFret: 1, stringNumbers: [6] }; // two positions
  const q1 = pickQuestion(strings, wider, POSITION_TO_NOTE, null, () => 0);
  const q2 = pickQuestion(strings, wider, POSITION_TO_NOTE, q1, () => 0);
  expect(q2).not.toEqual(q1);
});

test("an unaskable scope returns no question rather than throwing", () => {
  expect(pickQuestion(strings, { startFret: 30, endFret: 20 }, POSITION_TO_NOTE)).toBeNull();
});

// ---------------------------------------------------------------- answers

test("position-to-note: the chosen note is checked against the question's note", () => {
  const q = { direction: POSITION_TO_NOTE, string: 6, fret: 3, note: "G" };
  expect(checkPositionAnswer(q, "G")).toBe(true);
  expect(checkPositionAnswer(q, "F#")).toBe(false);
});

test("note-to-position: the note that SOUNDS at the tap decides it, not the tap's intent", () => {
  const q = { direction: NOTE_TO_POSITION, note: "G", string: null, fret: null };
  // String 6 fret 3 sounds G.
  const right = checkTapAnswer(strings, q, 6, 3);
  expect(right).toEqual({ correct: true, note: "G" });
  // String 6 fret 4 sounds Ab (this app's canonical spelling for that
  // pitch class - see neck.js's PITCH_CLASSES), not G.
  const wrong = checkTapAnswer(strings, q, 6, 4);
  expect(wrong).toEqual({ correct: false, note: "Ab" });
});

test("a tap on a string number the neck does not have answers false and names no note", () => {
  const q = { direction: NOTE_TO_POSITION, note: "G", string: null, fret: null };
  expect(checkTapAnswer(strings, q, 99, 0)).toEqual({ correct: false, note: null });
});

// ---------------------------------------------------------------- the structured record (#32)

test("a position-to-note attempt payload carries the target position and the chosen note", () => {
  const q = { direction: POSITION_TO_NOTE, string: 6, fret: 3, note: "G" };
  const payload = attemptPayload({ question: q, given: { note: "F#" }, responseMs: 1200 });
  expect(payload).toEqual({
    session_id: null,
    drill: "fret_to_note",
    direction: POSITION_TO_NOTE,
    response_ms: 1200,
    target_string: 6,
    target_fret: 3,
    target_note: "G",
    given_note: "F#",
  });
});

test("a note-to-position attempt payload carries the tapped position and its resolved note", () => {
  const q = { direction: NOTE_TO_POSITION, note: "G", string: null, fret: null };
  const payload = attemptPayload({
    sessionId: 42,
    question: q,
    given: { string: 6, fret: 4, note: "G#" },
  });
  expect(payload).toEqual({
    session_id: 42,
    drill: "fret_to_note",
    direction: NOTE_TO_POSITION,
    response_ms: null,
    target_note: "G",
    given_string: 6,
    given_fret: 4,
    given_note: "G#",
  });
});

// ---------------------------------------------------------------- phrasing, and the tone rules

test("progress states two counts and nothing when nothing has been asked", () => {
  expect(progressStatement()).toBe("Nothing asked yet.");
  expect(progressStatement({ asked: 1, correct: 0 })).toBe("1 question, none answered correctly.");
  expect(progressStatement({ asked: 3, correct: 2 })).toBe("3 questions, 2 answered correctly.");
});

test("answerStatement states the fact without a verdict word, either way", () => {
  const q = { direction: POSITION_TO_NOTE, string: 6, fret: 3, note: "G" };
  expect(answerStatement(q, { note: "G" }, true)).toBe("String 6, fret 3 is G.");
  expect(answerStatement(q, { note: "F#" }, false)).toBe(
    "String 6, fret 3 is G. You named F#.",
  );
  for (const text of [
    answerStatement(q, { note: "G" }, true),
    answerStatement(q, { note: "F#" }, false),
  ]) {
    expect(forbiddenWord(text), text).toBeNull();
  }
});

test("every phrase this module produces avoids the forbidden words and never a percentage", () => {
  const samples = [
    progressStatement({ asked: 5, correct: 2 }),
    sessionNote({ asked: 5, correct: 2, direction: POSITION_TO_NOTE, strings, scope: fullScope }),
    scopeLabel(strings, fullScope),
  ];
  for (const text of samples) {
    expect(forbiddenWord(text), text).toBeNull();
    expect(text).not.toMatch(/%/);
  }
});

test("sessionNote names the direction, the counts, and the scope", () => {
  const note = sessionNote({
    asked: 4,
    correct: 3,
    direction: NOTE_TO_POSITION,
    strings,
    scope: { startFret: 0, endFret: 5, stringNumbers: [6, 5] },
  });
  expect(note).toBe(
    "Fret to note, note to position. 4 questions, 3 answered correctly. strings 5, 6, frets 0-5.",
  );
});
