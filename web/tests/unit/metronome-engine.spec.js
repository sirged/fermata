// The general metronome's own rate arithmetic, called directly - no audio, no
// page, no renderer. See tests/unit/metronome.spec.js for the same approach
// applied to the pure functions underneath.
//
// What is testable here without a browser and what is NOT is worth stating
// plainly, because the line matters: createMetronomeEngine touches `window`
// only inside ensureAudioCtx, which nothing below reaches - so its settings
// and the rate they resolve to can be exercised in Node, while every claim
// about a click ACTUALLY SOUNDING is left to the browser suites, which assert
// it at the Web Audio boundary. Nothing here should ever be read as evidence
// that a click happened; currentRate() is the number the scheduler consumes,
// and these tests only check that it is the right number.
import { expect, test } from "@playwright/test";

import { MAX_METRONOME_BPM, MIN_METRONOME_BPM } from "../../src/lib/metronome.js";
import {
  BPM_PRESETS,
  MAX_SUBDIVISION,
  PROPORTION_PRESETS,
  createMetronomeEngine,
  seedBpmForRate,
} from "../../src/lib/metronome-engine.js";

/** An engine with nothing switched on - so it never asks for an AudioContext
 * - and the reported rates it produces along the way. */
function engine() {
  const reported = [];
  const e = createMetronomeEngine({ onTempo: (r) => reported.push(r) });
  return { e, reported };
}

// ----------------------------------------------- what the rate is made of

test("in fixed-BPM mode the typed number IS the rate, in every meter", () => {
  const { e } = engine();
  e.setMode("bpm");
  e.setBpm(120);
  for (const meter of [
    [4, 4],
    [6, 8],
    [3, 2],
    [12, 16],
  ]) {
    e.setMeter(meter[0], meter[1]);
    expect(e.currentRate(), `in ${meter[0]}/${meter[1]}`).toBe(120);
  }
});

test("a proportion is converted onto the meter's own click unit before it is reported", () => {
  const { e } = engine();
  e.setMode("proportion");
  e.setBaseTempo(96);
  e.setProportion(1);
  e.setMeter(4, 4);
  expect(e.currentRate()).toBe(96);
  // An eighth-note meter clicks twice per quarter, so the same proportion of
  // the same tempo is twice the rate - and that, not 96, is what a listener
  // is actually hearing.
  e.setMeter(6, 8);
  expect(e.currentRate()).toBe(192);
  // ...and a half-note meter once every two.
  e.setMeter(2, 2);
  expect(e.currentRate()).toBe(48);
});

test("the whole widened preset range resolves to a real rate, not just the middle of it", () => {
  const { e } = engine();
  e.setMode("proportion");
  // 200, so that even 15% of it (30) clears MIN_METRONOME_BPM - the floor
  // gets its own test below.
  e.setBaseTempo(200);
  e.setMeter(4, 4);
  for (const preset of PROPORTION_PRESETS) {
    e.setProportion(preset / 100);
    expect(e.currentRate(), `${preset}%`).toBe(preset * 2);
  }
  // The bottom of the ladder is far below the half speed the old one stopped
  // at - the point of widening it.
  expect(PROPORTION_PRESETS[0]).toBeLessThan(50);
  expect(PROPORTION_PRESETS.at(-1)).toBeGreaterThan(100);
});

test("the bottom of the ladder over an already-slow piece is held at the countable floor, and the rate reported is the floor rather than the wish", () => {
  // 15% of a piece marked 100 is 15 clicks a minute, which stops being a
  // metronome and starts being a wait - MIN_METRONOME_BPM exists for exactly
  // that. What matters is which of the two numbers is shown: the rate
  // actually scheduled, not the proportion that was asked for. A readout of
  // "15" over a click sounding 20 times a minute is the "displayed and
  // sounded disagree" failure in miniature.
  const { e } = engine();
  e.setMode("proportion");
  e.setBaseTempo(100);
  e.setMeter(4, 4);
  e.setProportion(0.15);
  expect(e.currentRate()).toBe(MIN_METRONOME_BPM);
});

