// The metronome as a general tool (issue #97): the same click, in every place
// that wants one, pre-filled from that place's own context and still
// adjustable afterwards.
//
// metronome.spec.js covers the click's own scheduling, inside the score
// viewer, where it has always lived. This file covers what moving it out
// bought: the standalone page, the practice page, the widened tempo control,
// and the honesty rule on a tempo that was inferred rather than read.
//
// Every timing and accent assertion here reads the instant and frequency
// actually handed to Web Audio (OscillatorNode.prototype.start, wrapped in
// beforeEach) rather than a value the interface reports ALONGSIDE that call -
// the same property metronome.spec.js's own header explains at length, and
// for the same reason: the dataset click counter this project once asserted on
// was published from a call SIBLING to the one that creates the oscillator, so
// deleting the real scheduling left every assertion passing. That is not a
// historical curiosity - after the refactor this file exists to cover, the
// whole oscillator was deleted from metronome-engine.js by hand (bookkeeping
// left intact) and 8 of the 12 tests in metronome.spec.js went red. These
// assertions are written to be in that 8, not the surviving 4.
import { expect, test } from "@playwright/test";

import { stubMetronomeScore } from "./fixtures/metronome-score.js";
import { CLEAN_CONFIDENCE, stubScoreApi, transcriptionResponse } from "./fixtures/transcription-warnings.js";
import { localDay } from "../../src/lib/practice.js";

const metronomeButton = (page) => page.locator('button:has-text("Metronome")');
const startButton = (page) => page.locator('.metronome.prominent > button');
const bigReadout = (page) => page.locator(".metronome-readout-large");
const readout = (page) => page.locator(".metronome-readout");
const modeSelect = (page) => page.locator("select.metronome-mode");
const presetSelect = (page) => page.locator("select.metronome-presets");
const bpmInput = (page) => page.locator("input.metronome-bpm");
const faster = (page) => page.locator("button.metronome-faster");
const slower = (page) => page.locator("button.metronome-slower");
const subdivisionSelect = (page) => page.locator("select.metronome-subdivision");
const meterSelect = (page) => page.locator("select.metronome-meter");
const accentButton = (page) => page.locator("button.metronome-accent");
const baseNote = (page) => page.locator(".metronome-base");
const playButton = (page) => page.locator(".player button.primary");

// Halfway between METRONOME_TICK_HZ (950) and METRONOME_ACCENT_HZ (1500) in
// metronome-engine.js - a threshold rather than an exact match, so this suite
// is coupled only to "clearly the higher one".
const ACCENT_HZ_THRESHOLD = 1200;

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    // The ground truth for every timing and accent assertion below: the exact
    // audio-clock instant and frequency actually handed to a real
    // OscillatorNode's start(). Not a wall-clock sample of when Playwright
    // happened to notice a dataset attribute change, and not a value
    // published by code that merely runs alongside the real scheduling call.
    window.__oscillatorStarts = [];
    const OriginalStart = OscillatorNode.prototype.start;
    OscillatorNode.prototype.start = function (when, ...rest) {
      window.__oscillatorStarts.push({ when: when ?? 0, frequency: this.frequency.value });
      return OriginalStart.call(this, when, ...rest);
    };
    // Cleared on demand, so a measurement taken AFTER a setting change is not
    // contaminated by clicks scheduled before it - including the up-to-120ms
    // of lookahead already queued at the instant the change lands.
    window.__resetOscillatorStarts = () => {
      window.__oscillatorStarts.length = 0;
    };
  });
});

/** Waits until `n` real oscillators have been started, then returns their
 * {when, frequency} records. */
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

/**
 * The click rate the audio clock actually ran at, in clicks per minute,
 * averaged over `n` real clicks. Averaged rather than checked gap by gap
 * because the assertions below are about a rate a single beat per minute
 * apart, and one click's worth of scheduling jitter at the start of a run is
 * larger than that difference; the audio clock itself is exact, so a handful
 * of gaps average to well inside 0.2 bpm.
 */
