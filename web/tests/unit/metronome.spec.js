// The practice metronome's arithmetic, called directly - no renderer, no
// audio, no page. See tests/unit/pitch.spec.js for why: metronome.js has no
// runes and no imports precisely so this can import it.
import { expect, test } from "@playwright/test";

import {
  DEFAULT_METRONOME_BPM,
  FALLBACK_SCORE_TEMPO,
  MAX_METRONOME_BPM,
  MIN_METRONOME_BPM,
  clampBpm,
  effectiveMetronomeBpm,
  metronomePattern,
  secondsPerClick,
  timeSignatureAtTick,
} from "../../src/lib/metronome.js";

// ---------------------------------------------------------------- clamping

test("a bpm inside the range is left alone", () => {
  expect(clampBpm(92)).toBe(92);
  expect(clampBpm(MIN_METRONOME_BPM)).toBe(MIN_METRONOME_BPM);
  expect(clampBpm(MAX_METRONOME_BPM)).toBe(MAX_METRONOME_BPM);
});

test("a bpm outside the range is pulled back to it, not merely reported as invalid", () => {
  expect(clampBpm(1)).toBe(MIN_METRONOME_BPM);
  expect(clampBpm(0)).toBe(MIN_METRONOME_BPM);
  expect(clampBpm(-40)).toBe(MIN_METRONOME_BPM);
  expect(clampBpm(1000)).toBe(MAX_METRONOME_BPM);
});

// ------------------------------------------------------------- fixed bpm mode

test("bpm mode ignores the score entirely", () => {
  // Same bpm, wildly different score tempos - the answer must not move.
  expect(effectiveMetronomeBpm({ mode: "bpm", bpm: 100, scoreTempo: 60 })).toBe(100);
  expect(effectiveMetronomeBpm({ mode: "bpm", bpm: 100, scoreTempo: 200 })).toBe(100);
  expect(effectiveMetronomeBpm({ mode: "bpm", bpm: 100, scoreTempo: null })).toBe(100);
  // and a proportion sitting alongside it, unused, must not leak in either
  expect(effectiveMetronomeBpm({ mode: "bpm", bpm: 100, proportion: 0.5, scoreTempo: 60 })).toBe(100);
});

test("an unusable fixed bpm falls back rather than producing a silent or negative click", () => {
  for (const bad of [0, -10, Number.NaN, undefined, null]) {
    expect(effectiveMetronomeBpm({ mode: "bpm", bpm: bad })).toBe(DEFAULT_METRONOME_BPM);
  }
});

test("a fixed bpm outside the countable range is still clamped", () => {
  expect(effectiveMetronomeBpm({ mode: "bpm", bpm: 5 })).toBe(MIN_METRONOME_BPM);
  expect(effectiveMetronomeBpm({ mode: "bpm", bpm: 900 })).toBe(MAX_METRONOME_BPM);
});

// ------------------------------------------------------------- proportion mode

test("proportion mode tracks the score tempo it is given, not a value resolved once", () => {
  // The same call, called again with a different scoreTempo, is how a piece
  // that changes tempo mid-stream is meant to be tracked - so calling it
  // twice with two different scoreTempos has to answer two different bpms.
  expect(effectiveMetronomeBpm({ mode: "proportion", proportion: 1, scoreTempo: 80 })).toBe(80);
  expect(effectiveMetronomeBpm({ mode: "proportion", proportion: 1, scoreTempo: 160 })).toBe(160);
});

test("a proportion is taken of the score tempo, not added or substituted", () => {
  expect(effectiveMetronomeBpm({ mode: "proportion", proportion: 0.7, scoreTempo: 100 })).toBe(70);
  expect(effectiveMetronomeBpm({ mode: "proportion", proportion: 1.5, scoreTempo: 100 })).toBe(150);
  // over 100% is a legitimate way to push past what is written, not an error
  expect(effectiveMetronomeBpm({ mode: "proportion", proportion: 2, scoreTempo: 60 })).toBe(120);
});

test("a score with no declared tempo degrades to alphaTab's own fallback, not a made-up one", () => {
  expect(FALLBACK_SCORE_TEMPO).toBe(120);
  expect(effectiveMetronomeBpm({ mode: "proportion", proportion: 1, scoreTempo: null })).toBe(120);
  expect(effectiveMetronomeBpm({ mode: "proportion", proportion: 0.5, scoreTempo: undefined })).toBe(60);
  expect(effectiveMetronomeBpm({ mode: "proportion", proportion: 0.5, scoreTempo: 0 })).toBe(60);
});

test("an unusable proportion defaults to 100%, not to zero or NaN", () => {
  for (const bad of [0, -1, Number.NaN, undefined, null]) {
    expect(effectiveMetronomeBpm({ mode: "proportion", proportion: bad, scoreTempo: 90 })).toBe(90);
  }
});

test("proportion mode is clamped at both ends like bpm mode is", () => {
  expect(effectiveMetronomeBpm({ mode: "proportion", proportion: 0.05, scoreTempo: 100 })).toBe(
    MIN_METRONOME_BPM,
  );
  expect(effectiveMetronomeBpm({ mode: "proportion", proportion: 10, scoreTempo: 100 })).toBe(
    MAX_METRONOME_BPM,
  );
});

// ----------------------------------------------------------------- subdivision