test("every absolute preset is inside the range the click can actually be scheduled at", () => {
  const { e } = engine();
  e.setMode("bpm");
  for (const preset of BPM_PRESETS) {
    e.setBpm(preset);
    // Equal, not merely clamped to something - a preset the clamp has to pull
    // back is a preset that lies about what it does.
    expect(e.currentRate(), `${preset} bpm`).toBe(preset);
  }
});

// ------------------------------------------------------- subdivisions

test("a subdivision multiplies the real click rate in both modes", () => {
  const { e } = engine();
  e.setMode("bpm");
  e.setBpm(60);
  e.setSubdivision(2);
  expect(e.currentRate()).toBe(120);
  e.setSubdivision(3);
  expect(e.currentRate()).toBe(180);

  e.setMode("proportion");
  e.setBaseTempo(80);
  e.setProportion(1);
  e.setMeter(4, 4);
  e.setSubdivision(1);
  expect(e.currentRate()).toBe(80);
  e.setSubdivision(4);
  expect(e.currentRate()).toBe(320);
});

test("the clamp lands on the rate actually scheduled, not on a number before the subdivision is applied", () => {
  // The exact mistake the pre-conversion clamp made for compound meters, one
  // setting further along: 200 bpm in sixteenths is 800 clicks a minute, and
  // clamping the 200 first would let the real rate run to twice what either
  // MAX_METRONOME_BPM or the click envelope can survive.
  const { e } = engine();
  e.setMode("bpm");
  e.setBpm(200);
  e.setSubdivision(4);
  expect(e.currentRate()).toBe(MAX_METRONOME_BPM);

  // Same in proportion mode, where the meter's own unit and the subdivision
  // compound: 175% of 200 in 16ths, split four ways, is far past the ceiling.
  e.setMode("proportion");
  e.setBaseTempo(200);
  e.setProportion(1.75);
  e.setMeter(12, 16);
  e.setSubdivision(4);
  expect(e.currentRate()).toBe(MAX_METRONOME_BPM);
});

test("a subdivision outside the offered range is refused rather than applied", () => {
  const { e } = engine();
  e.setMode("bpm");
  e.setBpm(100);
  for (const bad of [0, -1, MAX_SUBDIVISION + 1, Number.NaN, "many", null]) {
    e.setSubdivision(bad);
    expect(e.currentRate(), `after setSubdivision(${String(bad)})`).toBe(100);
  }
});

// ------------------------------------------------------- the pre-fill

test("a pre-filled tempo is a starting point: the last value set is the one that counts, in either direction", () => {
  const { e } = engine();
  e.setMode("bpm");
  e.setBpm(92);
  expect(e.currentRate()).toBe(92);
  e.setBpm(104);
  expect(e.currentRate()).toBe(104);
  e.setBpm(40);
  expect(e.currentRate()).toBe(40);
});

test("an unusable tempo leaves the last usable one in place rather than resolving to nothing", () => {
  const { e } = engine();
  e.setMode("bpm");
  e.setBpm(88);
  for (const bad of [0, -20, Number.NaN, undefined, null, "fast", Infinity]) {
    e.setBpm(bad);
    expect(e.currentRate(), `after setBpm(${String(bad)})`).toBe(88);
  }
  // The clamp still applies to a usable-but-out-of-range number, which is a
  // different case from an unusable one: it is pulled into range, not ignored.
  e.setBpm(1);
  expect(e.currentRate()).toBe(MIN_METRONOME_BPM);
});

test("a live pulse source's meter overrides the pre-filled one, which is what lets a piece change time signature mid-stream", () => {
  const { e } = engine();
  e.setMode("proportion");
  e.setBaseTempo(96);
  e.setProportion(1);
  e.setMeter(4, 4);
  expect(e.currentRate()).toBe(96);

  // The seam: a caller with a playhead answers in plain numbers, and the
  // engine never reaches for a renderer to get them.
  let bar = { startTick: 0, endTick: 2880, numerator: 6, denominator: 8 };
  e.setPulseSource(() => ({ tick: 0, bar }));
  expect(e.currentRate()).toBe(192);

  // Moving under it - a meter change written mid-piece - moves the answer,
  // rather than being resolved once and remembered.
  bar = { startTick: 2880, endTick: 5760, numerator: 3, denominator: 2 };
  expect(e.currentRate()).toBe(48);

  // A source that has nothing to say yet falls back to the pre-fill rather
  // than to nothing.
  e.setPulseSource(() => null);
  expect(e.currentRate()).toBe(96);
});

