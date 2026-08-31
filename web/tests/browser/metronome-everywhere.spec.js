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

import {
  METRONOME_MUSICXML_NO_TEMPO,
  stubMetronomeScore,
  stubMetronomeScoreFast,
  stubMetronomeScoreLateTempo,
  stubMetronomeScoreNoTempo,
  stubMetronomeScoreOther,
  stubMetronomeScoreRepeat,
} from "./fixtures/metronome-score.js";
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
const limitNote = (page) => page.locator(".metronome-limit");
const playButton = (page) => page.locator(".player button.primary");

// ABOVE METRONOME_BEAT_HZ (1180) in metronome-engine.js - a threshold rather
// than an exact match, so this suite is coupled only to "clearly the highest
// of the three".
const ACCENT_HZ_THRESHOLD = 1200;
// Halfway between METRONOME_TICK_HZ (950) and METRONOME_BEAT_HZ (1180) -
// "clearly the middle one, not the plain tick".
const BEAT_HZ_THRESHOLD = 1065;

/** The three-level name for a real oscillator frequency, read off the same
 * two thresholds every test in this file uses. */
function levelOf(frequency) {
  return frequency > ACCENT_HZ_THRESHOLD ? "downbeat" : frequency > BEAT_HZ_THRESHOLD ? "beat" : "tick";
}

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

// -------------------------------------------- issue #121: a bar with no bar

test("in 6/8 the bar is audible, not only the grouping - a third, distinct click marks where it starts", async ({
  page,
}) => {
  await page.goto("/#/metronome");
  await meterSelect(page).selectOption("6/8");
  await bpmInput(page).fill("240");
  await bpmInput(page).press("Tab");
  await subdivisionSelect(page).selectOption("1");
  await startButton(page).click();

  // Two bars and one click over, so the third downbeat proves the pattern
  // repeats rather than being a fluke of where the run happened to start -
  // the standalone page always starts fresh at phase 0 (start() resets
  // freeRunPhase), so this sequence is deterministic from the very first
  // click.
  const starts = await oscillatorStarts(page, 13);
  const levels = starts.map((s) => levelOf(s.frequency));
  expect(levels, `frequencies: ${starts.map((s) => s.frequency).join(",")}`).toEqual([
    "downbeat", "tick", "tick", "beat", "tick", "tick",
    "downbeat", "tick", "tick", "beat", "tick", "tick",
    "downbeat",
  ]);

  // Stated directly on the real frequencies too, in the exact shape the
  // issue's own report used: the downbeat (index 0) is higher than the beat
  // (index 3), which is itself higher than a plain tick (index 1). Red today
  // at the first inequality alone - phase 0 and phase 3 were byte-identical,
  // both 1500 Hz.
  expect(starts[0].frequency).toBeGreaterThan(starts[3].frequency);
  expect(starts[3].frequency).toBeGreaterThan(starts[1].frequency);
});

test("compound meters of different lengths do not sound the same - 6/8 and 9/8 are not the same click stream wearing different labels", async ({
  page,
}) => {
  await page.goto("/#/metronome");
  await meterSelect(page).selectOption("6/8");
  await bpmInput(page).fill("300");
  await bpmInput(page).press("Tab");
  await subdivisionSelect(page).selectOption("1");
  await startButton(page).click();
  const sixEight = (await oscillatorStarts(page, 25)).map((s) => levelOf(s.frequency));

  await startButton(page).click(); // stop
  await page.evaluate(() => window.__resetOscillatorStarts());
  await meterSelect(page).selectOption("9/8");
  await startButton(page).click(); // a fresh start - phase resets to the downbeat

  const nineEight = (await oscillatorStarts(page, 25)).map((s) => levelOf(s.frequency));

  // Red today for a reason no threshold constant can dodge: both meters
  // produce the literal same level string - "downbeat, tick, tick, beat,
  // tick, tick, ..." repeating - because there was nothing but the grouping
  // to click, and the grouping repeats identically in every compound meter
  // regardless of how many groups the bar actually holds. This is the
  // sharpest of the three tests here: it cannot be satisfied by anything
  // short of the bar itself becoming audible.
  expect(nineEight, `6/8: ${sixEight.join(",")} / 9/8: ${nineEight.join(",")}`).not.toEqual(sixEight);
});

