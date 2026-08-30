// How is this piece going (#57), against the real backend and the real build.
//
// WHAT THESE PROVE THAT THE SERVER TESTS CANNOT.
// server/tests/test_score_progress_api.py pins every figure the endpoint
// computes, to the second. It cannot prove any of them reached the screen, and
// #95's lesson - recorded next door in zz-library-missing.spec.js - is that a
// guarantee nothing renders is a guarantee nobody has. So what is asserted
// here is the seam: the numbers that are actually drawn, the empty state a new
// piece gets INSTEAD of a screen of noughts, the sentence a single tempo point
// gets instead of a line, and the fact that a deleted piece keeps its hours and
// loses its link.
//
// AND ONE THING ONLY A BROWSER CAN CHECK AT ALL: that the tempo values are in
// the page as text rather than in a tooltip. This is read from a music stand,
// and a chart whose numbers appear on hover is a chart nobody standing at one
// can read.
//
// WHY IT IS NAMED TO SORT LAST. It puts scores in the library, and every other
// spec here refuses to run against a backend holding scores it did not put
// there - including zzz-library-organise, whose own beforeEach empties the
// library. Sorting after all of them keeps every one of those refusals true.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { addDays, localDay } from "../../src/lib/practice.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, "..", "..", "test-fixtures", "notation-only.musicxml");

const SCAN_DEADLINE_MS = 30_000;

const today = localDay();

