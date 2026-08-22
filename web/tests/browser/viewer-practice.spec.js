// The practice timer and the panel that asks what the session was, against a
// real score in a real library.
//
// WHY THIS IS ITS OWN FILE. Every other spec here runs against an EMPTY
// library, and instruments.spec.js and practice.spec.js both refuse to run
// otherwise - which is the guard that makes it safe for them to delete
// instruments and practice history. Reaching this panel needs a score, so this
// spec puts a file in the throwaway library and takes it out again afterwards.
// It is named to sort LAST, so that even a cleanup that fails cannot leave a
// score behind for another spec to trip over.
//
// What it covers that a unit test cannot: that stopping the timer stores a
// session at all (the write goes through fetch from a real page), that the
// panel patches the session that was just logged rather than some other one,
// and that clearing a field means unset rather than zero - all of which live in
// the seam between the component, the endpoint and the database.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { localDay } from "../../src/lib/practice.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, "..", "..", "test-fixtures", "notation-only.musicxml");
// A MusicXML file rather than a PDF: the scanner reads it without needing a
// thumbnailer, and the viewer chrome under test - the timer button and the
// panel - is the same for either.
const SCORE_NAME = "practice-timer-fixture.musicxml";
// A second score, so the route can swap `id` on the SAME Viewer instance -
// which is the navigation the detail panel had to survive being carried
// across, and the only one where it could be saved onto the wrong session.
const OTHER_NAME = "practice-timer-fixture-two.musicxml";

const libraryDir = () => {
  const dir = process.env.FERMATA_TEST_LIBRARY_DIR;
  if (!dir) throw new Error("FERMATA_TEST_LIBRARY_DIR is not set - see playwright.config.js");
  return dir;
};

const panel = (page) => page.locator(".session-detail");
const timer = (page) => page.locator("button.timer");

async function waitForScore(request, name) {
  // The scan runs in a background thread, so the row appears a moment after the
  // upload returns.
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const scores = await (await request.get("/api/scores")).json();
    const found = scores.find((s) => s.path.endsWith(name));
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`the uploaded score ${name} never appeared in the library`);
}

async function upload(request, name) {
  const res = await request.post("/api/upload?folder=Uploads", {
    multipart: {
      file: { name, mimeType: "application/xml", buffer: fs.readFileSync(FIXTURE) },
    },
  });
  expect(res.ok(), await res.text()).toBe(true);
  return waitForScore(request, name);
}

// This spec's own fixture paths, which the guard below has to be able to tell
// apart from somebody's real sheet music.
const OWN_PATHS = [SCORE_NAME, OTHER_NAME].map((name) => `Uploads/${name}`);

// WHY THE FILES ARE NOT TAKEN BACK OUT AFTER EVERY TEST ANY MORE.
//
// This used to delete the fixture files and scan, and wait for /api/scores to
// come back empty. Neither half of that works now, both for the same reason
// (#95), and both deliberately:
//
//   - a score whose file is gone is MARKED missing, not deleted, so that the
//     practice history, tags and hand-corrected transcriptions hanging off it
//     survive a drive that did not come back. The row stays.
//   - and a scan that finds NO readable files while the database holds scores
//     refuses to reconcile at all, because that is what an unmounted library
//     looks like. Emptying a two-score library is exactly the shape this guard
//     is built to disbelieve.
//
// So the files stay put between tests in this file, and the rows are reused:
// beforeEach uploads to the same path, and a scan finds the existing row there.
// Only ever two rows exist here, one per fixture name. The files are removed
// once at the end, for the filesystem's sake rather than the database's - and
// this spec is named to sort LAST, so nothing runs after it either way.
async function removeFixtureFiles() {
  for (const name of [SCORE_NAME, OTHER_NAME]) {
    const target = path.join(libraryDir(), "Uploads", name);
    if (fs.existsSync(target)) fs.rmSync(target);
  }
}

let score;