// -------------------------------------------------- what gets reported

test("nothing is reported before there is a context to report from, and everything after", () => {
  const reported = [];
  const e = createMetronomeEngine({ onTempo: (r) => reported.push(r), ready: false });
  e.setMode("bpm");
  e.setBpm(120);
  // A caller whose pre-fill arrives asynchronously must not be shown a number
  // that has nothing behind it yet.
  expect(reported).toEqual([]);
  expect(e.currentRate()).toBeNull();

  e.setReady(true);
  expect(reported).toEqual([120]);
  expect(e.currentRate()).toBe(120);
});

test("the reported rate is de-duplicated, so a setting change that does not move the click does not announce one", () => {
  const { e, reported } = engine();
  e.setMode("bpm");
  e.setBpm(120);
  const afterFirst = reported.length;
  e.setBpm(120);
  e.setBpm(120);
  expect(reported.length).toBe(afterFirst);
  e.setBpm(121);
  expect(reported.at(-1)).toBe(121);
});

test("what is reported is what would be scheduled - one rounding, not two", () => {
  // 96 * 0.15 * 2 = 28.8. A readout that rounded separately from the
  // scheduler is exactly the "shows one number, sounds another" failure the
  // single rounding exists to rule out, so currentRate and the reported value
  // have to be the identical integer.
  const { e, reported } = engine();
  e.setMode("proportion");
  e.setBaseTempo(96);
  e.setMeter(6, 8);
  e.setProportion(0.15);
  expect(e.currentRate()).toBe(29);
  expect(reported.at(-1)).toBe(29);
});

// ------------------------------------------- when the range runs out

test("the engine says which end of its range the clamp is holding the click at, so an interface can explain a readout that stopped matching its control", () => {
  const { e } = engine();
  e.setMode("proportion");
  e.setBaseTempo(120);
  e.setMeter(4, 4);

  // 15% of 120 is 18 a minute, below the countable floor.
  e.setProportion(0.15);
  expect(e.currentRate()).toBe(MIN_METRONOME_BPM);
  expect(e.currentLimit()).toBe("slowest");

  // Far enough up that the setting decides the rate again, and nothing is
  // claimed - a notice that is always on stops meaning anything.
  e.setProportion(0.5);
  expect(e.currentRate()).toBe(60);
  expect(e.currentLimit()).toBeNull();

  // And the other end.
  e.setMode("bpm");
  e.setBpm(200);
  e.setSubdivision(4);
  expect(e.currentRate()).toBe(MAX_METRONOME_BPM);
  expect(e.currentLimit()).toBe("fastest");
});

test("the limit is reported alongside the rate, including when it changes while the rate does not", () => {
  const reported = [];
  const e = createMetronomeEngine({ onTempo: (rate, limit) => reported.push([rate, limit]) });
  e.setMode("proportion");
  e.setMeter(4, 4);
  // A base of 100: 20% is 20 a minute exactly - the floor's own value, reached
  // legitimately rather than by being clamped to it.
  e.setBaseTempo(100);
  e.setProportion(0.2);
  expect(reported.at(-1)).toEqual([20, null]);

  // 10% of the same base is 10 a minute, clamped up to the same 20. The RATE
  // is unchanged, so a de-duplication keyed on the rate alone would swallow
  // this - and the interface would go on showing "10%" beside "20" with no
  // explanation, which is the exact failure the limit exists to prevent.
  e.setProportion(0.1);
  expect(reported.at(-1)).toEqual([20, "slowest"]);
});

// --------------------------------- seeding a fixed tempo from a live rate