test("simple meters click one per beat, only the downbeat accented", () => {
  for (const [num, den] of [
    [2, 4],
    [3, 4],
    [4, 4],
    [5, 4],
  ]) {
    const p = metronomePattern(num, den);
    expect(p, `${num}/${den}`).toEqual({ clicksPerBar: num, accentEvery: num, unit: den });
  }
});

test("compound meters click the eighth-note subdivision, accented in threes", () => {
  for (const [num, den] of [
    [6, 8],
    [9, 8],
    [12, 8],
    [15, 8],
  ]) {
    const p = metronomePattern(num, den);
    expect(p, `${num}/${den}`).toEqual({ clicksPerBar: num, accentEvery: 3, unit: 8 });
  }
});

test("3/8 clicks as a plain three, downbeat accented - compound or not, one bar of three has nothing else to be", () => {
  const p = metronomePattern(3, 8);
  expect(p).toEqual({ clicksPerBar: 3, accentEvery: 3, unit: 8 });
});

test("5/8 and 7/8 are irregular, not compound - one click per eighth, only the downbeat accented", () => {
  expect(metronomePattern(5, 8)).toEqual({ clicksPerBar: 5, accentEvery: 5, unit: 8 });
  expect(metronomePattern(7, 8)).toEqual({ clicksPerBar: 7, accentEvery: 7, unit: 8 });
});

test("an accented click recurs every accentEvery ticks, across more than one bar", () => {
  // What a scheduler actually uses accentEvery for: is click index i accented?
  const p = metronomePattern(6, 8);
  const accented = (i) => i % p.accentEvery === 0;
  const overTwoBars = Array.from({ length: p.clicksPerBar * 2 }, (_, i) => accented(i));
  expect(overTwoBars).toEqual([
    true, false, false, true, false, false, // bar 1: 1, 4
    true, false, false, true, false, false, // bar 2: 1, 4 again
  ]);
});

test("a malformed time signature falls back to 4/4 rather than producing nonsense clicks", () => {
  expect(metronomePattern(0, 4)).toEqual({ clicksPerBar: 4, accentEvery: 4, unit: 4 });
  expect(metronomePattern(4, 0)).toEqual({ clicksPerBar: 4, accentEvery: 4, unit: 4 });
  expect(metronomePattern(Number.NaN, 4)).toEqual({ clicksPerBar: 4, accentEvery: 4, unit: 4 });
  expect(metronomePattern(4.5, 4)).toEqual({ clicksPerBar: 4, accentEvery: 4, unit: 4 });
});

// -------------------------------------------------------------- click timing

test("seconds per click halves when the bpm doubles", () => {
  const a = secondsPerClick(60, 4);
  const b = secondsPerClick(120, 4);
  expect(a).toBeCloseTo(1, 9);
  expect(b).toBeCloseTo(0.5, 9);
});

test("an eighth-note click is half the length of a quarter-note click at the same bpm", () => {
  const quarter = secondsPerClick(120, 4);
  const eighth = secondsPerClick(120, 8);
  expect(eighth).toBeCloseTo(quarter / 2, 9);
});

test("a bpm is always quarter-note bpm, regardless of the notated meter - 2/2's half-note beat is two quarters long", () => {
  // A tempo of "120" in 2/2 does not mean the half-note pulse ticks 120 times
  // a minute; it means the quarter note is 120, so the half-note beat (unit
  // 2) is twice as long as the quarter click would be.
  const half = secondsPerClick(120, 2);
  const quarter = secondsPerClick(120, 4);
  expect(half).toBeCloseTo(quarter * 2, 9);
});

// --------------------------------------------------------- time signature lookup

test("an empty bar list answers 4/4 rather than throwing", () => {
  expect(timeSignatureAtTick([], 5000)).toEqual({ numerator: 4, denominator: 4 });
  expect(timeSignatureAtTick(null, 5000)).toEqual({ numerator: 4, denominator: 4 });
});

test("a tick before the first bar still gets an answer", () => {
  const bars = [{ startTick: 0, numerator: 4, denominator: 4 }];
  expect(timeSignatureAtTick(bars, -10)).toEqual({ numerator: 4, denominator: 4 });
});

test("a tick lands in the bar that started at or before it, not the next one", () => {
  const bars = [
    { startTick: 0, numerator: 4, denominator: 4 },
    { startTick: 3840, numerator: 6, denominator: 8 },
    { startTick: 7680, numerator: 3, denominator: 4 },
  ];
  expect(timeSignatureAtTick(bars, 0)).toEqual({ numerator: 4, denominator: 4 });
  expect(timeSignatureAtTick(bars, 3839)).toEqual({ numerator: 4, denominator: 4 });
  // exactly on the boundary belongs to the bar that starts there
  expect(timeSignatureAtTick(bars, 3840)).toEqual({ numerator: 6, denominator: 8 });
  expect(timeSignatureAtTick(bars, 5000)).toEqual({ numerator: 6, denominator: 8 });
  expect(timeSignatureAtTick(bars, 7680)).toEqual({ numerator: 3, denominator: 4 });
  // past the last bar's start, it is still the last bar's signature
  expect(timeSignatureAtTick(bars, 999999)).toEqual({ numerator: 3, denominator: 4 });
});

test("a single-bar score answers that bar for any tick at or after its start", () => {
  const bars = [{ startTick: 0, numerator: 5, denominator: 8 }];
  expect(timeSignatureAtTick(bars, 0)).toEqual({ numerator: 5, denominator: 8 });
  expect(timeSignatureAtTick(bars, 123456)).toEqual({ numerator: 5, denominator: 8 });
});
