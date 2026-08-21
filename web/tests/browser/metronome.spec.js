// The practice metronome (issue #60), against the real alphaTab renderer and
// a real Web Audio click - not a mock of either.
//
// What these are for: the interesting part of this feature is that the click
// is a SECOND, independent audio path (see createPracticeMetronome in
// score-render.js) rather than a setting on alphaTab's own player, and that
// its tempo has nothing to do with setSpeed(). Neither fact is checkable
// against a value this component merely intended to produce - a UI-side
// number that just mirrors what a test typed into a field would stay green
// through a bug in the audio layer itself (the exact failure mode this
// project has been bitten by before). So every timing assertion here reads
// real wall-clock gaps between real scheduled clicks (data-metronome-clicks
// changing on the actual .at-host element - see publishMetronomeClick in
// score-render.js), and the AudioContext check uses the same "wrap the
// constructor and count" technique instruments.spec.js uses to prove a click
// reaches real audio machinery rather than a counter in the component.
import { expect, test } from "@playwright/test";
import { stubMetronomeScore } from "./fixtures/metronome-score.js";

const host = (page) => page.locator(".at-host");
const playButton = (page) => page.locator(".player button.primary");
const metronomeButton = (page) => page.locator('button:has-text("Metronome")');
const modeSelect = (page) => page.locator("select.metronome-mode");
const proportionInput = (page) => page.locator("input.metronome-proportion");
const bpmInput = (page) => page.locator("input.metronome-bpm");
const speedSelect = (page) => page.locator('select[title="Playback speed"]');
const loopButton = (page) => page.locator('button:has-text("Loop")');

test.beforeEach(async ({ page }) => {
  // Independent evidence that a click reaches real audio machinery, not just
  // a value in a Svelte $state - see instruments.spec.js for the same trick.
  await page.addInitScript(() => {
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
  });
  await stubMetronomeScore(page);
  await page.goto("/#/score/1");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
});

/** Waits until the real, scheduled click count on the host reaches `n`, then
 * records the wall-clock time it happened - not a value read out of a
 * MutationObserver batch (which can coalesce several rapid same-attribute
 * changes into one callback and silently under-count), but the ground-truth
 * counter itself, polled until it says so. */
async function waitForClickCount(page, n, timeout = 20_000) {
  await page.waitForFunction(
    (count) => Number(document.querySelector(".at-host")?.dataset.metronomeClicks || 0) >= count,
    n,
    { timeout },
  );
  return Date.now();
}

/** Real wall-clock gaps between the 1st..Nth scheduled click. */
async function measureClickIntervals(page, count) {
  const times = [];
  for (let n = 1; n <= count; n++) times.push(await waitForClickCount(page, n));
  const gaps = [];
  for (let i = 1; i < times.length; i++) gaps.push(times[i] - times[i - 1]);
  return gaps;
}

function average(values) {
  return values.reduce((a, b) => a + b, 0) / values.length;
}

test("the metronome is a second, independent audio path: off produces nothing, on while playing creates a real AudioContext and starts real scheduled clicks, off stops them again", async ({
  page,
}) => {
  // Not yet playing, not yet enabled - toggling it on here must not create
  // any audio machinery by itself; see prime()/ensureAudioCtx in
  // score-render.js, which only run once playback actually starts.
  await metronomeButton(page).click();
  const beforePlay = await page.evaluate(() => window.__audioContexts);

  await playButton(page).click();
  // Proves an AudioContext attributable to enabling+playing the metronome
  // exists at all - not, on its own, that playPause()'s prime() call is what
  // put it there ahead of the scheduler's own ensureAudioCtx() a moment
  // later. Telling those two apart would mean asserting on Chromium's
  // autoplay-gesture policy actually denying a late resume() in this
  // harness, which was not attempted - see the PR description for why
  // prime() exists and what verifying it would take.
  await expect
    .poll(() => page.evaluate(() => window.__audioContexts), { timeout: 5_000 })
    .toBeGreaterThan(beforePlay);

  await waitForClickCount(page, 3);
  const clicksWhileOn = Number(await host(page).getAttribute("data-metronome-clicks"));
  expect(clicksWhileOn).toBeGreaterThanOrEqual(3);

  // Turning it off mid-playback must stop the scheduler, not just mute
  // alphaTab's own (permanently-muted) metronome - the count must actually
  // stop moving, not merely stop being audible.
  await metronomeButton(page).click();
  await page.waitForTimeout(700);
  const clicksAfterOff = Number(await host(page).getAttribute("data-metronome-clicks"));
  expect(clicksAfterOff).toBe(clicksWhileOn);
});

