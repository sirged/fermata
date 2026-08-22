// The practice metronome (issue #60), against the real alphaTab renderer and
// a real Web Audio click - not a mock of either.
//
// Every timing and accent assertion here reads the instant and frequency
// actually handed to Web Audio (OscillatorNode.prototype.start, wrapped in
// beforeEach) rather than a value the interface reports ALONGSIDE that call.
// That distinction is the point, not decoration: the click count and accent
// flag score-render.js writes to the DOM used to be published from a
// SEPARATE call sibling to the one that actually creates the oscillator, so
// deleting the real scheduling call entirely left every dataset-based
// assertion in an earlier version of this suite passing regardless - the
// exact "asserts what the code intended, not what it did" failure this
// project has shipped more than once (see score-render.js's own comment on
// scheduleClick for the fix: onClick now fires from INSIDE it). Wrapping
// OscillatorNode.prototype.start closes that gap from the test side too: it
// cannot be satisfied by anything short of a real oscillator node actually
// being started, on the real audio clock, at the real frequency - the same
// "wrap the constructor and count" principle instruments.spec.js uses for
// AudioContext, one level more specific.
//
// This is measured rather than believed, and re-measured whenever the click
// moves. When it was lifted out of score-render.js into metronome-engine.js
// (issue #97), the oscillator was deleted from scheduleClick by hand with
// every piece of bookkeeping left in place: EIGHT of the twelve tests below
// went red. The four that did not are the four built on the dataset helpers
// (waitForClickCount / collectClickMeta), which is what those helpers are for
// and what their own comment says at length. If a future change to this file
// leaves fewer than eight failing under that mutation, the suite has quietly
// stopped being the thing it is here to be.
import { expect, test } from "@playwright/test";
import {
  stubMetronomeScore,
  stubMetronomeScoreOther,
  stubMetronomeScoreRepeat,
  stubMetronomeScoreShortLoop,
} from "./fixtures/metronome-score.js";

const host = (page) => page.locator(".at-host");
const playButton = (page) => page.locator(".player button.primary");
const metronomeButton = (page) => page.locator('button:has-text("Metronome")');
const modeSelect = (page) => page.locator("select.metronome-mode");
const proportionInput = (page) => page.locator("input.metronome-proportion");
const bpmInput = (page) => page.locator("input.metronome-bpm");
const speedSelect = (page) => page.locator('select[title="Playback speed"]');
const loopButton = (page) => page.locator('button:has-text("Loop")');
const countInButton = (page) => page.locator('button:has-text("Count-in")');
// Roughly halfway between METRONOME_TICK_HZ (950) and METRONOME_ACCENT_HZ
// (1500) in score-render.js - a threshold rather than an exact match so this
// suite is not coupled to the precise constants, only to "clearly the higher
// one".
const ACCENT_HZ_THRESHOLD = 1200;

test.beforeEach(async ({ page }) => {
  await page.addInitScript((accentThreshold) => {
    // Independent evidence that a click reaches real audio machinery, not
    // just a value in a Svelte $state - see instruments.spec.js for the same
    // trick applied to AudioContext.
    window.__audioContexts = 0;
    for (const name of ["AudioContext", "webkitAudioContext"]) {
      const Original = window[name];
      if (!Original) continue;
      window[name] = class extends Original {
        constructor(...args) {
          super(...args);
          window.__audioContexts += 1;
        }
      };
    }
    // The ground truth for every timing and accent assertion below: the
    // exact audio-clock instant (`when`, in the AudioContext's own seconds)
    // and frequency actually handed to a real OscillatorNode's start() -
    // not a wall-clock Date.now() sampled whenever Playwright's polling
    // happened to notice a dataset attribute change (which has its own
    // jitter and, at a fast enough click rate, can even collapse several
    // real clicks into what looks like one), and not a value published by
    // code that runs alongside the real scheduling call rather than only as
    // a consequence of it.
    window.__oscillatorStarts = [];
    window.__accentHzThreshold = accentThreshold;
    const OriginalStart = OscillatorNode.prototype.start;
    OscillatorNode.prototype.start = function (when, ...rest) {
      window.__oscillatorStarts.push({ when: when ?? 0, frequency: this.frequency.value });
      return OriginalStart.call(this, when, ...rest);
    };
  }, ACCENT_HZ_THRESHOLD);
  await stubMetronomeScore(page);
  await stubMetronomeScoreOther(page);
  await stubMetronomeScoreShortLoop(page);
  await stubMetronomeScoreRepeat(page);
  await page.goto("/#/score/1");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
});