test.beforeEach(async ({ request }) => {
  // The same refusal every other spec makes, and for the same reason: this one
  // deletes practice history too. Narrowed to scores that are NOT this spec's
  // own fixtures, which are expected to still be on record from an earlier test
  // in this file. Anything else means a real library, and this spec must not run
  // against one.
  const existing = await (await request.get("/api/scores")).json();
  expect(
    existing.filter((s) => !OWN_PATHS.includes(s.path)),
    "refusing to run: this backend has scores in its library that this spec did not put " +
      "there, so it is not the throwaway instance the suite creates",
  ).toEqual([]);

  // Earlier specs clear practice history on the way IN rather than out, so the
  // last one leaves its rows behind. Cleared here too, because the assertions
  // below count sessions in the whole record.
  const stale = (await (await request.get("/api/practice/sessions?limit=1000")).json()).sessions;
  for (const session of stale) await request.delete(`/api/practice/sessions/${session.id}`);

  score = await upload(request, SCORE_NAME);
});

test.afterAll(async () => {
  await removeFixtureFiles();
});

test.afterEach(async ({ request }) => {
  const sessions = (await (await request.get("/api/practice/sessions?limit=1000")).json())
    .sessions;
  for (const session of sessions) await request.delete(`/api/practice/sessions/${session.id}`);
});

/** Run the timer for long enough to be stored. The component refuses anything
 * under ten seconds, on purpose - opening a score for a moment is not
 * practice - so this is a real wait rather than a mock. */
async function practiseFor(page, seconds = 12) {
  await timer(page).click();
  await expect(timer(page)).toHaveClass(/on/);
  await page.waitForTimeout(seconds * 1000);
  await timer(page).click();
}

test("stopping the timer stores a session, and the panel adds detail to that session", async ({
  page,
  request,
}) => {
  await page.goto(`/#/score/${score.id}`);
  await expect(timer(page)).toBeVisible();

  await practiseFor(page);
  await expect(panel(page)).toBeVisible();
  // The length and the day it was filed under, stated back.
  await expect(panel(page)).toContainText(localDay());

  const logged = (await (await request.get("/api/practice/sessions")).json()).sessions;
  expect(logged).toHaveLength(1);
  expect(logged[0].seconds).toBeGreaterThanOrEqual(10);
  expect(logged[0].score_id).toBe(score.id);
  expect(logged[0].local_date).toBe(localDay());
  expect(logged[0].local_date_source).toBe("recorded");
  // Nothing has been said about it yet.
  expect(logged[0].rating).toBeNull();

  await page.locator('[data-rating="4"]').click();
  await page.locator(".detail-mode").selectOption("section");
  await page.locator(".detail-bar").first().fill("17");
  await page.locator(".detail-bar").nth(1).fill("32");
  await page.locator(".detail-tempo").fill("76");
  await page.locator(".detail-target-tempo").fill("120");
  await page.locator(".detail-note").fill("left hand still behind");
  await page.locator(".save-detail").click();
  await expect(panel(page)).toHaveCount(0);

  const stored = (await (await request.get("/api/practice/sessions")).json()).sessions;
  expect(stored).toHaveLength(1);
  expect(stored[0].id).toBe(logged[0].id);
  expect(stored[0].rating).toBe(4);
  expect(stored[0].mode).toBe("section");
  expect(stored[0].from_bar).toBe(17);
  expect(stored[0].to_bar).toBe(32);
  expect(stored[0].tempo_bpm).toBe(76);
  expect(stored[0].target_tempo_bpm).toBe(120);
  expect(stored[0].note).toBe("left hand still behind");
  // Derived from the two tempos rather than stored, and false because 76 is
  // short of 120 - the one place the interface could tell somebody they are
  // further on than they are.
  expect(stored[0].reached_target).toBe(false);
});

test("a field left empty is unset rather than zero", async ({ page, request }) => {
  // Number(null) is 0, so an emptied number input used to send 0 - which the
  // server refuses, failing the whole save with a message about a field the
  // person had just cleared.
  await page.goto(`/#/score/${score.id}`);
  await practiseFor(page);
  await expect(panel(page)).toBeVisible();

  await page.locator(".detail-tempo").fill("76");
  await page.locator(".detail-tempo").fill("");
  await page.locator(".detail-note").fill("no tempo worth naming");
  await page.locator(".save-detail").click();

  await expect(panel(page)).toHaveCount(0);
  await expect(page.locator(".detail-hint")).toHaveCount(0);
  const stored = (await (await request.get("/api/practice/sessions")).json()).sessions;
  expect(stored[0].tempo_bpm).toBeNull();
  expect(stored[0].note).toBe("no tempo worth naming");
});