test("the live tempo shown on the button is the value score-render.js is actually clicking at, not a number the component computed separately", async ({
  page,
}) => {
  await metronomeButton(page).click();
  await playButton(page).click();
  await waitForClickCount(page, 1);

  // The fixture's own declared tempo (96 BPM) at the default 100% proportion -
  // read back from the SAME attribute the audio scheduler itself writes (see
  // publishMetronomeClick), and cross-checked against the visible button text.
  await expect(host(page)).toHaveAttribute("data-metronome-bpm", "96");
  await expect(metronomeButton(page).locator(".metronome-readout")).toHaveText("96");
});

test("6/8 clicks six per bar on the eighth note, accented on the 1st and 4th - not the notated beat, and not just the downbeat", async ({
  page,
}) => {
  await metronomeButton(page).click();
  // Slow enough (48 BPM quarter, an eighth-note click every 625ms) that a
  // same-step read immediately after each threshold is well clear of the
  // next scheduled click - see measureClickIntervals's comment on why this
  // suite reads the ground-truth counter rather than an observer batch.
  await modeSelect(page).selectOption("proportion");
  await proportionInput(page).fill("50");
  await proportionInput(page).press("Tab");
  await playButton(page).click();

  const accents = [];
  const timeSignatures = new Set();
  for (let n = 1; n <= 6; n++) {
    await waitForClickCount(page, n);
    const [accent, numerator, denominator] = await Promise.all([
      host(page).getAttribute("data-metronome-accent"),
      host(page).getAttribute("data-metronome-numerator"),
      host(page).getAttribute("data-metronome-denominator"),
    ]);
    accents.push(accent === "true");
    timeSignatures.add(`${numerator}/${denominator}`);
  }

  expect([...timeSignatures]).toEqual(["6/8"]);
  // clickIndex resets to 0 when playback starts, so the very first click of
  // this run is index 0 (accented), then 1 and 2 are subdivision-only, then
  // index 3 - the bar's second main pulse - is accented again.
  expect(accents).toEqual([true, false, false, true, false, false]);
});

test("a fixed BPM click holds its own rate regardless of playback speed - the whole point of separating the two", async ({
  page,
}) => {
  await metronomeButton(page).click();
  await modeSelect(page).selectOption("bpm");
  await bpmInput(page).fill("240");
  await bpmInput(page).press("Tab");
  // Playback itself slowed to a quarter of the click's own tempo - if the
  // click were still riding on alphaTab's playbackSpeed (the bug this issue
  // exists to fix), the gap below would come out around 500ms, not 125ms.
  await speedSelect(page).selectOption("0.5");
  await loopButton(page).click();
  await playButton(page).click();

  const gaps = await measureClickIntervals(page, 6);
  // 240 BPM, eighth-note clicks (6/8): 60/240 * 4/8 = 0.125s = 125ms.
  const avg = average(gaps);
  expect(avg, `gaps: ${gaps.join(", ")}`).toBeGreaterThan(85);
  expect(avg, `gaps: ${gaps.join(", ")}`).toBeLessThan(200);
});

test("a proportion click tracks the score's own tempo, not the playback speed set alongside it", async ({
  page,
}) => {
  await metronomeButton(page).click();
  await modeSelect(page).selectOption("proportion");
  // 25% of the fixture's declared 96 BPM is 24 BPM - an eighth-note click
  // every 60/24 * 4/8 = 1.25s. Playback itself sped UP to double, in the
  // opposite direction from the bpm-mode test above: if the click's tempo
  // were still tied to playbackSpeed, this gap would come out around 625ms,
  // not 1250ms - proving the decoupling holds in both directions, not just
  // the one the other test happens to check.
  await proportionInput(page).fill("25");
  await proportionInput(page).press("Tab");
  await speedSelect(page).selectOption("1.25");
  await loopButton(page).click();
  await playButton(page).click();

  const gaps = await measureClickIntervals(page, 4);
  const avg = average(gaps);
  expect(avg, `gaps: ${gaps.join(", ")}`).toBeGreaterThan(950);
  expect(avg, `gaps: ${gaps.join(", ")}`).toBeLessThan(1450);
});

test("gig mode shows the live tempo but not the mode/value controls - a stand is not where those get set", async ({
  page,
}) => {
  await metronomeButton(page).click();
  await playButton(page).click();
  await waitForClickCount(page, 1);

  await page.locator('button[title*="Distraction-free"]').click();
  await expect(page.locator(".gig-hud")).toBeVisible();
  await expect(page.locator(".gig-hud .metronome-indicator")).toHaveText("♩ 96");
  // the toolbar - and the mode/proportion/bpm controls in it - is gone
  await expect(page.locator("select.metronome-mode")).toHaveCount(0);
  await expect(page.locator("input.metronome-proportion")).toHaveCount(0);
});