/** Waits until `n` real oscillators have been started, then returns their
 * {when, frequency} records - the ground truth described above. */
async function oscillatorStarts(page, n, timeout = 20_000) {
  await page.waitForFunction((count) => (window.__oscillatorStarts?.length ?? 0) >= count, n, { timeout });
  return page.evaluate((count) => window.__oscillatorStarts.slice(0, count), n);
}

/** Real audio-clock gaps, in milliseconds, between the 1st..Nth real click. */
async function clickGapsMs(page, n, timeout) {
  const starts = await oscillatorStarts(page, n, timeout);
  const gaps = [];
  for (let i = 1; i < starts.length; i++) gaps.push((starts[i].when - starts[i - 1].when) * 1000);
  return gaps;
}

function average(values) {
  return values.reduce((a, b) => a + b, 0) / values.length;
}

/** Waits until the real, scheduled click count on the host reaches `n` - a
 * valid synchronisation point because publishMetronomeClick's dataset write
 * happens from inside the same real scheduling call oscillatorStarts reads
 * (see scheduleClick in metronome-engine.js). Used for meter/accent/phase
 * bookkeeping oscillator frequency alone cannot carry (which bar, which
 * slot in it).
 *
 * READ THIS BEFORE TRUSTING A TEST BUILT ON IT. This is a BOOKKEEPING
 * assertion, and it is not coverage against the click going missing. It reads
 * a dataset attribute, so it passes for a click that was announced without a
 * real oscillator behind it - which is precisely the failure this suite's own
 * header describes shipping once already. That gap is closed from the product
 * side (onClick fires only from inside the oscillator-creating call) and from
 * the test side by the tests that read OscillatorNode.prototype.start
 * directly - not by this helper.
 *
 * Measured, not assumed: after the metronome moved out of the renderer seam,
 * the oscillator was deleted from scheduleClick by hand with all bookkeeping
 * left intact. Eight of the twelve tests in this file went red. The four that
 * did NOT are exactly the four built on this helper and collectClickMeta
 * below - the mid-bar phase test, the loop-wrap test, the repeat-meter test
 * and the counter-reset test. Each carries a note saying so. They are worth
 * having: no oscillator's frequency can tell you WHICH BAR a click landed in.
 * They are just not the tests that would notice the click was gone. */
async function waitForClickCount(page, n, timeout = 20_000) {
  await page.waitForFunction(
    (count) => Number(document.querySelector(".at-host")?.dataset.metronomeClicks || 0) >= count,
    n,
    { timeout },
  );
}

/** The 1st..Nth click's dataset snapshot (meter/phase), read immediately
 * after each one is confirmed scheduled. Bookkeeping, with the same caveat as
 * waitForClickCount above: not coverage against a missing oscillator. */
async function collectClickMeta(page, count) {
  const out = [];
  for (let n = 1; n <= count; n++) {
    await waitForClickCount(page, n);
    const [numerator, denominator, phase] = await Promise.all([
      host(page).getAttribute("data-metronome-numerator"),
      host(page).getAttribute("data-metronome-denominator"),
      host(page).getAttribute("data-metronome-phase"),
    ]);
    out.push({ numerator, denominator, phase: Number(phase) });
  }
  return out;
}