test("closing the panel keeps the session it was about", async ({ page, request }) => {
  await page.goto(`/#/score/${score.id}`);
  await practiseFor(page);
  await expect(panel(page)).toBeVisible();
  await page.locator(".close-detail").click();
  await expect(panel(page)).toHaveCount(0);

  const stored = (await (await request.get("/api/practice/sessions")).json()).sessions;
  expect(stored).toHaveLength(1);
  expect(stored[0].rating).toBeNull();
});

test("moving to another score closes the panel rather than aiming it there", async ({
  page,
  request,
}) => {
  // A data-integrity bug, not a cosmetic one. The route swaps `id` on the same
  // Viewer instance rather than remounting it, so the panel stayed on screen
  // across the change - and a rating typed into it then went to the PREVIOUS
  // score's session, recording an opinion about practice that did not happen.
  //
  // Navigated by changing the hash from inside the page, not with goto(): a
  // full document load would unmount the component and destroy the panel
  // whatever the component did, which is a test that passes for the wrong
  // reason.
  const other = await upload(request, OTHER_NAME);

  await page.goto(`/#/score/${score.id}`);
  await practiseFor(page);
  await expect(panel(page)).toBeVisible();
  const logged = (await (await request.get("/api/practice/sessions")).json()).sessions;
  expect(logged).toHaveLength(1);

  await page.evaluate((id) => {
    window.location.hash = `#/score/${id}`;
  }, other.id);
  await expect(page.locator("header .title")).toContainText("Notation-only example");
  await expect(panel(page)).toHaveCount(0);

  const after = (await (await request.get("/api/practice/sessions")).json()).sessions;
  expect(after).toHaveLength(1);
  expect(after[0].id).toBe(logged[0].id);
  expect(after[0].rating).toBeNull();
});

test("a session that runs past midnight is filed on the day it started", async ({
  page,
  request,
}) => {
  // The single field this whole feature counts, and the day it picks has to be
  // chosen rather than incidental. Taking the day from the clock at flush time
  // filed a 23:40-to-00:20 session entirely on the following day - and at a
  // week boundary, against the next week's goal rather than the one it was
  // practised for.
  //
  // A fake clock, because the alternative is waiting until midnight. Installed
  // at a fixed local time so both the start day and the stop day are known.
  const beforeMidnight = new Date(2026, 7, 17, 23, 59, 50);
  await page.clock.install({ time: beforeMidnight });
  await page.goto(`/#/score/${score.id}`);
  await expect(timer(page)).toBeVisible();

  await timer(page).click();
  await expect(timer(page)).toHaveClass(/on/);
  // Past midnight, and past the ten-second floor.
  await page.clock.fastForward("00:30");
  await timer(page).click();
  await expect(panel(page)).toBeVisible();

  const stored = (await (await request.get("/api/practice/sessions")).json()).sessions;
  expect(stored).toHaveLength(1);
  expect(stored[0].local_date).toBe("2026-08-17");
  expect(stored[0].local_date_source).toBe("recorded");
});

test("the session reaches the library's own view of the score", async ({ page, request }) => {
  await page.goto(`/#/score/${score.id}`);
  await practiseFor(page);
  await expect(panel(page)).toBeVisible();

  // Found by id, not by position: the other fixture's row may still be on
  // record from an earlier test in this file (marked missing, file gone), and
  // both carry the same title, so the order is not something to lean on.
  const listed = await (await request.get("/api/scores")).json();
  const mine = listed.find((s) => s.id === score.id);
  expect(mine.practice_seconds).toBeGreaterThanOrEqual(10);
  // The practice DAY, not a timestamp - this is what the library's "practised
  // today" is computed from.
  expect(mine.last_practiced).toBe(localDay());

  await page.goto("/#/");
  await expect(page.locator(".card .practiced")).toHaveText("practiced today");
});