async function measuredRate(page, n = 6, timeout = 20_000) {
  const gaps = await clickGapsMs(page, n, timeout);
  return 60_000 / average(gaps);
}

/** Discards clicks already scheduled (up to the 120ms lookahead), so what is
 * measured next is the click as it is NOW set, not as it was a moment ago. */
async function afterSettingChange(page) {
  await page.waitForTimeout(400);
  await page.evaluate(() => window.__resetOscillatorStarts());
}

// ------------------------------------------------------ on its own

test("the metronome has a page of its own, and starting it there produces real clicks on the real audio clock at the rate shown", async ({
  page,
}) => {
  // "I just want a metronome" without opening a piece first - the whole
  // reason the click had to stop being a feature of the score viewer.
  await page.goto("/#/metronome");
  await expect(bigReadout(page)).toBeVisible();
  // No transport, no score: nothing may have made any audio before the one
  // gesture that starts it.
  expect(await page.evaluate(() => window.__oscillatorStarts.length)).toBe(0);

  await bpmInput(page).fill("150");
  await bpmInput(page).press("Tab");
  await expect(bigReadout(page)).toHaveText("150");
  await startButton(page).click();

  // 150 clicks a minute is 400ms exactly, and this is the number displayed -
  // shown and sounded are the same value or this assertion fails.
  const rate = await measuredRate(page, 6);
  expect(rate, `measured ${rate}`).toBeGreaterThan(149.5);
  expect(rate, `measured ${rate}`).toBeLessThan(150.5);

  // Stopping stops scheduling more, rather than merely going quiet.
  await startButton(page).click();
  await page.waitForTimeout(700);
  const afterStop = await page.evaluate(() => window.__oscillatorStarts.length);
  await page.waitForTimeout(700);
  expect(await page.evaluate(() => window.__oscillatorStarts.length)).toBe(afterStop);
});

test("one beat per minute is adjustable on its own, and the single step lands on the real click rate rather than only on the readout", async ({
  page,
}) => {
  // The granularity a tempo actually gets raised at over weeks, and what
  // makes a goal like "this section from 92 to 100" mean anything.
  await page.goto("/#/metronome");
  await bpmInput(page).fill("120");
  await bpmInput(page).press("Tab");
  await startButton(page).click();
  await expect(bigReadout(page)).toHaveText("120");

  await faster(page).click();
  await faster(page).click();
  await expect(bigReadout(page)).toHaveText("122");
  await afterSettingChange(page);

  // 122 a minute is 491.8ms; 120 would be 500ms and 121 would be 495.9ms.
  // Bounds tight enough that landing on either neighbour fails - a control
  // that moved the label but not the click, or moved the click by five,
  // cannot pass this.
  const rate = await measuredRate(page, 8);
  expect(rate, `measured ${rate}`).toBeGreaterThan(121.6);
  expect(rate, `measured ${rate}`).toBeLessThan(122.4);

  await slower(page).click();
  await expect(bigReadout(page)).toHaveText("121");
  await afterSettingChange(page);
  const slowed = await measuredRate(page, 8);
  expect(slowed, `measured ${slowed}`).toBeGreaterThan(120.6);
  expect(slowed, `measured ${slowed}`).toBeLessThan(121.4);
});