test("the metronome is a second, independent audio path: off produces no real oscillators, on while playing starts a real AudioContext and real clicks, off stops scheduling more", async ({
  page,
}) => {
  // Not yet playing, not yet enabled - toggling it on here must not create
  // any audio machinery by itself; see prime()/ensureAudioCtx in
  // score-render.js, which only run once playback actually starts.
  await metronomeButton(page).click();
  const beforePlay = await page.evaluate(() => window.__audioContexts);
  expect(await page.evaluate(() => window.__oscillatorStarts.length)).toBe(0);

  await playButton(page).click();
  // Proves an AudioContext attributable to enabling+playing the metronome
  // exists at all - not, on its own, that playPause()'s prime() call is what
  // put it there ahead of the scheduler's own ensureAudioCtx() a moment
  // later. Telling those two apart would mean asserting on Chromium's
  // autoplay-gesture policy actually denying a late resume() in this
  // harness, which was not attempted.
  await expect
    .poll(() => page.evaluate(() => window.__audioContexts), { timeout: 5_000 })
    .toBeGreaterThan(beforePlay);

  await oscillatorStarts(page, 3);
  const startsWhileOn = (await page.evaluate(() => window.__oscillatorStarts.length));
  expect(startsWhileOn).toBeGreaterThanOrEqual(3);

  // Turning it off mid-playback must stop the scheduler from starting any
  // MORE real oscillators - not merely stop being audible (alphaTab's own,
  // permanently-muted metronome is a separate thing entirely).
  await metronomeButton(page).click();
  await page.waitForTimeout(700);
  const startsAfterOff = await page.evaluate(() => window.__oscillatorStarts.length);
  expect(startsAfterOff).toBe(startsWhileOn);
});

test("the live tempo shown is the actual click RATE - clicks per minute, matching the real measured gap between clicks, not a quarter-note figure a listener would have to convert", async ({
  page,
}) => {
  await metronomeButton(page).click();
  await playButton(page).click();

  // 6/8 at the fixture's declared 96 quarter-note BPM, default 100%
  // proportion: 96 quarters a minute is 192 EIGHTHS a minute, since each
  // quarter is two eighths - and 192, not 96, is both what the readout must
  // show and the real measured rate below.
  await expect(host(page)).toHaveAttribute("data-metronome-bpm", "192");
  await expect(metronomeButton(page).locator(".metronome-readout")).toHaveText("192");

  const gaps = await clickGapsMs(page, 4);
  // 60_000 / 192 = 312.5ms - measured on the real audio clock, which the
  // review measured as effectively driftless, so this can be tight.
  for (const gap of gaps) {
    expect(gap, `gaps: ${gaps.join(", ")}`).toBeGreaterThan(300);
    expect(gap, `gaps: ${gaps.join(", ")}`).toBeLessThan(325);
  }
});

test("6/8 accents every third eighth, exactly where the real phase says it should - verified against the real oscillator frequency, not just the dataset flag reported alongside it", async ({
  page,
}) => {
  // Deliberately does not assume which phase slot the run happens to start
  // on. alphaTab's own player has its own startup latency separate from
  // this click's clock (they are two independent AudioContexts, primed at
  // slightly different moments - see prime() in score-render.js), so the
  // very first click or two of a fresh play can land while the real
  // playhead has not genuinely started advancing yet, showing an
  // artificially low phase - correct given the real information available
  // at that instant, not a defect in reading it. Waiting past that startup
  // window before switching the metronome on, as this test does, is what
  // makes the phase run predictable enough to assert on tightly - each
  // click exactly one slot after the last, wrapping at six - without
  // depending on exactly where that run begins.
  await loopButton(page).click();
  await playButton(page).click();
  await page.waitForTimeout(500);
  await metronomeButton(page).click();

  const [starts, meta] = await Promise.all([oscillatorStarts(page, 6), collectClickMeta(page, 6)]);
  expect(new Set(meta.map((c) => `${c.numerator}/${c.denominator}`))).toEqual(new Set(["6/8"]));

  const phases = meta.map((c) => c.phase);
  for (let i = 1; i < phases.length; i++) {
    expect(phases[i], `phases: ${phases.join(", ")}`).toBe((phases[i - 1] + 1) % 6);
  }

  // The real oscillator frequency, not the dataset accent flag alongside
  // it, has to agree with what the real phase says should be accented -
  // every third slot (0, 3) starting from the downbeat.
  const accents = starts.map((s) => s.frequency > ACCENT_HZ_THRESHOLD);
  expect(accents).toEqual(phases.map((p) => p % 3 === 0));
});

