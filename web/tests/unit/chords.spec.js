// Chord flash cards, called directly (issue #28) - the drill built on
// chord-theory.js, chord-shapes.js and constraints.js. No browser needed -
// see this module's own docstring.
import { expect, test } from "@playwright/test";

import { chordTones } from "../../src/lib/trainer/chord-theory.js";
import {
  DRILL,
  FAMILIES,
  FAMILY_LIST,
  NAME_TO_SHAPE,
  SHAPE_TO_NAME,
  answerStatement,
  attemptPayload,
  checkNameAnswer,
  checkShapeAnswer,
  chordChoices,
  chordPool,
  directionLabel,
  familyLabel,
  loggedStatement,
  pickQuestion,
  poolIsAskable,
  progressStatement,
  scopeLabel,
  sessionNote,
} from "../../src/lib/trainer/chords.js";
import { defaultStrings } from "../../src/lib/trainer/neck.js";
import { forbiddenWord } from "../../src/lib/practice.js";

const strings = defaultStrings();
const fullScope = { startFret: 0, endFret: 12 };

// ---------------------------------------------------------------- family presets

test("the three family presets say what issue #28 asks for, in order", () => {
  expect(FAMILY_LIST).toEqual(["major_minor", "sevenths", "barre"]);
  expect(FAMILIES.major_minor.qualities).toEqual(["major", "minor"]);
  // Sevenths widened by issue #252 to all three seventh qualities - the
  // dominant's open shapes plus the new minor/major sevenths' movable
  // barre forms.
  expect(FAMILIES.sevenths.qualities).toEqual(["dominant7", "minor7", "major7"]);
  expect(FAMILIES.barre.qualities).toEqual(["major", "minor"]);
  expect(FAMILIES.major_minor.shapeFamilies).toEqual(["open"]);
  expect(FAMILIES.barre.shapeFamilies.every((f) => f.startsWith("barre"))).toBe(true);
});

// ---------------------------------------------------------------- pool / scoping

test("the major & minor pool over the full scope contains the open C major and E minor shapes", () => {
  const pool = chordPool(strings, fullScope, "major_minor");
  expect(pool.some((s) => s.root === "C" && s.quality === "major")).toBe(true);
  expect(pool.some((s) => s.root === "E" && s.quality === "minor")).toBe(true);
  // Not a barre or a seventh - the preset's own qualities and families.
  expect(pool.every((s) => ["major", "minor"].includes(s.quality))).toBe(true);
  expect(pool.every((s) => s.family === "open")).toBe(true);
});

test("the sevenths pool contains only the three seventh qualities, and the barre pool only barre shapes", () => {
  const sevenths = chordPool(strings, fullScope, "sevenths");
  expect(sevenths.length).toBeGreaterThan(0);
  expect(sevenths.every((s) => ["dominant7", "minor7", "major7"].includes(s.quality))).toBe(true);

  const barre = chordPool(strings, fullScope, "barre");
  expect(barre.length).toBeGreaterThan(0);
  expect(barre.every((s) => s.family.startsWith("barre"))).toBe(true);
});

test("the sevenths pool also offers minor and major sevenths, drawn from the new barre shapes", () => {
  const sevenths = chordPool(strings, fullScope, "sevenths");
  const minor7 = sevenths.filter((s) => s.quality === "minor7");
  const major7 = sevenths.filter((s) => s.quality === "major7");
  expect(minor7.length).toBeGreaterThan(0);
  expect(major7.length).toBeGreaterThan(0);
  // Neither new quality has an open shape (chord-shapes.js only builds one
  // for major, minor and dominant7) - every instance is one of the two new
  // movable barre families.
  expect(minor7.every((s) => s.family.startsWith("barre"))).toBe(true);
  expect(major7.every((s) => s.family.startsWith("barre"))).toBe(true);
});

test("narrowing the fret range to open position drops a shape that needs a higher fret", () => {
  // The open G major shape frets string 6 at fret 3 and string 1 at fret 3 -
  // outside frets 0-2, so a scope that narrow must not offer it.
  const openPosition = { startFret: 0, endFret: 2 };
  const pool = chordPool(strings, openPosition, "major_minor");
  expect(pool.some((s) => s.id === "open:G:major")).toBe(false);
  // Every shape actually returned really does fit inside that range - the
  // scope is honoured, not merely narrowed by name.
  for (const shape of pool) {
    for (const { fret } of shape.frets) {
      expect(fret, `${shape.id} fret ${fret} within 0-2`).toBeGreaterThanOrEqual(0);
      expect(fret, `${shape.id} fret ${fret} within 0-2`).toBeLessThanOrEqual(2);
    }
  }
});

test("narrowing the string set drops every shape that uses a string outside it", () => {
  const highStringsOnly = { stringNumbers: [1, 2, 3], startFret: 0, endFret: 12 };
  const pool = chordPool(strings, highStringsOnly, "major_minor");
  for (const shape of pool) {
    for (const { string } of shape.frets) {
      expect([1, 2, 3], `${shape.id} uses string ${string}`).toContain(string);
    }
  }
});