test("seeding a fixed tempo from the live rate divides by the subdivision, so the box and the readout cannot disagree", () => {
  // The failure this exists to rule out, in full: proportion mode, 100% of a
  // piece marked 120 in 4/4, eighth-note subdivision. The engine reports 240.
  // Seeding the fixed-BPM box with 240 makes the engine compute 240 * 2 = 480,
  // which the clamp holds at 400 - so the box reads 240 while 400 sounds and
  // the readout beside it says 400. Two numbers on the same strip disagreeing.
  const e = createMetronomeEngine({ onTempo: () => {} });
  e.setMode("proportion");
  e.setBaseTempo(120);
  e.setMeter(4, 4);
  e.setSubdivision(2);
  expect(e.currentRate()).toBe(240);

  // Seeded correctly, switching mode leaves the tempo exactly where it was.
  const seeded = seedBpmForRate(e.currentRate(), 2);
  expect(seeded).toBe(120);
  e.setMode("bpm");
  e.setBpm(seeded);
  expect(e.currentRate()).toBe(240);
  expect(e.currentLimit()).toBeNull();

  // ...where seeding with the rate itself would have moved it, and tripped the
  // ceiling on the way. Asserted so this test states what the wrong answer
  // actually does rather than only what the right one does.
  e.setBpm(240);
  expect(e.currentRate()).toBe(MAX_METRONOME_BPM);
  expect(e.currentLimit()).toBe("fastest");
});

test("seeding with no subdivision in force is the identity, and a nonsense subdivision does not corrupt it", () => {
  expect(seedBpmForRate(96)).toBe(96);
  expect(seedBpmForRate(96, 1)).toBe(96);
  for (const bad of [0, -2, Number.NaN, null, undefined, "two"]) {
    expect(seedBpmForRate(96, bad), `subdivision ${String(bad)}`).toBe(96);
  }
  // A rate that is not usable at all falls back rather than propagating NaN
  // into the scheduler - the same rule clampBpm follows.
  for (const bad of [0, -5, Number.NaN, null, "fast"]) {
    expect(Number.isFinite(seedBpmForRate(bad, 2)), `rate ${String(bad)}`).toBe(true);
  }
  // Still clamped, so what lands in the box is a value the box can hold.
  expect(seedBpmForRate(4000, 1)).toBe(MAX_METRONOME_BPM);
  expect(seedBpmForRate(30, 4)).toBe(MIN_METRONOME_BPM);
});

test("the ceiling is reachable from ordinary meters at ordinary presets, not only from extreme settings", () => {
  // A sweep of the preset ladder against real meters found the ceiling roughly
  // eighteen times more reachable than the floor. These are the cases from it:
  // running a jig or a 12/8 blues above tempo, which is normal practice. Kept
  // as a table because the claim being made is about how ORDINARY these are,
  // and a single example would not carry it.
  const e = createMetronomeEngine({ onTempo: () => {} });
  e.setMode("proportion");
  const cases = [
    { tempo: 144, meter: [6, 8], preset: 150, asked: 432 },
    { tempo: 120, meter: [6, 8], preset: 175, asked: 420 },
    { tempo: 120, meter: [9, 8], preset: 175, asked: 420 },
    { tempo: 120, meter: [12, 8], preset: 175, asked: 420 },
  ];
  for (const { tempo, meter, preset, asked } of cases) {
    e.setBaseTempo(tempo);
    e.setMeter(meter[0], meter[1]);
    e.setProportion(preset / 100);
    const label = `${preset}% of ${tempo} in ${meter[0]}/${meter[1]}`;
    // The arithmetic really does ask for more than can be sounded...
    expect(tempo * (preset / 100) * (meter[1] / 4), label).toBeCloseTo(asked, 5);
    // ...so the rate is the ceiling, and it says so.
    expect(e.currentRate(), label).toBe(MAX_METRONOME_BPM);
    expect(e.currentLimit(), label).toBe("fastest");
  }

  // And the same meters at 100% are comfortably inside the range, so the
  // notice above is a fact about those presets rather than about those meters.
  e.setProportion(1);
  for (const { tempo, meter } of cases) {
    e.setBaseTempo(tempo);
    e.setMeter(meter[0], meter[1]);
    expect(e.currentLimit(), `100% of ${tempo} in ${meter[0]}/${meter[1]}`).toBeNull();
  }
});