async function scanSettled(request) {
  const deadline = Date.now() + SCAN_DEADLINE_MS;
  for (;;) {
    const status = await (await request.get("/api/scan/status")).json();
    if (!status.scanning) return status;
    if (Date.now() > deadline) throw new Error("a scan never finished");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

/** A real score in the throwaway library. Same shape as
 * zzz-library-organise.spec.js's helper and for the same reasons: the content
 * is made distinct per name so the scanner's content-hash relink does not
 * treat the second upload as a rename of the first, and the scan the upload
 * starts is waited out so a later delete is not refused for arriving during
 * it. */
async function upload(request, name) {
  const body = Buffer.concat([fs.readFileSync(FIXTURE), Buffer.from(`<!-- ${name} -->\n`)]);
  const res = await request.post("/api/upload?folder=Uploads", {
    multipart: { file: { name, mimeType: "application/xml", buffer: body } },
  });
  expect(res.ok(), await res.text()).toBe(true);
  let found;
  await expect(async () => {
    const scores = await (await request.get("/api/scores")).json();
    found = scores.find((s) => s.path === `Uploads/${name}`);
    expect(found, `${name} never appeared in the library`).toBeTruthy();
  }).toPass({ timeout: SCAN_DEADLINE_MS });
  await scanSettled(request);
  return found;
}

/** One session against a piece. Awaited before anything navigates, so nothing
 * here reads a page for state a request has not finished writing (#110). */
async function practise(request, scoreId, body) {
  const res = await request.post(`/api/scores/${scoreId}/practice`, { data: body });
  expect(res.ok(), await res.text()).toBe(true);
  return res.json();
}

async function emptyTheLibrary(request) {
  await scanSettled(request);
  for (const score of await (await request.get("/api/scores")).json()) {
    await request.delete(`/api/scores/${score.id}`);
  }
  for (const score of await (await request.get("/api/trash")).json()) {
    await request.delete(`/api/trash/${score.id}`);
  }
  const sessions = (await (await request.get("/api/practice/sessions?limit=1000")).json())
    .sessions;
  for (const session of sessions) await request.delete(`/api/practice/sessions/${session.id}`);
  const goals = (await (await request.get(`/api/practice/goals?today=${today}`)).json()).goals;
  for (const goal of goals) await request.delete(`/api/practice/goals/${goal.id}`);
}

test.beforeEach(async ({ request }) => {
  // The same refusal every spec in this suite carries, and the same reason:
  // the helper below destroys scores and deletes practice history, which is
  // the one thing in this application that cannot be regenerated.
  const existing = await (await request.get("/api/scores")).json();
  const foreign = existing.filter((s) => !s.missing_since && !s.path.startsWith("Uploads/"));
  expect(
    foreign,
    "refusing to run: this backend has scores in folders the suite never creates, so it is " +
      "not the throwaway instance the suite creates - and this file empties the library",
  ).toEqual([]);
  await emptyTheLibrary(request);
});

test.afterAll(async ({ request }) => {
  await emptyTheLibrary(request);
});

const headline = (page) => page.locator(".headline");
const tempoTotal = (page) => page.locator(".tempo-total");
const tempoValues = (page) => page.locator(".tempo-value");
const tempoPoints = (page) => page.locator(".tempo-point");
const sessions = (page) => page.locator(".session-list .session");

async function open(page, scoreId) {
  await page.goto(`/#/score/${scoreId}/practice`);
  await expect(page.locator(".score-progress")).toBeVisible();
}

test("a piece nobody has played says so, instead of showing a screen of noughts", async ({
  page,
  request,
}) => {
  // The empty state is a different STATEMENT from a total of zero, not a
  // prettier rendering of one - the server says which this is (`practised`)
  // so a page never has to guess it from a nought.
  const score = await upload(request, "progress-untouched.musicxml");
  await open(page, score.id);

  await expect(page.locator(".nothing-yet")).toBeVisible();
  await expect(page.locator(".nothing-yet")).toContainText("No practice logged against this piece yet");
  // Not "0 sessions", not "none in total", not an empty chart with an axis on
  // it. None of the measuring furniture is drawn at all.
  await expect(headline(page)).toHaveCount(0);
  await expect(page.locator(".history-strip")).toHaveCount(0);
  await expect(page.locator(".tempo-chart")).toHaveCount(0);
  await expect(page.locator(".session-list")).toHaveCount(0);
  // And there is somewhere to go from it, which is the only useful thing a
  // page with no data can offer.
  await expect(page.locator(".open-score")).toHaveAttribute("href", `#/score/${score.id}`);
});

test("the whole record and the window are stated separately, in words and in figures", async ({
  page,
  request,
}) => {
  const score = await upload(request, "progress-totals.musicxml");
  await practise(request, score.id, { seconds: 1500, local_date: today });
  await practise(request, score.id, { seconds: 2100, local_date: addDays(today, -2) });
  await open(page, score.id);

  // 25m + 35m. Pinned as the string a person reads, not as a number a test
  // recomputed with the same arithmetic as the thing under test.
  await expect(headline(page)).toHaveText("2 sessions, 1h in total");
  await expect(page.locator(".window-total")).toHaveText("2 days, 1h in the last 90 days");
  // "When did I last play this" has no window and is answered from the whole
  // record - the two blocks are separate on purpose.
  await expect(page.locator(".last-practised")).toContainText("Last practised");
  // Ninety columns, one per day, including the empty ones: a day with nothing
  // on it is a fact about the window and not a gap to be inferred.
  await expect(page.locator(".history-strip .history-day")).toHaveCount(90);
  await expect(
    page.locator(`.history-strip .history-day[data-day="${today}"]`),
  ).toHaveAttribute("data-seconds", "1500");
  await expect(
    page.locator(`.history-strip .history-day[data-day="${addDays(today, -1)}"]`),
  ).toHaveAttribute("data-seconds", "0");
});

test("one session at a tempo is drawn as one session and not as a trend", async ({
  page,
  request,
}) => {
  // Issue #57's own words: the view should say so rather than drawing a
  // confident trend through three points. Through one, even more so.
  const score = await upload(request, "progress-one-tempo.musicxml");
  await practise(request, score.id, {
    seconds: 1200,
    local_date: today,
    tempo_bpm: 90,
    target_tempo_bpm: 120,
  });
  await open(page, score.id);

  await expect(tempoTotal(page)).toContainText("One session is not a progression");
  await expect(tempoPoints(page)).toHaveCount(1);
  // No line at all. Not a faint one, not a flat one - joining a single point
  // to nothing is the shape a reader reads as the start of a climb.
  await expect(page.locator(".tempo-line")).toHaveCount(0);
  await expect(tempoValues(page)).toHaveText(["90"]);
});

test("several tempo points are drawn in the order they happened, with their values in the page", async ({
  page,
  request,
}) => {
  const score = await upload(request, "progress-tempo-ladder.musicxml");
  // Logged newest first, so a chart that drew them in insertion order would
  // come out backwards and this would catch it.
  await practise(request, score.id, {
    seconds: 900,
    local_date: today,
    tempo_bpm: 120,
    target_tempo_bpm: 120,
  });
  await practise(request, score.id, { seconds: 900, local_date: addDays(today, -10), tempo_bpm: 100 });
  await practise(request, score.id, { seconds: 900, local_date: addDays(today, -20), tempo_bpm: 80 });
  await open(page, score.id);

  await expect(tempoTotal(page)).toHaveText("3 sessions with a tempo");
  // Oldest first, left to right, and every value present as TEXT - the whole
  // point of this section on a music stand.
  await expect(tempoValues(page)).toHaveText(["80", "100", "120"]);
  await expect(page.locator(".tempo-line")).toHaveCount(1);
  // The target is named beside its line, so the dashed rule is not a mystery.
  await expect(page.locator(".tempo-target")).toHaveText("Working towards 120 bpm");
  await expect(page.locator(".target-label")).toContainText("120");
  // Reaching a target is marked ADDITIVELY - something appears on the one that
  // did, rather than something being marked on the two that did not.
  await expect(page.locator(".tempo-point.reached")).toHaveCount(1);
  await expect(page.locator(".tempo-point.reached")).toHaveAttribute("data-bpm", "120");
});

test("the time splits between section work and run-throughs, and the unstated stays visible", async ({
  page,
  request,
}) => {
  const score = await upload(request, "progress-modes.musicxml");
  await practise(request, score.id, { seconds: 900, local_date: today, mode: "section" });
  await practise(request, score.id, { seconds: 300, local_date: today, mode: "run_through" });
  await practise(request, score.id, { seconds: 120, local_date: today });
  await open(page, score.id);

  await expect(page.locator('.split li[data-mode="section"] .split-value')).toHaveText("15m");
  await expect(page.locator('.split li[data-mode="run_through"] .split-value')).toHaveText("5m");
  // Not dropped and not folded into either of the two - the column exists so
  // this is never guessed from whether a bar range happens to be present.
  await expect(page.locator('.split li[data-mode="unstated"] .split-label')).toHaveText(
    "Not stated",
  );
});

test("the sessions arrive with whatever was written about them", async ({ page, request }) => {
  // Half of what somebody comes back to their own history for is the note.
  const score = await upload(request, "progress-notes.musicxml");
  await practise(request, score.id, {
    seconds: 1800,
    local_date: today,
    note: "left hand shape at bar 34 still collapsing",
    from_bar: 30,
    to_bar: 38,
    tempo_bpm: 88,
    target_tempo_bpm: 120,
    mode: "section",
  });
  await open(page, score.id);

  await expect(sessions(page)).toHaveCount(1);
  await expect(page.locator(".session-note")).toHaveText(
    "left hand shape at bar 34 still collapsing",
  );
  await expect(page.locator(".session-extra")).toContainText("bars 30-38");
  await expect(page.locator(".session-extra")).toContainText("88 bpm, aiming at 120");
});

test("a goal set about this piece appears with its intent and its counts", async ({
  page,
  request,
}) => {
  const score = await upload(request, "progress-goal.musicxml");
  const goal = await request.post(`/api/practice/goals?today=${today}`, {
    data: {
      target_days: 3,
      scope: "score",
      score_id: score.id,
      intent: "the awkward middle section",
    },
  });
  expect(goal.ok(), await goal.text()).toBe(true);
  await practise(request, score.id, { seconds: 1200, local_date: today });
  await open(page, score.id);

  const shown = page.locator(".goal");
  await expect(shown).toHaveCount(1);
  await expect(shown.locator(".goal-intent")).toHaveText("the awkward middle section");
  // The count and nothing concluded from it: one of three, with no percentage,
  // no grade and no "missing two".
  await expect(shown.locator(".statement-text")).toHaveText("1 of 3 planned days");
});

test("this view is reachable from the score itself", async ({ page, request }) => {
  // Issue #57 asks for both: reachable from the score, and a place of its own.
  // The place of its own is the URL every other test here opens directly; this
  // is the other half, and it is the half a person actually finds.
  const score = await upload(request, "progress-from-viewer.musicxml");
  await practise(request, score.id, { seconds: 600, local_date: today });

  await page.goto(`/#/score/${score.id}`);
  const link = page.locator(".history-link");
  await expect(link).toBeVisible();
  await link.click();
  // Waited on through the page, not read back out of band (#110): the heading
  // cannot be there until the route changed and the endpoint answered.
  await expect(page.locator(".score-progress")).toBeVisible();
  await expect(headline(page)).toHaveText("1 session, 10m in total");
});

test("a piece in the trash keeps every hour and stops being a way into the library", async ({
  page,
  request,
}) => {
  // Issue #56's policy, on the page that is entirely about one piece: the
  // hours were spent, so they are still counted and still shown - and the
  // piece is not in the library, so nothing here offers a way into it.
  const score = await upload(request, "progress-deleted.musicxml");
  await practise(request, score.id, { seconds: 2700, local_date: today, tempo_bpm: 90 });

  await open(page, score.id);
  await expect(headline(page)).toHaveText("1 session, 45m in total");
  await expect(page.locator(`a[href="#/score/${score.id}"]`)).toHaveCount(1);

  const deleted = await request.delete(`/api/scores/${score.id}`);
  expect(deleted.ok(), await deleted.text()).toBe(true);

  await page.reload();
  await expect(page.locator(".score-progress")).toBeVisible();
  await expect(page.locator(".deleted-mark")).toHaveText("deleted");
  // Still counted, to the minute.
  await expect(headline(page)).toHaveText("1 session, 45m in total");
  await expect(sessions(page)).toHaveCount(1);
  await expect(tempoPoints(page)).toHaveCount(1);
  // And no route into a score the library no longer holds.
  await expect(page.locator(`a[href="#/score/${score.id}"]`)).toHaveCount(0);
  await expect(page.locator(".open-score")).toHaveCount(0);
});

test("nothing on this page is styled as an error", async ({ page, request }) => {
  // The same rule Practice.svelte carries and the same reason: a target not
  // reached is not a fault, and a page about one piece is where colouring one
  // would be most tempting. Checked against the colour this application
  // actually uses for a real error rather than against a name in a stylesheet.
  const score = await upload(request, "progress-no-danger.musicxml");
  await practise(request, score.id, {
    seconds: 600,
    local_date: today,
    tempo_bpm: 80,
    target_tempo_bpm: 120,
    rating: 2,
  });
  await open(page, score.id);

  const danger = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue("--danger").trim(),
  );
  expect(danger, "--danger is expected to be defined, or this test proves nothing").toBeTruthy();
  // Through a probe, like practice.spec.js's version of this check: the
  // variable holds a hex and every computed colour comes back as rgb(), so
  // comparing the two directly is a test that can never go red.
  const dangerRgb = await page.evaluate((hex) => {
    const probe = document.createElement("span");
    probe.style.color = hex;
    document.body.appendChild(probe);
    const value = getComputedStyle(probe).color;
    probe.remove();
    return value;
  }, danger);
  expect(dangerRgb).toMatch(/^rgb/);

  const colours = await page.evaluate(() =>
    [...document.querySelectorAll(".score-progress *")].map((el) => {
      const style = getComputedStyle(el);
      return [style.color, style.backgroundColor, style.borderColor, style.fill].join(" ");
    }),
  );
  expect(colours.length, "nothing was inspected, so nothing was proved").toBeGreaterThan(20);
  // A session that did not reach its target, and a rating of 2, are both on
  // this page - and neither is drawn in the error colour.
  const dangerous = colours.filter((c) => c.includes(dangerRgb));
  expect(dangerous, "something on this page is drawn in the error colour").toEqual([]);
});