test("a fret range narrow enough to admit nothing is honestly unaskable, not a crash", () => {
  const nothing = { startFret: 30, endFret: 31 };
  expect(chordPool(strings, nothing, "major_minor")).toEqual([]);
  expect(poolIsAskable(strings, nothing, "major_minor")).toBe(false);
});

test("a key scope keeps only chords fully diatonic to it, dropping one that is not", () => {
  const cMajorKey = { startFret: 0, endFret: 12, key: { root: "C", quality: "major" } };
  const pool = chordPool(strings, cMajorKey, "major_minor");
  // C major (C, E, G) and D minor (D, F, A) are both entirely C major's own
  // notes.
  expect(pool.some((s) => s.root === "C" && s.quality === "major")).toBe(true);
  expect(pool.some((s) => s.root === "D" && s.quality === "minor")).toBe(true);
  // E major (E, Ab, B) is not - Ab is not a note of C major - so it must be
  // excluded even though it is offered with no key set at all.
  expect(chordPool(strings, fullScope, "major_minor").some((s) => s.root === "E" && s.quality === "major")).toBe(
    true,
  );
  expect(pool.some((s) => s.root === "E" && s.quality === "major")).toBe(false);
});

// ---------------------------------------------------------------- picking a question

test("shape_to_name shows a real fingering; name_to_shape names a chord with no fingering shown", () => {
  const shown = pickQuestion(strings, fullScope, "major_minor", SHAPE_TO_NAME, null, () => 0);
  expect(shown.shape).not.toBeNull();
  expect(shown.shape.frets.length).toBeGreaterThan(0);

  const named = pickQuestion(strings, fullScope, "major_minor", NAME_TO_SHAPE, null, () => 0);
  expect(named.shape).toBeNull();
  expect(named.root).toBeTruthy();
  expect(named.quality).toBeTruthy();
});

test("a question never immediately repeats the same chord", () => {
  const pool = chordPool(strings, fullScope, "major_minor");
  let previous = null;
  for (let i = 0; i < 30; i++) {
    const q = pickQuestion(strings, fullScope, "major_minor", SHAPE_TO_NAME, previous, Math.random);
    if (previous && pool.length > 1) {
      expect(`${q.root}:${q.quality}`).not.toBe(`${previous.root}:${previous.quality}`);
    }
    previous = q;
  }
});

test("an unaskable pool returns no question rather than throwing", () => {
  const nothing = { startFret: 30, endFret: 31 };
  expect(pickQuestion(strings, nothing, "major_minor", SHAPE_TO_NAME)).toBeNull();
});

test("every question carries a playable fingering to hear, even name_to_shape which shows none", () => {
  const named = pickQuestion(strings, fullScope, "major_minor", NAME_TO_SHAPE, null, () => 0);
  expect(named.sound.length).toBeGreaterThan(0);
  // It really does sound the question's own chord.
  const notes = new Set(
    named.sound.map(({ string, fret }) => {
      const midi = strings.find((s) => s.number === string).midi + fret;
      return midi;
    }),
  );
  expect(notes.size).toBeGreaterThan(0);
});

// ---------------------------------------------------------------- choices (shape_to_name's answer buttons)

test("chordChoices lists every distinct chord the pool offers, named and deduplicated", () => {
  const choices = chordChoices(strings, fullScope, "major_minor");
  expect(choices.some((c) => c.root === "C" && c.quality === "major" && c.name === "C major")).toBe(
    true,
  );
  // Deduplicated: only one entry per (root, quality) even though several
  // shapes might share a chord once barre voicings are in play.
  const keys = choices.map((c) => `${c.root}:${c.quality}`);
  expect(new Set(keys).size).toBe(keys.length);
});

test("chordChoices is empty for an unaskable scope, not a crash", () => {
  expect(chordChoices(strings, { startFret: 30, endFret: 31 }, "major_minor")).toEqual([]);
});

// ---------------------------------------------------------------- grading

test("checkNameAnswer is correct for the matching chord and incorrect for a different one", () => {
  const question = { root: "C", quality: "major" };
  expect(checkNameAnswer(question, "C", "major")).toBe(true);
  expect(checkNameAnswer(question, "C", "minor")).toBe(false);
  expect(checkNameAnswer(question, "G", "major")).toBe(false);
});

test("checkShapeAnswer is correct when what was tapped sounds exactly the question's chord", () => {
  const question = { root: "C", quality: "major" };
  // The open C major shape's own frets, tapped one at a time.
  const tapped = [
    { string: 5, fret: 3 },
    { string: 4, fret: 2 },
    { string: 3, fret: 0 },
    { string: 2, fret: 1 },
    { string: 1, fret: 0 },
  ];
  const result = checkShapeAnswer(strings, question, tapped);
  expect(result.correct).toBe(true);
  expect(result.notes).toEqual([...chordTones("C", "major")].sort());
});