test("a subdivision splits each beat in the real audio, not just in the label - and the accent still lands on the bar rather than on every split", async ({
  page,
}) => {
  await page.goto("/#/metronome");
  await meterSelect(page).selectOption("4/4");
  await bpmInput(page).fill("60");
  await bpmInput(page).press("Tab");
  await subdivisionSelect(page).selectOption("2");
  await startButton(page).click();

  // 60 beats a minute in eighths is 120 clicks a minute - 500ms, not 1000ms.
  const rate = await measuredRate(page, 8);
  expect(rate, `measured ${rate}`).toBeGreaterThan(119);
  expect(rate, `measured ${rate}`).toBeLessThan(121);

  // Eight eighth-note clicks in 4/4 is one bar's worth, so exactly one of
  // every eight is the accented downbeat - the accent follows the BAR, not
  // the subdivision. Read off the real oscillator frequency.
  const starts = await oscillatorStarts(page, 16);
  const accents = starts.map((s) => s.frequency > ACCENT_HZ_THRESHOLD);
  expect(accents.filter(Boolean).length, `accents: ${accents.join(",")}`).toBe(2);
});

test("turning the accent off makes every real click identical, and turning it back on restores a louder, higher one", async ({
  page,
}) => {
  await page.goto("/#/metronome");
  await meterSelect(page).selectOption("4/4");
  await bpmInput(page).fill("240");
  await bpmInput(page).press("Tab");
  await startButton(page).click();

  // On by default: over two bars of 4/4 exactly two clicks are the accent.
  const withAccent = await oscillatorStarts(page, 8);
  expect(withAccent.filter((s) => s.frequency > ACCENT_HZ_THRESHOLD).length).toBe(2);

  await accentButton(page).click();
  await afterSettingChange(page);
  // Asserted on the real frequency handed to Web Audio, so "accent off"
  // cannot be satisfied by a flag the interface stopped displaying while the
  // audio went on accenting.
  const withoutAccent = await oscillatorStarts(page, 12);
  expect(
    withoutAccent.every((s) => s.frequency < ACCENT_HZ_THRESHOLD),
    `frequencies: ${withoutAccent.map((s) => s.frequency).join(",")}`,
  ).toBe(true);

  await accentButton(page).click();
  await afterSettingChange(page);
  const restored = await oscillatorStarts(page, 12);
  expect(
    restored.some((s) => s.frequency > ACCENT_HZ_THRESHOLD),
    `frequencies: ${restored.map((s) => s.frequency).join(",")}`,
  ).toBe(true);
});

test("standing on its own, the metronome arrives at what it was last left at - the only context it has", async ({
  page,
}) => {
  await page.goto("/#/metronome");
  await bpmInput(page).fill("176");
  await bpmInput(page).press("Tab");
  await meterSelect(page).selectOption("6/8");
  // Eighths, not triplets, deliberately: 176 in triplets is 528 clicks a
  // minute, which MAX_METRONOME_BPM pulls back to 400 - correctly, and the
  // readout then honestly says 400 rather than 176. Which is a fine thing for
  // it to do and a confusing thing to assert remembering through, so this
  // stays inside the range where the beat and the click rate differ by a
  // factor the arithmetic is not hiding: 176 * 2 = 352.
  await subdivisionSelect(page).selectOption("2");
  await expect(bigReadout(page)).toHaveText("352");

  // A full reload, not a hash change - the component is built from scratch,
  // so anything still here came back from storage rather than from memory.
  await page.reload();
  await expect(bpmInput(page)).toHaveValue("176");
  await expect(meterSelect(page)).toHaveValue("6/8");
  await expect(subdivisionSelect(page)).toHaveValue("2");
  await expect(bigReadout(page)).toHaveText("352");

  // ...and it is a starting point, not a constraint.
  await bpmInput(page).fill("44");
  await bpmInput(page).press("Tab");
  await expect(bigReadout(page)).toHaveText("88");
});

// ------------------------------------------------------ over a piece

