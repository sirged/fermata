// The practice metronome's arithmetic, called directly - no renderer, no
// audio, no page. See tests/unit/pitch.spec.js for why: metronome.js has no
// runes and no imports precisely so this can import it.
import { expect, test } from "@playwright/test";

import {
  DEFAULT_METRONOME_BPM,
  FALLBACK_SCORE_TEMPO,
  MAX_METRONOME_BPM,
  MIN_METRONOME_BPM,
  barAtTick,
  clampBpm,
  clickLevel,
  clickPhaseInBar,
  effectiveClickRate,
  metronomePattern,
  secondsPerClick,
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

test("clampBpm guards its own input - a caller that forgets to pre-filter cannot produce NaN or a string", () => {
  // Not exploitable through effectiveClickRate today (it pre-filters before
  // ever calling this), but clampBpm is the last line of defence against an
  // unterminating scheduler loop - secondsPerClick(NaN) is NaN, and a while
  // loop comparing against NaN never becomes false - so it has to hold on
  // its own, not merely because every caller happens to behave today.
  for (const bad of [Number.NaN, undefined, null, "abc", {}, [], Infinity, -Infinity]) {
    const result = clampBpm(bad);
    expect(typeof result, JSON.stringify(bad)).toBe("number");
    expect(Number.isFinite(result), JSON.stringify(bad)).toBe(true);
  }
  // a numeric string still works, and still clamps
  expect(clampBpm("200")).toBe(200);
  expect(clampBpm("9999")).toBe(MAX_METRONOME_BPM);
});

// ------------------------------------------------------------- fixed bpm mode

test("bpm mode ignores the score AND the meter entirely", () => {
  // Same bpm, wildly different score tempos - the answer must not move.
  expect(effectiveClickRate({ mode: "bpm", bpm: 100, scoreTempo: 60, unit: 4 })).toBe(100);
  expect(effectiveClickRate({ mode: "bpm", bpm: 100, scoreTempo: 200, unit: 4 })).toBe(100);
  expect(effectiveClickRate({ mode: "bpm", bpm: 100, scoreTempo: null, unit: 4 })).toBe(100);
  // a proportion sitting alongside it, unused, must not leak in either
  expect(effectiveClickRate({ mode: "bpm", bpm: 100, proportion: 0.5, scoreTempo: 60, unit: 4 })).toBe(100);
  // nor may the meter's own unit change the answer - this is the whole
  // point of fixed-BPM mode: the typed number IS the rate, in every meter
  for (const unit of [2, 4, 8, 16]) {
    expect(effectiveClickRate({ mode: "bpm", bpm: 100, unit })).toBe(100);
  }
});

test("an unusable fixed bpm falls back rather than producing a silent or negative click", () => {
  for (const bad of [0, -10, Number.NaN, undefined, null]) {
    expect(effectiveClickRate({ mode: "bpm", bpm: bad, unit: 4 })).toBe(DEFAULT_METRONOME_BPM);
  }
});

test("a fixed bpm outside the countable range is still clamped", () => {
  expect(effectiveClickRate({ mode: "bpm", bpm: 5, unit: 4 })).toBe(MIN_METRONOME_BPM);
  expect(effectiveClickRate({ mode: "bpm", bpm: 900, unit: 4 })).toBe(MAX_METRONOME_BPM);
});

// ------------------------------------------------------------- proportion mode

test("proportion mode tracks the score tempo it is given, not a value resolved once", () => {
  // The same call, called again with a different scoreTempo, is how a piece
  // that changes tempo mid-stream is meant to be tracked - so calling it
  // twice with two different scoreTempos has to answer two different rates.
  expect(effectiveClickRate({ mode: "proportion", proportion: 1, scoreTempo: 80, unit: 4 })).toBe(80);
  expect(effectiveClickRate({ mode: "proportion", proportion: 1, scoreTempo: 160, unit: 4 })).toBe(160);
});

test("a proportion is taken of the score tempo, not added or substituted", () => {
  expect(effectiveClickRate({ mode: "proportion", proportion: 0.7, scoreTempo: 100, unit: 4 })).toBe(70);
  expect(effectiveClickRate({ mode: "proportion", proportion: 1.5, scoreTempo: 100, unit: 4 })).toBe(150);
  // over 100% is a legitimate way to push past what is written, not an error
  expect(effectiveClickRate({ mode: "proportion", proportion: 2, scoreTempo: 60, unit: 4 })).toBe(120);
});

test("proportion mode converts onto the meter's OWN click unit - the actual click rate, not a quarter-note figure a listener has to convert", () => {
  // 96 quarter notes a minute, clicked on the eighth (6/8): twice as many
  // clicks as quarters, because each quarter is two eighths.
  expect(effectiveClickRate({ mode: "proportion", proportion: 1, scoreTempo: 96, unit: 8 })).toBe(192);
  // clicked on the half note (2/2): half as many, because each half is two
  // quarters, so waiting for the next half takes twice as long.
  expect(effectiveClickRate({ mode: "proportion", proportion: 1, scoreTempo: 96, unit: 2 })).toBe(48);
  // unit 4 (the quarter itself) is the identity conversion
  expect(effectiveClickRate({ mode: "proportion", proportion: 1, scoreTempo: 96, unit: 4 })).toBe(96);
});

test("the unit conversion happens before clamping, not after - an extreme meter cannot sneak the actual rate past MAX_METRONOME_BPM", () => {
  // 300 quarter notes a minute in 4/128 would need 9600 clicks a minute if
  // converted unclamped - clamping the 300 first (as a quarter-note figure)
  // and converting second would let that 9600 straight through unchecked.
  const rate = effectiveClickRate({ mode: "proportion", proportion: 1, scoreTempo: 300, unit: 128 });
  expect(rate).toBe(MAX_METRONOME_BPM);
  // and the ordinary case is unaffected - conversion first, clamp second,
  // lands on the same answer as before when nothing extreme is happening
  expect(effectiveClickRate({ mode: "proportion", proportion: 1, scoreTempo: 96, unit: 8 })).toBe(192);
});

test("a score with no declared tempo degrades to alphaTab's own fallback, not a made-up one", () => {
  expect(FALLBACK_SCORE_TEMPO).toBe(120);
  expect(effectiveClickRate({ mode: "proportion", proportion: 1, scoreTempo: null, unit: 4 })).toBe(120);
  expect(effectiveClickRate({ mode: "proportion", proportion: 0.5, scoreTempo: undefined, unit: 4 })).toBe(60);
  expect(effectiveClickRate({ mode: "proportion", proportion: 0.5, scoreTempo: 0, unit: 4 })).toBe(60);
});

test("an unusable proportion defaults to 100%, not to zero or NaN", () => {
  for (const bad of [0, -1, Number.NaN, undefined, null]) {
    expect(effectiveClickRate({ mode: "proportion", proportion: bad, scoreTempo: 90, unit: 4 })).toBe(90);
  }
});

test("an unusable unit defaults to 4 (the quarter note, the identity conversion)", () => {
  for (const bad of [0, -1, Number.NaN, undefined, null, 4.5]) {
    expect(effectiveClickRate({ mode: "proportion", proportion: 1, scoreTempo: 90, unit: bad })).toBe(90);
  }
});

test("proportion mode is clamped at both ends like bpm mode is", () => {
  expect(effectiveClickRate({ mode: "proportion", proportion: 0.05, scoreTempo: 100, unit: 4 })).toBe(
    MIN_METRONOME_BPM,
  );
  expect(effectiveClickRate({ mode: "proportion", proportion: 10, scoreTempo: 100, unit: 4 })).toBe(
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

test("compound meters click the eighth-note subdivision, grouped in threes - accentEvery marks where each GROUP starts, not on its own how loud a click sounds", () => {
  // accentEvery === 3 says where the dotted-quarter pulses fall (1, 4, 7,
  // ...); it does not say the bar has only two sounds in it. Issue #121: a
  // metronome built on accentEvery alone as a bare on/off accent leaves 6/8,
  // 9/8 and 12/8 clicking byte-identical streams, because every one of those
  // pulses got the same sound regardless of which pulse STARTS the bar. See
  // clickLevel below for the function that actually decides the sound -
  // downbeat only at phase 0, this grouping everywhere else it recurs.
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

test("the same compound grouping applies written in sixteenths - 9/16 and 12/16 are compound too", () => {
  for (const [num, den] of [
    [6, 16],
    [9, 16],
    [12, 16],
    [15, 16],
  ]) {
    const p = metronomePattern(num, den);
    expect(p, `${num}/${den}`).toEqual({ clicksPerBar: num, accentEvery: 3, unit: 16 });
  }
});

test("5/16 and 7/16 are irregular in sixteenths too, not compound", () => {
  expect(metronomePattern(5, 16)).toEqual({ clicksPerBar: 5, accentEvery: 5, unit: 16 });
  expect(metronomePattern(7, 16)).toEqual({ clicksPerBar: 7, accentEvery: 7, unit: 16 });
});

test("x/4 meters are left simple even when the numerator is divisible by three - 6/4 is not treated as compound", () => {
  // Unlike 6/8, which is unambiguously two dotted-quarter pulses of three
  // eighths each, 6/4 is genuinely ambiguous (two dotted-half pulses, or six
  // plain quarters) - so this clicks as six plain quarters, only the
  // downbeat accented, rather than guessing which reading a piece meant.
  expect(metronomePattern(6, 4)).toEqual({ clicksPerBar: 6, accentEvery: 6, unit: 4 });
  expect(metronomePattern(9, 4)).toEqual({ clicksPerBar: 9, accentEvery: 9, unit: 4 });
});

test("only denominators 8 and 16 are ever treated as compound, across every numerator from 1 to 24", () => {
  // A thorough sweep, not just the handful of meters named above - proof
  // that d===8 or d===16 (with a numerator divisible by three, other than 3
  // itself where it makes no observable difference) is the WHOLE rule, no
  // denominator this misses.
  for (const numerator of Array.from({ length: 24 }, (_, i) => i + 1)) {
    for (const denominator of [1, 2, 4, 8, 16, 32]) {
      const p = metronomePattern(numerator, denominator);
      // n === 3 asks the same question the compound branch answers and gets
      // the same accentEvery either way (see metronomePattern's own comment
      // on 3/8), so it is not excluded here - both readings agree on it.
      const shouldBeCompound = (denominator === 8 || denominator === 16) && numerator % 3 === 0;
      if (shouldBeCompound) {
        expect(p.accentEvery, `${numerator}/${denominator}`).toBe(3);
      } else {
        expect(p.accentEvery, `${numerator}/${denominator}`).toBe(numerator);
      }
    }
  }
});

test("a group start recurs every accentEvery ticks, across more than one bar - but only the FIRST one each bar is the downbeat", () => {
  // What a scheduler actually uses accentEvery for: is click index i the
  // start of a group? That is a weaker claim than "is it accented" - group
  // starts 3 and 9 below are not the bar's own downbeat, and clickLevel (see
  // metronome.spec.js's own tests) is what tells the two apart by sound.
  const p = metronomePattern(6, 8);
  const groupStart = (i) => i % p.accentEvery === 0;
  const overTwoBars = Array.from({ length: p.clicksPerBar * 2 }, (_, i) => groupStart(i));
  expect(overTwoBars).toEqual([
    true, false, false, true, false, false, // bar 1: group starts at 1, 4 - only 1 is the downbeat
    true, false, false, true, false, false, // bar 2: 1, 4 again - only 1 is the downbeat
  ]);
  // The distinction stated as sound, using clickLevel directly: index 0 (bar
  // 1's downbeat) and index 6 (bar 2's downbeat) are "downbeat"; index 3 and
  // 9 - group starts that are NOT the downbeat - are only "beat".
  const levels = Array.from({ length: p.clicksPerBar * 2 }, (_, i) =>
    clickLevel(i, p.clicksPerBar, p.accentEvery, 1),
  );
  expect(levels[0]).toBe("downbeat");
  expect(levels[3]).toBe("beat");
  expect(levels[6]).toBe("downbeat");
  expect(levels[9]).toBe("beat");
});

test("a malformed time signature falls back to 4/4 rather than producing nonsense clicks", () => {
  expect(metronomePattern(0, 4)).toEqual({ clicksPerBar: 4, accentEvery: 4, unit: 4 });
  expect(metronomePattern(4, 0)).toEqual({ clicksPerBar: 4, accentEvery: 4, unit: 4 });
  expect(metronomePattern(Number.NaN, 4)).toEqual({ clicksPerBar: 4, accentEvery: 4, unit: 4 });
  expect(metronomePattern(4.5, 4)).toEqual({ clicksPerBar: 4, accentEvery: 4, unit: 4 });
});

// -------------------------------------------------------------- click level
//
// Issue #121: with only two sounds (accent and tick), a bare metronome in
// compound meter had nothing to mark where the BAR starts, as opposed to
// where each dotted-quarter group inside it starts - 6/8, 9/8 and 12/8
// clicked byte-identical streams. clickLevel is the third sound's decision,
// factored out of metronome-engine.js's resolve() into one named, tested
// place: "downbeat" | "beat" | "tick", from phase and the pattern alone, no
// audio and no browser required.

test("6/8 gets all three sounds - downbeat at the bar, beat at the other dotted-quarter pulse, tick at the two plain eighths between them", () => {
  // metronomePattern(6, 8) is { clicksPerBar: 6, accentEvery: 3, unit: 8 }.
  const levels = Array.from({ length: 12 }, (_, i) => clickLevel(i, 6, 3, 1));
  expect(levels).toEqual([
    "downbeat", "tick", "tick", "beat", "tick", "tick",
    "downbeat", "tick", "tick", "beat", "tick", "tick",
  ]);
});

test("a simple meter never reaches the beat tier - accentEvery equals clicksPerBar, so nothing but phase 0 can ever satisfy either test", () => {
  // metronomePattern(4, 4) is { clicksPerBar: 4, accentEvery: 4, unit: 4 }.
  // This is the whole reason 4/4 stays two sounds: clickLevel(i, 4, 4, ...)
  // for i = 1, 2, 3 fails BOTH the downbeat test (i !== 0) and the beat test
  // (i is not a multiple of accentEvery, which here is the same number as
  // clicksPerBar) - there is no phase left over for a third sound to occupy.
  const levels = Array.from({ length: 8 }, (_, i) => clickLevel(i, 4, 4, 1));
  expect(levels).toEqual(["downbeat", "tick", "tick", "tick", "downbeat", "tick", "tick", "tick"]);
});

test("subdivision multiplies the beat's spacing along with the downbeat's - splitting 6/8 into sixteenths still marks the same two dotted-quarter pulses, just with twice as many ticks between them", () => {
  // 6/8 at subdivision 2: clicksPerBar becomes 12 (metronome-engine.js's
  // resolve() does that multiplication), but accentEvery is handed to
  // clickLevel UN-multiplied (metronomePattern's raw 3) with subdivision
  // passed alongside it - phase % (accentEvery * subdivision) is clickLevel's
  // own job, not something a caller pre-computes.
  const levels = Array.from({ length: 12 }, (_, i) => clickLevel(i, 12, 3, 2));
  expect(levels).toEqual([
    "downbeat", "tick", "tick", "tick", "tick", "tick",
    "beat", "tick", "tick", "tick", "tick", "tick",
  ]);
});

test("phase is normalised into range before either test runs, the same defensive stance clickPhaseInBar takes on its own input", () => {
  expect(clickLevel(6, 6, 3, 1)).toBe("downbeat"); // wraps to 0
  expect(clickLevel(-3, 6, 3, 1)).toBe("beat"); // wraps to 3
  expect(clickLevel(-6, 6, 3, 1)).toBe("downbeat"); // wraps to 0
});

test("clickLevel guards a degenerate bar or a bad accentEvery rather than propagating NaN into a modulus", () => {
  expect(clickLevel(0, 0, 3, 1)).toBe("tick");
  expect(clickLevel(3, -6, 3, 1)).toBe("tick");
  expect(clickLevel(Number.NaN, 6, 3, 1)).toBe("tick");
  // No usable accentEvery still answers correctly for phase 0 (still the
  // downbeat, tested first) and never claims "beat" for anything else.
  expect(clickLevel(0, 6, Number.NaN, 1)).toBe("downbeat");
  expect(clickLevel(3, 6, Number.NaN, 1)).toBe("tick");
  expect(clickLevel(3, 6, 0, 1)).toBe("tick");
});

test("a subdivision that is not a positive integer is treated as 1 rather than corrupting the beat spacing", () => {
  for (const bad of [0, -1, Number.NaN, undefined, null, "two"]) {
    expect(clickLevel(3, 6, 3, bad), `subdivision ${String(bad)}`).toBe("beat");
  }
});

// -------------------------------------------------------------- click timing
//
// secondsPerClick takes a click RATE already in clicks per minute - the same
// number effectiveClickRate produces and the same number a caller displays -
// so there is no meter or mode left for it to know about. The meter-aware
// arithmetic is entirely effectiveClickRate's (see "proportion mode converts
// onto the meter's OWN click unit" above); this only ever converts a rate to
// a period.

test("seconds per click halves when the rate doubles", () => {
  expect(secondsPerClick(60)).toBeCloseTo(1, 9);
  expect(secondsPerClick(120)).toBeCloseTo(0.5, 9);
});

test("192 clicks a minute (96 quarter notes converted onto an eighth-note meter) is a click every 312.5ms", () => {
  expect(secondsPerClick(192)).toBeCloseTo(0.3125, 9);
});

// ----------------------------------------------------------------- bar lookup

test("an empty bar list answers null rather than throwing", () => {
  expect(barAtTick([], 5000)).toBeNull();
  expect(barAtTick(null, 5000)).toBeNull();
});

test("a tick before the first bar still gets an answer", () => {
  const bars = [{ startTick: 0, endTick: 1920, numerator: 4, denominator: 4 }];
  expect(barAtTick(bars, -10)).toEqual(bars[0]);
});

test("a tick lands in the bar that started at or before it, not the next one", () => {
  const bars = [
    { startTick: 0, endTick: 3840, numerator: 4, denominator: 4 },
    { startTick: 3840, endTick: 7680, numerator: 6, denominator: 8 },
    { startTick: 7680, endTick: 9600, numerator: 3, denominator: 4 },
  ];
  expect(barAtTick(bars, 0)).toEqual(bars[0]);
  expect(barAtTick(bars, 3839)).toEqual(bars[0]);
  // exactly on the boundary belongs to the bar that starts there
  expect(barAtTick(bars, 3840)).toEqual(bars[1]);
  expect(barAtTick(bars, 5000)).toEqual(bars[1]);
  expect(barAtTick(bars, 7680)).toEqual(bars[2]);
  // past the last bar's start, it is still the last bar
  expect(barAtTick(bars, 999999)).toEqual(bars[2]);
});

test("a single-bar list answers that bar for any tick at or after its start", () => {
  const bars = [{ startTick: 0, endTick: 1920, numerator: 5, denominator: 8 }];
  expect(barAtTick(bars, 0)).toEqual(bars[0]);
  expect(barAtTick(bars, 123456)).toEqual(bars[0]);
});

test("a repeated section reorders bars on the played timeline, and the lookup follows the PLAYED order", () => {
  // Bar A (4/4) plays, repeats (plays again), THEN bar B (6/8) plays once -
  // exactly the shape a repeat sign produces on the generated MIDI timeline.
  // A hand-summed index over the NOTATED bars (A, B) would place B right
  // after A's first pass and get every tick from there on wrong; this list
  // models the PLAYED order instead, which is what barAtTick is handed.
  const bars = [
    { startTick: 0, endTick: 3840, numerator: 4, denominator: 4 }, // A, pass 1
    { startTick: 3840, endTick: 7680, numerator: 4, denominator: 4 }, // A, pass 2 (the repeat)
    { startTick: 7680, endTick: 11520, numerator: 6, denominator: 8 }, // B, after the repeat
  ];
  // A tick that would land inside notated bar B's own duration (3840) if
  // measured from bar A's single notated length, but is still within A's
  // repeated second pass on the PLAYED timeline.
  expect(barAtTick(bars, 5000)).toEqual(bars[1]);
  expect(barAtTick(bars, 5000).denominator).toBe(4);
  // only past the repeat does the lookup reach B
  expect(barAtTick(bars, 8000)).toEqual(bars[2]);
});

// ------------------------------------------------------------- phase in bar

test("phase is 0 at the very start of a bar and clicksPerBar - 1 just before its end", () => {
  const bar = { startTick: 1000, endTick: 1000 + 4 * 480 };
  expect(clickPhaseInBar(1000, bar, 4)).toBe(0);
  expect(clickPhaseInBar(1000 + 480, bar, 4)).toBe(1);
  expect(clickPhaseInBar(1000 + 2 * 480, bar, 4)).toBe(2);
  expect(clickPhaseInBar(1000 + 3 * 480 + 200, bar, 4)).toBe(3);
});

test("phase resets at a bar boundary regardless of how far the previous bar's count had run", () => {
  // The whole reason this is derived from the playhead rather than carried
  // forward: bar two's phase must not continue counting from wherever bar
  // one left off.
  const barOne = { startTick: 0, endTick: 1920 };
  const barTwo = { startTick: 1920, endTick: 3840 };
  expect(clickPhaseInBar(1919, barOne, 6)).toBe(5);
  expect(clickPhaseInBar(1920, barTwo, 6)).toBe(0);
});

test("a tick past the bar's own end still answers with the last slot, not an out-of-range one", () => {
  // Covers a click scheduled slightly ahead of the audio clock (see
  // METRONOME_SCHEDULE_AHEAD_S in score-render.js) landing a few ticks past
  // where the playhead has actually reached so far.
  const bar = { startTick: 0, endTick: 1920 };
  expect(clickPhaseInBar(1920, bar, 6)).toBe(5);
  expect(clickPhaseInBar(999999, bar, 6)).toBe(5);
});

test("a null or degenerate bar answers phase 0 rather than NaN", () => {
  expect(clickPhaseInBar(500, null, 4)).toBe(0);
  expect(clickPhaseInBar(500, { startTick: 0, endTick: 0 }, 4)).toBe(0);
  expect(clickPhaseInBar(500, { startTick: 0, endTick: 1920 }, 0)).toBe(0);
});

test("a loop whose length is not a whole number of click periods still answers correctly at every point sampled - it never needs to know about the loop at all", () => {
  // This is the shape of the "three-bar loop at seventy per cent" case the
  // review named: the click's own rate has nothing to do with the bar
  // length, so sampling it at arbitrary, non-bar-aligned real ticks (as an
  // independent, differently-paced click would) still has to answer with
  // wherever THAT tick actually falls - not drift because of what a
  // persistent counter happened to reach.
  const bar = { startTick: 10_000, endTick: 10_000 + 6 * 240 }; // 6/8, division 480 -> 240 ticks/eighth
  for (let i = 0; i < 6; i++) {
    expect(clickPhaseInBar(10_000 + i * 240 + 37, bar, 6)).toBe(i);
  }
});