test("fixed-BPM mode clicks the typed rate itself, in every meter - not a quarter-note tempo converted onto the meter's unit", async ({
  page,
}) => {
  await metronomeButton(page).click();
  await modeSelect(page).selectOption("bpm");
  await bpmInput(page).fill("240");
  await bpmInput(page).press("Tab");
  // Playback itself slowed to a quarter of the click's own tempo - if the
  // click were still riding on alphaTab's playbackSpeed (the bug this issue
  // exists to fix), the gap below would come out around 1000ms, not 250ms.
  await speedSelect(page).selectOption("0.5");
  await loopButton(page).click();
  await playButton(page).click();

  await expect(metronomeButton(page).locator(".metronome-readout")).toHaveText("240");

  const gaps = await clickGapsMs(page, 6);
  // 240 BPM IS the click rate here, full stop - 60,000/240 = 250ms exactly,
  // regardless of the 6/8 meter's own eighth-note unit. The real audio
  // clock measures this with effectively no jitter, so the bounds only need
  // to be wide enough to rule out the two wrong answers this could produce:
  // 125ms (proportion mode's quarter-note-onto-eighth conversion applied to
  // 240 instead) or 500ms (still coupled to the halved playback speed).
  for (const gap of gaps) {
    expect(gap, `gaps: ${gaps.join(", ")}`).toBeGreaterThan(240);
    expect(gap, `gaps: ${gaps.join(", ")}`).toBeLessThan(260);
  }
});

test("a proportion click tracks the score's own tempo, not the playback speed set alongside it", async ({
  page,
}) => {
  await metronomeButton(page).click();
  await modeSelect(page).selectOption("proportion");
  // 25% of the fixture's declared 96 BPM, converted onto the 6/8 eighth-note
  // unit: 96 * 0.25 * 2 = 48 clicks a minute, a click every 1250ms. Playback
  // itself sped UP to double, in the opposite direction from the bpm-mode
  // test above: if the click's tempo were still tied to playbackSpeed, this
  // gap would come out around 625ms, not 1250ms - proving the decoupling
  // holds in both directions, not just the one the other test happens to
  // check.
  await proportionInput(page).fill("25");
  await proportionInput(page).press("Tab");
  await speedSelect(page).selectOption("1.25");
  await loopButton(page).click();
  await playButton(page).click();

  await expect(metronomeButton(page).locator(".metronome-readout")).toHaveText("48");

  const gaps = await clickGapsMs(page, 4);
  for (const gap of gaps) {
    expect(gap, `gaps: ${gaps.join(", ")}`).toBeGreaterThan(1235);
    expect(gap, `gaps: ${gaps.join(", ")}`).toBeLessThan(1265);
  }
});

test("gig mode shows the live tempo but not the mode/value controls - a stand is not where those get set", async ({
  page,
}) => {
  await metronomeButton(page).click();
  await playButton(page).click();
  await oscillatorStarts(page, 1);

  await page.locator('button[title*="Distraction-free"]').click();
  await expect(page.locator(".gig-hud")).toBeVisible();
  await expect(page.locator(".gig-hud .metronome-indicator")).toHaveText("♩ 192");
  // the toolbar - and the mode/proportion/bpm controls in it - is gone
  await expect(page.locator("select.metronome-mode")).toHaveCount(0);
  await expect(page.locator("input.metronome-proportion")).toHaveCount(0);
});