test("the tempo presets reach far below half speed, and a passage really can be clicked there", async ({
  page,
}) => {
  // The old ladder bottomed out at half speed. For a passage that is beyond
  // you, half speed is not slow enough - being able to go much slower is the
  // difference between practising a bar and avoiding it.
  await stubMetronomeScore(page);
  await page.goto("/#/score/1");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();
  await expect(modeSelect(page)).toHaveValue("proportion");

  const offered = await presetSelect(page).locator("option").evaluateAll((o) => o.map((x) => x.value));
  expect(offered).toContain("15");
  expect(offered).toContain("175");

  await presetSelect(page).selectOption("15");
  await page.locator('button:has-text("Loop")').click();
  await playButton(page).click();

  // 15% of the fixture's declared 96 quarter-note BPM, converted onto 6/8's
  // eighth-note unit: 96 * 0.15 * 2 = 28.8, rounded to 29 for display and
  // scheduled at exactly the same number. Well under the 96 a half-speed
  // floor would have bottomed out at.
  await expect(readout(page)).toHaveText("29");
  const rate = await measuredRate(page, 4, 30_000);
  expect(rate, `measured ${rate}`).toBeGreaterThan(28);
  expect(rate, `measured ${rate}`).toBeLessThan(30);
});

test("nudging one beat per minute over a piece takes the tempo over as a fixed number, seeded from what is actually being heard", async ({
  page,
}) => {
  // A single beat per minute is an absolute quantity, so it cannot be held
  // together with "a percentage of the piece". Taking the tempo over from the
  // live rate - rather than stepping the percentage by a point, which would
  // move the real rate by however many bpm one percent happens to be - is
  // what lets a goal name an actual number.
  await stubMetronomeScore(page);
  await page.goto("/#/score/1");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();
  await page.locator('button:has-text("Loop")').click();
  await playButton(page).click();

  // 6/8 at 96 quarter-note BPM, 100%: 192 eighth-note clicks a minute.
  await expect(readout(page)).toHaveText("192");

  await faster(page).click();
  await expect(modeSelect(page)).toHaveValue("bpm");
  await expect(bpmInput(page)).toHaveValue("193");
  await expect(readout(page)).toHaveText("193");

  await afterSettingChange(page);
  // 193 a minute is 310.9ms. 192 would be 312.5ms - only 1.6ms away, which is
  // why this is averaged; the audio clock itself is exact.
  const rate = await measuredRate(page, 10);
  expect(rate, `measured ${rate}`).toBeGreaterThan(192.5);
  expect(rate, `measured ${rate}`).toBeLessThan(193.5);
});

test("a tempo printed on the page is called marked; one lifted out of a transcription is called transcribed and carries the unverified mark", async ({
  page,
}) => {
  // The honesty rule: a tempo we inferred must not look like one we read.
  await stubMetronomeScore(page);
  await page.goto("/#/score/1");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();
  // The fixture is a MusicXML file with a real printed metronome mark at 96.
  await expect(baseNote(page)).toHaveText(/marked/);
  await expect(baseNote(page)).toContainText("96");
  await expect(baseNote(page)).not.toContainText("transcribed");
  await expect(baseNote(page).locator(".mark")).toHaveCount(0);
});

test("the same control over a transcription says transcribed, so the number it is a percentage of cannot be mistaken for one read off a page", async ({
  page,
}) => {
  await stubScoreApi(page, transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE }));
  await page.goto("/#/score/1");
  // The transcription is rendered beside the PDF; its own tempo is 90, and
  // nothing printed on the page was read to get it.
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();
  await expect(baseNote(page)).toHaveText(/transcribed/);
  await expect(baseNote(page)).toContainText("90");
  await expect(baseNote(page)).not.toContainText("marked");
  // The same unobtrusive mark the rest of the app uses for something
  // unverified - present here and absent on the printed-marking score above,
  // which is what makes it mean anything.
  await expect(baseNote(page).locator(".mark")).toHaveCount(1);
});

// ------------------------------------------------------ on the practice page