test("checkShapeAnswer is incorrect when a required tone is missing", () => {
  const question = { root: "C", quality: "major" };
  const missingTheFifth = [
    { string: 5, fret: 3 }, // C
    { string: 2, fret: 1 }, // C
    { string: 1, fret: 0 }, // E
  ];
  expect(checkShapeAnswer(strings, question, missingTheFifth).correct).toBe(false);
});

test("checkShapeAnswer is incorrect when an extra, non-chord tone is present", () => {
  const question = { root: "C", quality: "major" };
  const withAWrongNote = [
    { string: 5, fret: 3 }, // C
    { string: 4, fret: 2 }, // E
    { string: 3, fret: 0 }, // G
    { string: 6, fret: 1 }, // F - not a C major tone
  ];
  expect(checkShapeAnswer(strings, question, withAWrongNote).correct).toBe(false);
});

test("checkShapeAnswer is incorrect, not thrown, against nothing tapped at all", () => {
  expect(checkShapeAnswer(strings, { root: "C", quality: "major" }, []).correct).toBe(false);
  expect(checkShapeAnswer(strings, { root: "C", quality: "major" }, []).notes).toEqual([]);
});

// ---------------------------------------------------------------- attempt payloads

test("a shape_to_name attempt payload carries the target shape and the chosen chord", () => {
  const question = {
    direction: SHAPE_TO_NAME,
    root: "E",
    quality: "minor",
    shape: { frets: [{ string: 6, fret: 0 }, { string: 5, fret: 2 }] },
  };
  const payload = attemptPayload({ question, given: { root: "E", quality: "minor" }, responseMs: 900 });
  expect(payload.drill).toBe(DRILL);
  expect(payload.direction).toBe(SHAPE_TO_NAME);
  expect(payload.target_root).toBe("E");
  expect(payload.target_quality).toBe("minor");
  expect(payload.target_shape).toEqual([{ string: 6, fret: 0 }, { string: 5, fret: 2 }]);
  expect(payload.given_root).toBe("E");
  expect(payload.given_quality).toBe("minor");
  expect(payload.response_ms).toBe(900);
});

test("a name_to_shape attempt payload carries the tapped positions and resolved notes, no target shape", () => {
  const question = { direction: NAME_TO_SHAPE, root: "C", quality: "major", shape: null };
  const given = { positions: [{ string: 5, fret: 3 }], notes: ["C"] };
  const payload = attemptPayload({ question, given });
  expect(payload.direction).toBe(NAME_TO_SHAPE);
  expect(payload.target_shape).toBeUndefined();
  expect(payload.given_notes).toEqual(["C"]);
  expect(payload.given_shape).toEqual([{ string: 5, fret: 3 }]);
});

// ---------------------------------------------------------------- wording

test("answerStatement names the chord honestly either way, with no verdict word", () => {
  const question = { direction: SHAPE_TO_NAME, root: "C", quality: "major" };
  for (const text of [
    answerStatement(question, { root: "C", quality: "major" }, true),
    answerStatement(question, { root: "G", quality: "major" }, false),
  ]) {
    expect(forbiddenWord(text), text).toBeNull();
  }
  const nameToShape = { direction: NAME_TO_SHAPE, root: "C", quality: "major" };
  for (const text of [
    answerStatement(nameToShape, { notes: ["C", "E", "G"] }, true),
    answerStatement(nameToShape, { notes: ["C", "E"] }, false),
  ]) {
    expect(forbiddenWord(text), text).toBeNull();
  }
});

test("every phrase this module produces avoids the forbidden words and never a percentage", () => {
  const samples = [
    progressStatement({ asked: 5, correct: 2 }),
    sessionNote({ asked: 5, correct: 2, direction: SHAPE_TO_NAME, strings, scope: fullScope, family: "major_minor" }),
    scopeLabel(strings, fullScope, "major_minor"),
    loggedStatement(90),
  ];
  for (const text of samples) {
    expect(forbiddenWord(text), text).toBeNull();
    expect(text).not.toMatch(/%/);
  }
});

test("sessionNote names the direction, the counts, and the family and region", () => {
  const note = sessionNote({
    asked: 4,
    correct: 3,
    direction: NAME_TO_SHAPE,
    strings,
    scope: { startFret: 0, endFret: 5, stringNumbers: [6, 5] },
    family: "barre",
  });
  expect(note).toBe(
    "Chord flash cards, name to shape. 4 chords, 3 answered correctly. Barre chords, strings 5, 6, frets 0-5.",
  );
});

test("directionLabel and familyLabel read as plain words", () => {
  expect(directionLabel(SHAPE_TO_NAME)).toBe("shape to name");
  expect(directionLabel(NAME_TO_SHAPE)).toBe("name to shape");
  expect(familyLabel("sevenths")).toBe("Sevenths");
  expect(familyLabel("not-a-family")).toBe(FAMILIES[FAMILY_LIST[0]].label);
});