// BOOKKEEPING (dataset, not the audio boundary) - survives an
// oscillator-deletion mutation by design; see waitForClickCount.
test("enabling the metronome mid-bar accents wherever the music actually is, not the start of a fresh count", async ({
  page,
}) => {
  // Play first, with the metronome OFF, and let real playback run into the
  // middle of bar 1 (6/8 at 96 BPM: each eighth is 0.3125s, so bar 1 spans
  // 0-1.875s; waiting 1.1s lands around the 3rd-4th eighth) BEFORE switching
  // the click on. A phase that only ever resets to 0 when the scheduler
  // starts would report phase 0 here regardless - this is what tells that
  // apart from a phase actually read off the playhead.
  await loopButton(page).click();
  await playButton(page).click();
  await page.waitForTimeout(1100);
  await metronomeButton(page).click();

  const [meta] = await collectClickMeta(page, 1);
  expect(meta.numerator).toBe("6");
  expect(meta.denominator).toBe("8");
  expect(meta.phase).not.toBe(0);
});

test("the click stays silent during the count-in and starts only once real playback begins", async ({
  page,
}) => {
  await metronomeButton(page).click();
  await countInButton(page).click();
  await playButton(page).click();

  // The count-in for this fixture (6/8 at 96 BPM) is one bar - about 1.875s
  // at the count-in's own (unscaled) tempo. Well inside that window not one
  // real oscillator may have been started, regardless of what alphaTab's
  // own (separately volumed, never muted) count-in click is doing.
  await page.waitForTimeout(1000);
  expect(await page.evaluate(() => window.__oscillatorStarts.length)).toBe(0);

  // ...and it does start, once the count-in finishes - silence forever would
  // pass the assertion above for the wrong reason.
  await oscillatorStarts(page, 1, 5_000);
});

// BOOKKEEPING (dataset, not the audio boundary) - survives an
// oscillator-deletion mutation by design; see waitForClickCount.
test("a loop whose length is not a whole number of click periods does not walk the accent off the downbeat after it wraps", async ({
  page,
}) => {
  // The headline case from the review: a short loop (2 bars of 6/8 at 96
  // BPM - 3.75s of real audio) clicked at 70% (~0.446s/click, chosen so
  // neither the click period nor the bar period divides the other evenly).
  // A phase carried forward as a counter that only resets when the
  // scheduler starts would settle into a perfectly regular "+1 mod 6" cycle
  // measured by click count and stay there forever, oblivious to the loop
  // restarting under it. A phase read fresh from the playhead cannot: the
  // real position resets at every bar (twice a loop here), out of step with
  // the click's own cadence, so somewhere in a long-enough run the naive
  // "next phase = previous + 1" prediction has to be wrong.
  await page.goto("/#/score/3");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();
  await modeSelect(page).selectOption("proportion");
  await proportionInput(page).fill("70");
  await proportionInput(page).press("Tab");
  await loopButton(page).click();
  await playButton(page).click();

  const meta = await collectClickMeta(page, 24);
  expect(new Set(meta.map((c) => `${c.numerator}/${c.denominator}`))).toEqual(new Set(["6/8"]));
  expect(meta.every((c) => c.phase >= 0 && c.phase < 6)).toBe(true);

  const naiveIncrement = meta.slice(1).some((c, i) => c.phase !== (meta[i].phase + 1) % 6);
  expect(naiveIncrement, `phases: ${meta.map((c) => c.phase).join(", ")}`).toBe(true);
});