test("the practice page has a click of its own, pre-filled from the tempo the last session was working towards, and it really clicks there", async ({
  page,
  request,
}) => {
  // Refuses to touch anything that is not the throwaway instance this suite
  // starts - the cleanup below DELETES practice sessions, which is the one
  // thing in this application that cannot be regenerated from the files on
  // disk. Same guard as practice.spec.js, for the same reason.
  const scores = await (await request.get("/api/scores")).json();
  expect(
    scores,
    "refusing to run: this backend has scores in its library, so it is not the " +
      "throwaway instance the suite creates - and this test deletes practice history",
  ).toEqual([]);
  const existing = (await (await request.get("/api/practice/sessions?limit=1000")).json()).sessions;
  for (const s of existing) await request.delete(`/api/practice/sessions/${s.id}`);

  const today = localDay();
  // A real session, through the real endpoint: the pre-fill has to come from
  // what the server actually stored, not from a shape this test invented.
  const res = await request.post("/api/practice/sessions", {
    data: { activity: "technique", seconds: 900, local_date: today, tempo_bpm: 92, target_tempo_bpm: 104 },
  });
  expect(res.ok(), await res.text()).toBe(true);

  await page.goto("/#/practice");
  const section = page.locator("section.metronome-section");
  await expect(section).toBeVisible();
  // The TARGET, not the tempo already managed: a ladder exists to be climbed,
  // so tomorrow should be handed what yesterday was working towards.
  await expect(section.locator(".metronome-readout-large")).toHaveText("104");
  await expect(section.locator("input.metronome-bpm")).toHaveValue("104");
  await expect(section.locator(".hint")).toContainText("104");

  await section.locator(".metronome.prominent > button").click();
  const rate = await measuredRate(page, 6);
  expect(rate, `measured ${rate}`).toBeGreaterThan(103);
  expect(rate, `measured ${rate}`).toBeLessThan(105);

  // A pre-fill is a starting point, not a constraint.
  await section.locator("input.metronome-bpm").fill("60");
  await section.locator("input.metronome-bpm").press("Tab");
  await afterSettingChange(page);
  const changed = await measuredRate(page, 6);
  expect(changed, `measured ${changed}`).toBeGreaterThan(59);
  expect(changed, `measured ${changed}`).toBeLessThan(61);

  for (const s of (await (await request.get("/api/practice/sessions?limit=1000")).json()).sessions) {
    await request.delete(`/api/practice/sessions/${s.id}`);
  }
});

test("a tempo set up before stepping into gig mode is still set when stepping back out", async ({ page }) => {
  // Gig mode strips the toolbar - and the metronome's controls with it - which
  // means the component holding those settings is unmounted and rebuilt around
  // the trip. Setting a piece up and putting it on a stand is exactly the
  // sequence that happens, so coming back to find the tempo silently reset to
  // its default would be a real loss and a quiet one: you would not know until
  // you heard it.
  await stubMetronomeScore(page);
  await page.goto("/#/score/1");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();
  await presetSelect(page).selectOption("35");
  await page.locator('button:has-text("Loop")').click();
  await playButton(page).click();

  // 35% of 96 quarter-note BPM onto 6/8's eighth-note unit: 96 * 0.35 * 2 =
  // 67.2, rounded once to 67.
  await expect(readout(page)).toHaveText("67");

  await page.locator('button[title*="Distraction-free"]').click();
  await expect(page.locator(".gig-hud")).toBeVisible();
  await expect(page.locator(".gig-hud .metronome-indicator")).toHaveText("♩ 67");

  await page.keyboard.press("Escape");
  await expect(page.locator(".toolbar")).toBeVisible();
  await expect(presetSelect(page)).toHaveValue("35");
  await expect(readout(page)).toHaveText("67");

  // ...and the real audio agrees, which the readout alone cannot establish:
  // 67 a minute is 895.5ms, where a silent reset to 100% would be 312.5ms.
  await afterSettingChange(page);
  const rate = await measuredRate(page, 4, 30_000);
  expect(rate, `measured ${rate}`).toBeGreaterThan(66);
  expect(rate, `measured ${rate}`).toBeLessThan(68);
});
