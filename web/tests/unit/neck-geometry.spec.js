// Real fret spacing (issue #287) - pins neck-geometry.js's fretX against the
// three falsifiable claims a real fretboard makes that a linear layout
// cannot: spacing shrinks fret over fret, the twelfth fret sits at half the
// (unfretted) scale length, and the last drawn fret still lands exactly at
// the board's total width - so the on-screen board doesn't get wider or
// narrower than the old linear layout drew it.
import { expect, test } from "@playwright/test";

import { fretMarkerX, fretX } from "../../src/lib/trainer/neck-geometry.js";

const WIDTH = 1200;

test("fret spacing strictly decreases toward the body - a real neck, not a diagram", () => {
  const count = 24;
  const gaps = [];
  for (let n = 1; n <= count; n++) {
    gaps.push(fretX(n, count, WIDTH) - fretX(n - 1, count, WIDTH));
  }
  for (let i = 1; i < gaps.length; i++) {
    expect(gaps[i], `gap at fret ${i + 1} vs fret ${i}`).toBeLessThan(gaps[i - 1]);
  }
  // The sharpest, most legible case a reviewer would sanity-check by eye:
  // fret 1 is markedly wider than fret 12.
  expect(gaps[0]).toBeGreaterThan(gaps[11]);
});

test("the twelfth fret sits at half the open-string (unfretted scale) length when 24 frets are drawn", () => {
  const count = 24;
  // The theoretical, never-drawn scale length this board's spacing implies:
  // fret n's unscaled position is 1 - 2^(-n/12), so the scale length itself
  // (n -> infinity) is the pixel value that same formula converges to,
  // i.e. WIDTH's own scale factor: WIDTH / (1 - 2^(-count/12)).
  const scaleLength = WIDTH / (1 - Math.pow(2, -count / 12));
  expect(Math.abs(fretX(12, count, WIDTH) - scaleLength / 2)).toBeLessThan(1);
});

test("the last drawn fret lands exactly at the total width, whatever the fret count", () => {
  for (const count of [1, 12, 22, 24]) {
    expect(Math.abs(fretX(count, count, WIDTH) - WIDTH)).toBeLessThan(1e-9);
  }
  // The nut (fret 0) is unmoved - still the origin.
  expect(fretX(0, 24, WIDTH)).toBe(0);
});

test("a marker centre sits midway between the two fret wires it lies between", () => {
  const count = 24;
  for (const fret of [1, 5, 12, 24]) {
    const expected = (fretX(fret - 1, count, WIDTH) + fretX(fret, count, WIDTH)) / 2;
    expect(fretMarkerX(fret, count, WIDTH)).toBeCloseTo(expected, 9);
  }
});

// ---------------------------------------------------------------- mutation guard
//
// A constant-spacing layout (the bug this issue fixes) is a valid function
// too - it just isn't a fretboard. This proves the two geometry assertions
// above actually distinguish the two: swap in the old linear formula and
// watch them fail, so a future edit that quietly reverts to linear spacing
// cannot pass this file by accident.
function linearFretX(fret, fretCount, width) {
  const count = Math.max(1, fretCount);
  return (width * fret) / count;
}

test("a linear (constant-spacing) layout fails both the decreasing-gap and the twelfth-fret claims", () => {
  const count = 24;
  const gaps = [];
  for (let n = 1; n <= count; n++) {
    gaps.push(linearFretX(n, count, WIDTH) - linearFretX(n - 1, count, WIDTH));
  }
  const strictlyDecreasing = gaps.every((g, i) => i === 0 || g < gaps[i - 1]);
  expect(strictlyDecreasing).toBe(false);

  const scaleLength = WIDTH / (1 - Math.pow(2, -count / 12));
  expect(Math.abs(linearFretX(12, count, WIDTH) - scaleLength / 2)).toBeGreaterThan(1);
});