// BOOKKEEPING (dataset, not the audio boundary) - survives an
// oscillator-deletion mutation by design; see waitForClickCount.
test("a repeat sign does not desync the click's meter - the second pass of a repeated section still reports the repeated meter, not whatever bar follows it", async ({
  page,
}) => {
  // Two bars of 4/4 at 120 BPM, repeated once, then one bar of 6/8 - three
  // NOTATED bars playing as five (4/4, 4/4, 4/4, 4/4, 6/8). A tick->meter
  // index built by summing NOTATED bar durations would place the 6/8 bar
  // right after the repeated section's FIRST pass (4s in) rather than after
  // its second (8s in), misreporting the meter - and therefore the click
  // rate - for the whole second pass. See metronome-score.js for the fixture
  // and metronome.js's barAtTick for why this has to come from alphaTab's
  // own generated-MIDI tick lookup instead.
  await page.goto("/#/score/4");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();
  await modeSelect(page).selectOption("proportion");
  await proportionInput(page).fill("100");
  await proportionInput(page).press("Tab");
  await playButton(page).click();

  // 4/4 at 120 BPM clicks a quarter note every 0.5s. The repeated section's
  // SECOND pass runs from real time 4s to 8s - click #12 (at ~6s) lands
  // squarely inside it, well clear of either boundary.
  const meta = await collectClickMeta(page, 12);
  for (const c of meta) {
    expect(`${c.numerator}/${c.denominator}`, JSON.stringify(meta)).toBe("4/4");
  }

  // ...and playback does go on to reach the 6/8 bar - confirming the meter
  // actually changes once the repeat is behind it, rather than this fixture
  // having failed to reach bar 3 at all within the test's patience.
  await page.waitForFunction(
    () => document.querySelector(".at-host")?.dataset.metronomeDenominator === "8",
    { timeout: 15_000 },
  );
});

// BOOKKEEPING (dataset, not the audio boundary) - survives an
// oscillator-deletion mutation by design; see waitForClickCount. This one is
// ABOUT the bookkeeping, so that is the right level for it.
test("switching a mounted viewer to a different score resets the metronome's click counter instead of inheriting the previous score's count", async ({
  page,
}) => {
  await metronomeButton(page).click();
  await playButton(page).click();
  await waitForClickCount(page, 3);
  const beforeSwitch = Number(await host(page).getAttribute("data-metronome-clicks"));
  expect(beforeSwitch).toBeGreaterThanOrEqual(3);

  // A same-document hash change - the SPA's own router, not a full
  // navigation - so the same TabViewer instance (and the same host element)
  // is what score-render.js's next createScoreView call has to leave clean,
  // exactly the scenario publish()'s own dataset.scoreProfiles delete
  // exists for.
  await page.evaluate(() => {
    location.hash = "#/score/2";
  });
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });

  // Immediately on the new view existing - before anything has had a chance
  // to play - the stale count must already be gone, not merely about to be
  // overtaken by fresh clicks that would mask it passing this assertion for
  // the wrong reason.
  expect(await host(page).getAttribute("data-metronome-clicks")).toBeNull();
});

test("a stall longer than the scheduler's lookahead drops the missed clicks instead of firing them all at once", async ({
  page,
}) => {
  await metronomeButton(page).click();
  await modeSelect(page).selectOption("bpm");
  // Fast enough (300 BPM = one click every 200ms) that a 700ms stall would
  // miss three or four clicks under the bug this guards against - clearly
  // enough to tell a burst apart from ordinary scheduling.
  await bpmInput(page).fill("300");
  await bpmInput(page).press("Tab");
  await loopButton(page).click();
  await playButton(page).click();

  // A baseline click or two before the stall.
  await oscillatorStarts(page, 2);

  // Blocks the page's own main thread - where the scheduler's setInterval
  // runs - for long enough to fall behind. The AudioContext clock this is
  // measured against lives on a separate audio thread and keeps advancing
  // throughout, which is exactly what creates the stall this reproduces.
  await page.evaluate(() => {
    const until = Date.now() + 700;
    while (Date.now() < until) {
      /* deliberately busy-blocking the main thread */
    }
  });

  const gaps = await clickGapsMs(page, 8);
  // A burst reads as one or more near-zero real-audio-clock gaps clustered
  // together - Web Audio starts every oscillator whose scheduled `when` has
  // already passed essentially at once. Half the nominal 200ms interval is
  // generous enough to allow for the catch-up guard's own small resync gap
  // while still catching a genuine pile-up.
  expect(gaps.every((g) => g > 90), `gaps: ${gaps.join(", ")}`).toBe(true);
});
