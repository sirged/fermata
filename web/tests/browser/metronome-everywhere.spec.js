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
  stubMetronomeScore,
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