test("the subdivision option that names the eighth really clicks the eighth, not the sixteenth underneath it", async ({
  page,
}) => {
  await page.goto("/#/metronome");
  await meterSelect(page).selectOption("6/8");

  // Read the picker's own option labels rather than assuming which numeric
  // value carries "Eighths" - written against the OBSERVABLE (the label, and
  // the rate it produces), not the mechanism, so this passes whichever way
  // the label defect gets fixed: relabelling the rungs to match what they
  // always clicked, or remapping the factors so "Eighths" becomes the
  // meter's own ×1 in 6/8.
  const options = await subdivisionSelect(page)
    .locator("option")
    .evaluateAll((opts) => opts.map((o) => ({ value: o.value, label: o.textContent })));
  const eighths = options.find((o) => o.label === "Eighths");
  expect(eighths, `options: ${JSON.stringify(options)}`).toBeTruthy();
  await subdivisionSelect(page).selectOption(eighths.value);

  await bpmInput(page).fill("100");
  await bpmInput(page).press("Tab");
  await startButton(page).click();

  // Paired with the count below, so a relabel that leaves the audio
  // untouched (the option renamed but still clicking sixteenths) cannot pass
  // this: 6/8 read as eighths at box 100 has to measure ~100 clicks a
  // minute, not the 200 the mislabelled "Eighths" used to produce.
  const rate = await measuredRate(page, 6);
  expect(rate, `measured ${rate}`).toBeGreaterThan(97);
  expect(rate, `measured ${rate}`).toBeLessThan(103);

  // And the count between downbeats has to be six - a bar of 6/8 clicked on
  // the eighth, not twelve (sixteenths, what "Eighths" used to actually
  // click) or eighteen (sixteenth triplets).
  const starts = await oscillatorStarts(page, 13);
  const downbeats = [];
  starts.forEach((s, i) => {
    if (levelOf(s.frequency) === "downbeat") downbeats.push(i);
  });
  expect(downbeats.length, `frequencies: ${starts.map((s) => s.frequency).join(",")}`).toBeGreaterThanOrEqual(2);
  expect(downbeats[1] - downbeats[0], `downbeats at: ${downbeats.join(",")}`).toBe(6);
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

test("a transcription whose own document declares no tempo says assumed rather than transcribed - nothing was lifted off anything", async ({
  page,
}) => {
  // The interaction between the two words, and the case that matters most in
  // practice: the extractor emits no tempo direction at all when it read no
  // tempo off the PDF (see musicxml.build - `if opening and tempo`), so this is
  // the shape a great many real transcriptions have. "transcribed ♩ = 120"
  // would claim a number was lifted out of a scanned page; the 120 is the
  // renderer's fallback and came from nowhere.
  await stubScoreApi(
    page,
    transcriptionResponse({
      warnings: [],
      confidence: CLEAN_CONFIDENCE,
      content: METRONOME_MUSICXML_NO_TEMPO,
      format: "musicxml",
    }),
  );
  await page.goto("/#/score/1");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();
  await expect(baseNote(page)).toHaveText(/assumed 120 bpm/);
  await expect(baseNote(page)).toContainText("none in the score");
  // Anchored on .metronome-base, which the transcription test above proves can
  // match on this very route.
  await expect(baseNote(page)).not.toContainText("transcribed");
  await expect(baseNote(page)).not.toContainText("marked");
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

  // Cleanup, not a check - nothing above asserts anything about these rows,
  // so there is nothing for either read below to race.
  // out-of-band-ok: teardown
  const leftover = (await (await request.get("/api/practice/sessions?limit=1000")).json()).sessions;
  // out-of-band-ok: teardown
  for (const s of leftover) await request.delete(`/api/practice/sessions/${s.id}`);
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

test("a score that prints no tempo at all calls the number assumed and says there was none to read", async ({
  page,
}) => {
  // Issue #102, and the reason the two tests above could both pass while this
  // was broken: they use fixtures that DECLARE a tempo. alphaTab's
  // Score.tempo is a getter answering 120 whenever the first bar holds no
  // tempo automation, so an edition printing *Andante* and no number - most of
  // the classical material in this library - was reported as
  // "marked ♩ = 120". A value we invented, presented as one we read, on the
  // most visible number on the control.
  //
  // And it is said in PLAIN WORDS, not in engraver's notation. "♩ = 96" is how
  // a tempo is printed on a page and is right for a tempo somebody printed;
  // using it for our own fallback put the invention in the notation of a
  // marking, which undercut the very sentence saying it was not one.
  await stubMetronomeScoreNoTempo(page);
  await page.goto("/#/score/6");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();

  // The word first: "assumed", never "marked" - the same word this application
  // uses at every other site for a value it chose rather than read. Asserted on
  // .metronome-base, which the two tests above prove can match, so "no such
  // element" cannot be what makes the negative assertion pass.
  await expect(baseNote(page)).toHaveText(/assumed 120 bpm/);
  await expect(baseNote(page)).not.toContainText("marked");
  await expect(baseNote(page)).not.toContainText("transcribed");
  // ...and not in the notation of a marking.
  await expect(baseNote(page)).not.toContainText("♩ =");
  // ...and then, in visible text rather than a title attribute, that there was
  // nothing to read. "default ♩ = 120" alone still leaves a reader working out
  // whether 120 came from somewhere.
  await expect(baseNote(page)).toContainText("none in the score");
  await expect(baseNote(page)).toContainText("120");
  // The same unobtrusive mark the app uses for anything unverified - present
  // here, and absent on the printed-marking score above, which is what makes
  // it mean anything.
  await expect(baseNote(page).locator(".mark")).toHaveCount(1);

  // TWO LINES, and the toolbar no wider than its own box. This is a sentence
  // where the other two states are a three-token label, and the toolbar is a
  // single non-wrapping flex row with six other controls in it - held on one
  // line it clips the profile buttons at the far end, and left to wrap wherever
  // a character cap falls it can take three. Measured, because no assertion on
  // the TEXT can see either happen.
  const lines = await baseNote(page).evaluate((el) => {
    const style = getComputedStyle(el);
    const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.2;
    return Math.round(el.getBoundingClientRect().height / lineHeight);
  });
  expect(lines, "the base note is expected to occupy exactly two lines").toBe(2);
  const toolbar = await page.locator(".toolbar").evaluate((el) => ({
    scrollWidth: el.scrollWidth,
    clientWidth: el.clientWidth,
  }));
  expect(toolbar.scrollWidth, JSON.stringify(toolbar)).toBeLessThanOrEqual(toolbar.clientWidth);

  // The control beside it must not contradict it. Keeping the proportion mode
  // is right - the click has to run at something, and 100% of the assumed tempo
  // is what it runs at - but an option reading "% of score tempo" next to a note
  // saying the score declares none is two labels on one control disagreeing,
  // which is worse than either being wrong alone.
  await expect(modeSelect(page).locator("option[value=proportion]")).toHaveText(
    "% of assumed tempo",
  );
  await expect(modeSelect(page)).not.toContainText("% of score tempo");

  // The number is still the number the click actually runs at. Saying "default
  // 120" beside a click running at something else would be a different lie,
  // and 4/4 means the quarter-note base and the click rate are one number.
  await expect(readout(page)).toHaveText("120");
  await page.locator('button:has-text("Loop")').click();
  await playButton(page).click();
  const rate = await measuredRate(page, 8);
  expect(rate, `measured ${rate}`).toBeGreaterThan(119);
  expect(rate, `measured ${rate}`).toBeLessThan(121);
});

test("a score whose tempo mark sits in a later bar is not told it has none - that would be the same fault with the sign flipped", async ({
  page,
}) => {
  // The false-assertion direction, and the one to be most suspicious of in a
  // change like this. This fixture DOES declare a tempo - 88, on bar two, which
  // is what a score looks like when it opens with a pickup or when the exporter
  // attached the mark to the first real note. The number the renderer reports
  // is still the 120 fallback, so "default" is right; but the first version of
  // this work looked only at bar one and went on to PRINT that the score
  // declares no tempo, which is untrue of the document. Failing to mention a
  // fact (#102) and asserting a false one are not the same size of mistake.
  await stubMetronomeScoreLateTempo(page);
  await page.goto("/#/score/7");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();

  await expect(baseNote(page)).toHaveText(/assumed 120 bpm/);
  // The weaker claim, which is true here: nothing at the start.
  await expect(baseNote(page)).toContainText("none at the start");
  // NOT the stronger one, which is false here. Anchored on .metronome-base,
  // which the tests above prove can match.
  await expect(baseNote(page)).not.toContainText("none in the score");
  await expect(baseNote(page)).not.toContainText("marked");
  // The accessible label carries the same distinction, rather than the mark
  // saying one thing and its label another.
  await expect(baseNote(page).locator(".mark")).toHaveAttribute(
    "aria-label",
    /marks no tempo at its start/,
  );
  // The word is the same one everywhere else, in the label as in the text.
  await expect(baseNote(page).locator(".mark")).toHaveAttribute(
    "aria-label",
    /was assumed rather than read/,
  );
});

// ------------------------------------------- where the range runs out

test("a percentage the click cannot actually sound says so in plain sight, rather than leaving the readout looking wrong", async ({
  page,
}) => {
  // 15% of a piece marked 120 is 18 clicks a minute, which MIN_METRONOME_BPM
  // correctly refuses - a click that slow stops being a metronome and starts
  // being a wait. The click therefore runs at 20, and a control still reading
  // "15%" beside a readout of "20" is a percentage that has quietly stopped
  // being a percentage. Showing the true rate is most of the answer; without
  // the reason, the disagreement reads as a bug rather than as a floor.
  await stubMetronomeScoreRepeat(page);
  await page.goto("/#/score/4");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();
  await presetSelect(page).selectOption("15");

  // The rate shown is the real one - the floor, not the wish.
  await expect(readout(page)).toHaveText("20");
  // ...and the reason is VISIBLE TEXT, not a title attribute. A phone at a
  // music stand has no pointer to hover with, and this is exactly the moment
  // the reader is holding an instrument instead of a mouse.
  await expect(limitNote(page)).toBeVisible();
  await expect(limitNote(page)).toHaveText("at its slowest");

  // Backing off the percentage until the click can sound it takes the notice
  // away again, so it never becomes decoration that stops meaning anything.
  await presetSelect(page).selectOption("50");
  await expect(readout(page)).toHaveText("60");
  await expect(limitNote(page)).toHaveCount(0);
});

test("the ceiling is reached on an ordinary meter at an ordinary preset, and says so - it is the end of the range a player meets first", async ({
  page,
}) => {
  // A sweep of the preset ladder against real meters found the ceiling roughly
  // eighteen times more reachable than the floor: 150% of a piece marked 144
  // in 6/8 asks for 432, and 175% of 120 asks 420 in 6/8, 9/8 and 12/8 alike.
  // Running a jig or a 12/8 blues above tempo is ordinary practice, not an
  // extreme, so this end of the range is the one that actually needs to
  // explain itself - and the earlier version of this work only tested the
  // floor.
  await stubMetronomeScoreFast(page);
  await page.goto("/#/score/5");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();

  // 100% of 144 onto 6/8's eighth-note unit is 288 - inside the range, so
  // nothing is claimed yet. Asserted first so the notice below is known to
  // appear because of the preset and not because it is simply always there.
  await expect(readout(page)).toHaveText("288");
  await expect(limitNote(page)).toHaveCount(0);

  await presetSelect(page).selectOption("150");
  // 144 * 1.5 * 2 = 432, held at 400.
  await expect(readout(page)).toHaveText("400");
  await expect(limitNote(page)).toHaveText("at its fastest");

  // ...and the click really does run at 400, not at the 432 that was asked
  // for. The notice would be worth nothing if the rate beside it were a
  // fiction: 400 a minute is 150ms, where 432 would be 138.9ms.
  await page.locator('button:has-text("Loop")').click();
  await playButton(page).click();
  const rate = await measuredRate(page, 10);
  expect(rate, `measured ${rate}`).toBeGreaterThan(398);
  expect(rate, `measured ${rate}`).toBeLessThan(402);
});

test("the top of the range says so too, and the notice is not styled as an error", async ({ page }) => {
  await page.goto("/#/metronome");
  await bpmInput(page).fill("176");
  await bpmInput(page).press("Tab");
  await subdivisionSelect(page).selectOption("3");
  // 176 in triplets is 528 clicks a minute; MAX_METRONOME_BPM holds it at 400,
  // both to keep it a tempo practice happens at and to keep one click's tail
  // clear of the next one's attack.
  await expect(bigReadout(page)).toHaveText("400");
  await expect(limitNote(page)).toHaveText("at its fastest");

  // Nothing here went wrong - a fact about the tempo is being reported, in the
  // same register as the tempo itself. The project's rule is that nothing which
  // is not a fault is styled as one, and --danger is how a fault is spelled.
  const danger = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue("--danger").trim(),
  );
  expect(danger, "--danger is expected to exist, or this assertion proves nothing").not.toBe("");
  // Resolved through a probe element, the same way practice.spec.js does it:
  // the token holds a hex string while getComputedStyle().color answers in
  // rgb(), so comparing the two directly would be a tautology that passes
  // however red this text became.
  const dangerRgb = await page.evaluate((hex) => {
    const probe = document.createElement("span");
    probe.style.color = hex;
    document.body.appendChild(probe);
    const value = getComputedStyle(probe).color;
    probe.remove();
    return value;
  }, danger);
  const colour = await limitNote(page).evaluate((el) => getComputedStyle(el).color);
  expect(colour).not.toBe(dangerRgb);

  await subdivisionSelect(page).selectOption("1");
  await expect(bigReadout(page)).toHaveText("176");
  await expect(limitNote(page)).toHaveCount(0);
});

// --------------------- state that has to survive, and state that must not

test("a per-piece tempo does not survive a full reload - a proportion left over from a slow passage is not the next session's default", async ({
  page,
}) => {
  await stubMetronomeScore(page);
  await page.goto("/#/score/1");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();
  await presetSelect(page).selectOption("35");
  await expect(readout(page)).toHaveText("67");

  // A full reload, which is the strongest form of the question: nothing at all
  // is carried in memory, so anything still here came out of storage.
  await page.reload();
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();
  await expect(presetSelect(page)).toHaveValue("100");
  await expect(readout(page)).toHaveText("192");
});

test("a tempo set for one score is still set when a mounted viewer is switched to another", async ({ page }) => {
  // The sibling of the gig-mode case: there the component is unmounted and the
  // control survives; here the component survives and the control under it is
  // replaced. Both are "state versus a lifecycle", and the answers differ on
  // purpose - within one session, switching pieces to work the same passage at
  // the same proportion should not make you set it up again.
  await stubMetronomeScore(page);
  await stubMetronomeScoreOther(page);
  await page.goto("/#/score/1");
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await metronomeButton(page).click();
  await presetSelect(page).selectOption("50");
  await expect(readout(page)).toHaveText("96");

  await page.evaluate(() => {
    location.hash = "#/score/2";
  });
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await expect(presetSelect(page)).toHaveValue("50");
  // Score 2 is 4/4 at 140, so 50% is 70 - the SETTING carried over and was
  // re-resolved against the new piece, rather than the old piece's resolved
  // number being carried over with it.
  await expect(readout(page)).toHaveText("70");
});

test("leaving a page while the click is running really stops it, rather than leaving a scheduler behind", async ({
  page,
}) => {
  // The metronome on its own owns its scheduler and its AudioContext, so
  // navigating away has to tear both down. A leaked interval is inaudible
  // right up to the moment it is not - two of them, after two visits, click
  // twice.
  await page.goto("/#/metronome");
  await bpmInput(page).fill("300");
  await bpmInput(page).press("Tab");
  await startButton(page).click();
  await oscillatorStarts(page, 4);

  await page.evaluate(() => {
    location.hash = "#/";
  });
  // Long enough to catch a survivor in the act - at 300 a minute anything
  // still scheduling would fire four more clicks inside this window.
  await page.waitForTimeout(300);
  const afterLeaving = await page.evaluate(() => window.__oscillatorStarts.length);
  await page.waitForTimeout(900);
  expect(await page.evaluate(() => window.__oscillatorStarts.length)).toBe(afterLeaving);

  // Coming back arrives stopped, at what it was left at. A metronome that
  // starts clicking the moment a page opens is not one anybody asked for.
  await page.evaluate(() => {
    location.hash = "#/metronome";
  });
  await expect(bigReadout(page)).toHaveText("300");
  await page.waitForTimeout(500);
  expect(await page.evaluate(() => window.__oscillatorStarts.length)).toBe(afterLeaving);
});

test("on the practice page a tempo set by hand is not thrown away when the pre-fill it started from changes underneath it", async ({
  page,
  request,
}) => {
  // The same class of fault as the gig-mode one, found by going looking for
  // more of it. This page pre-fills from the last session's tempo, and that
  // number changes whenever the session list reloads - which happens every
  // time a session is logged. Rebuilding the control to take a new pre-fill
  // would destroy whatever had been dialled in since, stop the click, and
  // start it again somewhere else. Adopting a pre-fill only while nothing has
  // been set by hand is what avoids that.
  const scores = await (await request.get("/api/scores")).json();
  expect(
    scores,
    "refusing to run: this backend has scores in its library, so it is not the " +
      "throwaway instance the suite creates - and this test deletes practice history",
  ).toEqual([]);
  const clear = async () => {
    const rows = (await (await request.get("/api/practice/sessions?limit=1000")).json()).sessions;
    for (const r of rows) await request.delete(`/api/practice/sessions/${r.id}`);
  };
  await clear();

  // No sessions at all yet, so there is no pre-fill to start from.
  await page.goto("/#/practice");
  const section = page.locator("section.metronome-section");
  await expect(section).toBeVisible();
  await expect(section.locator(".metronome-readout-large")).toHaveText("120");

  // Dialled in by hand.
  await section.locator("input.metronome-bpm").fill("58");
  await section.locator("input.metronome-bpm").press("Tab");
  await expect(section.locator(".metronome-readout-large")).toHaveText("58");

  // Now a session with a tempo exists, and the page reloads its sessions -
  // exactly what logging practice does. The pre-fill goes from "unknown" to
  // 132, which is the change that used to rebuild the control.
  const res = await request.post("/api/practice/sessions", {
    data: { activity: "technique", seconds: 600, local_date: localDay(), target_tempo_bpm: 132 },
  });
  expect(res.ok(), await res.text()).toBe(true);
  await page.locator("button.log-other-button").click();
  await expect(section.locator(".hint")).toContainText("132");

  // The hint offers the new number; the control keeps the one that was chosen.
  await expect(section.locator(".metronome-readout-large")).toHaveText("58");
  await expect(section.locator("input.metronome-bpm")).toHaveValue("58");

  await clear();
});
