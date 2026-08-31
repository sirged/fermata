// Guitar chord shapes (issue #28), called directly - every open and barre
// shape's ACTUAL sounded notes checked against chord-theory.js's own
// arithmetic for the shape's declared root and quality, exhaustively enough
// to trust the fingering diagrams the flash card drill shows. No browser
// needed - see this module's own docstring.
import { expect, test } from "@playwright/test";

import { chordTones } from "../../src/lib/trainer/chord-theory.js";
import { defaultStrings } from "../../src/lib/trainer/neck.js";
import {
  allShapes,
  barreShapesFor,
  isStandardGuitarTuning,
  openShapesFor,
  shapeMatchesChord,
  shapeNotes,
} from "../../src/lib/trainer/chord-shapes.js";

const strings = defaultStrings();

// ---------------------------------------------------------------- tuning gate

test("the standard tuning is recognised, including transposed by a capo", () => {
  expect(isStandardGuitarTuning(strings)).toBe(true);
  const capoed = strings.map((s) => ({ ...s, midi: s.midi + 3 }));
  expect(isStandardGuitarTuning(capoed)).toBe(true);
});

test("a non-standard tuning offers no shapes at all - honest, not invented", () => {
  // Drop D: string 6 tuned down a whole step breaks the interval to string
  // 5, so this is no longer the pattern every shape below assumes.
  const dropD = strings.map((s) => (s.number === 6 ? { ...s, midi: s.midi - 2 } : s));
  expect(isStandardGuitarTuning(dropD)).toBe(false);
  expect(openShapesFor(dropD)).toEqual([]);
  expect(barreShapesFor(dropD)).toEqual([]);

  // Missing a string outright is the same "not this pattern" answer.
  const fiveString = strings.filter((s) => s.number !== 6);
  expect(isStandardGuitarTuning(fiveString)).toBe(false);
  expect(openShapesFor(fiveString)).toEqual([]);
});

// ---------------------------------------------------------------- open shapes, exhaustively

test("every open shape's actual sounded notes are exactly its declared chord's tones", () => {
  const shapes = openShapesFor(strings);
  expect(shapes.length).toBeGreaterThan(10);
  for (const shape of shapes) {
    const want = new Set(chordTones(shape.root, shape.quality));
    const got = new Set(shapeNotes(strings, shape));
    expect(got, `${shape.id} sounded notes`).toEqual(want);
  }
});

test("a known open shape's frets read exactly as a guitarist would write them - C major, x32010", () => {
  const c = openShapesFor(strings).find((s) => s.id === "open:C:major");
  const byString = Object.fromEntries(c.frets.map((f) => [f.string, f.fret]));
  expect(byString).toEqual({ 5: 3, 4: 2, 3: 0, 2: 1, 1: 0 });
  expect(byString[6]).toBeUndefined(); // muted, same as chord notation's "x"
});

// ---------------------------------------------------------------- barre shapes, generated

test("barre shapes are generated across a fret range, root read off the neck at each one", () => {
  const shapes = barreShapesFor(strings, { minBaseFret: 1, maxFret: 12 });
  expect(shapes.length).toBeGreaterThan(20);
  // F major is the classic first barre chord: E-shape major at fret 1.
  const f = shapes.find((s) => s.id === "barre-e-major:1");
  expect(f.root).toBe("F");
  // A-shape major at fret 3 is C major, an octave-plus-a-bit up the neck
  // from the open C shape - same chord, a different region.
  const c = shapes.find((s) => s.id === "barre-a-major:3");
  expect(c.root).toBe("C");
});

test("every generated barre shape's actual sounded notes are exactly its own chord's tones", () => {
  const shapes = barreShapesFor(strings, { minBaseFret: 1, maxFret: 15 });
  expect(shapes.length).toBeGreaterThan(0);
  for (const shape of shapes) {
    const want = new Set(chordTones(shape.root, shape.quality));
    const got = new Set(shapeNotes(strings, shape));
    expect(got, `${shape.id} (${shape.root} ${shape.quality}) sounded notes`).toEqual(want);
  }
});

test("across twelve consecutive base frets, an E-shape major barre names every one of the twelve roots", () => {
  // The shape's own highest string needs two frets past the base, so
  // twelve usable base positions (1..12) need the range to reach 14.
  const shapes = barreShapesFor(strings, { minBaseFret: 1, maxFret: 14 })
    .filter((s) => s.id.startsWith("barre-e-major:"));
  const roots = new Set(shapes.map((s) => s.root));
  expect(roots.size).toBe(12);
});

// ---------------------------------------------------------------- shapeMatchesChord

test("shapeMatchesChord is true for a shape against its own declared chord", () => {
  const shape = openShapesFor(strings).find((s) => s.id === "open:E:minor");
  expect(shapeMatchesChord(strings, shape)).toBe(true);
});

test("shapeMatchesChord is false against a different chord, and false for an unknown one", () => {
  const shape = openShapesFor(strings).find((s) => s.id === "open:E:minor");
  expect(shapeMatchesChord(strings, shape, "E", "major")).toBe(false);
  expect(shapeMatchesChord(strings, shape, "H", "major")).toBe(false);
});

test("shapeMatchesChord catches a shape doctored to miss one required tone", () => {
  const shape = openShapesFor(strings).find((s) => s.id === "open:C:major");
  // Drop the string that supplies the fifth (G, string 3 open) - the
  // remaining notes are only C and E, not a complete C major chord.
  const broken = { ...shape, frets: shape.frets.filter((f) => f.string !== 3) };
  expect(shapeMatchesChord(strings, broken)).toBe(false);
});

// ---------------------------------------------------------------- allShapes

test("allShapes combines open and barre shapes from the same tuning", () => {
  const shapes = allShapes(strings, { maxFret: 12 });
  expect(shapes.some((s) => s.family === "open")).toBe(true);
  expect(shapes.some((s) => s.family.startsWith("barre"))).toBe(true);
});
