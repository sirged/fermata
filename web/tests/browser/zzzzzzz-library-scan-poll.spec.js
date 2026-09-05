// The library page notices a scan it did not start, survives a status
// request that fails, and shows and polls that scan the way it needs to
// while it runs (issue #250).
//
// WHAT THE FIRST TWO COVER THAT NOTHING ELSE DOES. Every other test of the
// scan status in this suite drives it from the page's own "Scan library"
// button, which is the one path where the page already knew a scan was
// running because it started it. The two shapes #223 fixed in the
// background-batch poll were still present in this one:
//
//   1. the poll started only if a scan happened to be running at the instant
//      the page mounted, so a scan begun from another tab, another client, or
//      by the automatic pass an upload starts was never noticed at all;
//   2. one failed status request ended the loop for the rest of the page's
//      life, silently.
//
// Both are invisible to a server test - the scanner behaves identically either
// way - and invisible to a spec that clicks the button. They need a page that
// is already open while something else moves.
//
// WHAT "NOTICES" IS ASSERTED AS. A scan of the throwaway library this suite
// creates finishes in single-digit milliseconds, so requiring the page to catch
// the button mid-"Scanning 3/4…" would be a race against the scanner rather
// than a test of the poll. What is asserted instead is the state change a
// person actually cares about and can only get from a running poll: a score
// that was not in the grid when the page was opened is in the grid afterwards,
// with no reload and no click.
//
// THE THIRD TEST is the race the first two duck: what the page shows and how
// often it asks WHILE a scan it did not start is still running. It stubs
// GET /api/scan/status instead of driving a real scan, because a real one
// will not hold still for milliseconds, and needs no file and no cleanup for
// exactly that reason.
//
// WHY THE FIRST TWO SORT LAST. They put a file in the throwaway library and
// drive a real scan over it. The cleanup below removes the row and purges the
// trash, so they leave nothing behind when they pass - but a FAILING run of
// either would, and every spec here that checks its library is empty runs
// before this point. The third test never touches the library at all, but
// stays in this file rather than a new one because it is testing the same
// poll the first two are.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, "..", "..", "test-fixtures", "notation-only.musicxml");

// One name per test: both tests put a file in the library, and a shared name
// would make the second one's "this was not here when the page opened" claim
// depend on the first one's cleanup having worked.
const NAMES = {
  noticed: "scan-poll-noticed.musicxml",
  survived: "scan-poll-survived.musicxml",
};

const libraryDir = () => {
  const dir = process.env.FERMATA_TEST_LIBRARY_DIR;
  if (!dir) throw new Error("FERMATA_TEST_LIBRARY_DIR is not set - see playwright.config.js");
  return dir;
};

const filePath = (name) => path.join(libraryDir(), "Uploads", name);
const relPath = (name) => `Uploads/${name}`;

async function scores(request) {
  return (await request.get("/api/scores")).json();
}

/** Put a score file in the library WITHOUT going through the upload endpoint.
 *
 * Uploading would start a scan of its own, which is the one thing these tests
 * must not have happen: what is being tested is a scan this page did not start
 * arriving while the page is open, at a moment the test chooses.
 */
function placeFile(name) {
  fs.mkdirSync(path.dirname(filePath(name)), { recursive: true });
  // Distinct bytes per name, or the scanner's content-hash relink correctly
  // treats this as a rename of some other score rather than a new one.
  fs.writeFileSync(
    filePath(name),
    Buffer.concat([fs.readFileSync(FIXTURE), Buffer.from(`<!-- ${name} -->\n`)]),
  );
}

async function scanSettled(request) {
  const deadline = Date.now() + 30_000;
  for (;;) {
    const status = await (await request.get("/api/scan/status")).json();
    if (!status.scanning) return status;
    if (Date.now() > deadline) throw new Error("a scan never finished");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

/** Start a scan THIS call started, out of band, and wait for it to land. */
async function scanAndWait(request) {
  const deadline = Date.now() + 30_000;
  for (;;) {
    await scanSettled(request);
    const res = await (await request.post("/api/scan")).json();
    if (res.started) break;
    if (Date.now() > deadline) throw new Error("no scan this call started ever began");
  }
  return scanSettled(request);
}

/** Open the library and wait for BOTH of the reads that make what follows
 *  meaningful: the first scan-status read, which is the baseline the poll
 *  compares later reads against, and the first score list, which is the grid
 *  these tests then assert changed. Without the second one the grid can be
 *  drawn for the first time AFTER the scan below has already landed, and the
 *  new score is on the page without any poll having noticed anything. */
async function openLibrary(page) {
  const firstStatus = page.waitForResponse((r) => r.url().includes("/api/scan/status"));
  const firstScores = page.waitForResponse((r) => r.url().includes("/api/scores"));
  await page.goto("/#/");
  await Promise.all([firstStatus, firstScores]);
}

test.beforeEach(async ({ request }) => {
  // The same tolerant refusal every spec that shares Uploads/ makes: rows this
  // suite left under Uploads/, and rows already marked missing, are expected;
  // anything else means this is not the throwaway instance.
  const existing = await scores(request);
  const foreign = existing.filter((s) => !s.missing_since && !s.path.startsWith("Uploads/"));
  expect(
    foreign,
    "refusing to run: this backend has scores in its library that the suite did not put " +
      "there, so it is not the throwaway instance the suite creates",
  ).toEqual([]);
  for (const name of Object.values(NAMES)) {
    if (fs.existsSync(filePath(name))) fs.rmSync(filePath(name));
  }
});

test.afterEach(async ({ request }) => {
  // Leaves nothing behind: the row goes to the trash and the trash is purged,
  // which also takes the file with it.
  const purge = [];
  for (const name of Object.values(NAMES)) {
    const row = (await scores(request)).find((s) => s.path === relPath(name));
    if (row) {
      await request.delete(`/api/scores/${row.id}`);
      purge.push(row.id);
    }
    if (fs.existsSync(filePath(name))) fs.rmSync(filePath(name));
  }
  // By id, so this destroys exactly the rows this file created and nothing
  // another spec left on the trash.
  for (const id of purge) await request.delete(`/api/trash/${id}`);
});

test("a scan started while the page is open is noticed without a reload", async ({
  page,
  request,
}) => {
  await openLibrary(page);

  // Something else scans - another tab, another client, or the automatic pass
  // an upload starts. This page is not told and does not ask again on its own
  // account; only the poll can find out.
  placeFile(NAMES.noticed);
  const status = await scanAndWait(request);
  expect(status.refused, JSON.stringify(status)).toBe(false);
  const added = (await scores(request)).find((s) => s.path === relPath(NAMES.noticed));
  expect(added, "the scan did not add the file this test put in the library").toBeTruthy();

  const card = page.locator(`.card[href="#/score/${added.id}"]`);
  // Not there yet, read once rather than waited for: the scan has only just
  // finished and the page's next idle check is up to a full interval away, so
  // this is the state the bug leaves on screen for ever. Asserting it is what
  // makes the assertion below evidence of the poll rather than of the grid
  // having been drawn late.
  expect(await card.count(), "the card was already on the page before the scan").toBe(0);

  // One idle interval (15s) plus margin, and no reload anywhere in this test.
  await expect(card).toBeVisible({ timeout: 45_000 });
});

test("one failed status request does not end the poll", async ({ page, request }) => {
  let statusRequests = 0;
  let sawTheFailure;
  const failed = new Promise((resolve) => (sawTheFailure = resolve));
  // Exactly one request is killed - the second, so the page's baseline read has
  // already landed - and every other one is served normally. api.js turns a
  // dropped connection into a rejected promise, which is what a self-hosted
  // server restarting under an open page produces.
  await page.route("**/api/scan/status", async (route) => {
    statusRequests += 1;
    if (statusRequests === 2) {
      await route.abort("failed");
      sawTheFailure();
      return;
    }
    await route.continue();
  });

  await openLibrary(page);
  await failed;

  // The poll is now either dead or alive, and the only way to tell is to give
  // it something to find.
  placeFile(NAMES.survived);
  const status = await scanAndWait(request);
  expect(status.refused, JSON.stringify(status)).toBe(false);
  const added = (await scores(request)).find((s) => s.path === relPath(NAMES.survived));
  expect(added, "the scan did not add the file this test put in the library").toBeTruthy();

  await expect(page.locator(`.card[href="#/score/${added.id}"]`)).toBeVisible({
    timeout: 45_000,
  });
  // ...and it got there by asking again after the failure, rather than by some
  // other request on the page happening to redraw the grid.
  expect(statusRequests, "the poll made no request after the one that failed").toBeGreaterThan(2);
});

// The two other tests in this file prove the poll NOTICES a scan it did not
// start, through the one thing a person can see days later: a score that
// arrived without a reload. This one is about what the poll shows and how
// often it asks WHILE that scan is running, and needs the scan to still be
// in progress at the moment it looks - which the real scanner (single-digit
// milliseconds against a throwaway library) cannot hold still for. So this
// one stubs GET /api/scan/status directly and never starts a real scan at
// all: everything it asserts is a fact about Library.svelte's poll, not
// about the scanner.
test("the scan button reflects a scan the page did not start, at the fast poll cadence", async ({
  page,
}) => {
  // A plausible mid-scan body - see api_models.ScanStatusOut for the fields
  // a real response always carries. `scanning: true` here is the whole
  // point: this page never called POST /api/scan, so the only way it can
  // know a scan is running is by being told so on a poll it did not expect
  // to matter.
  const scanningStatus = {
    scanning: true,
    total: 4,
    processed: 2,
    added: 0,
    updated: 0,
    missing: 0,
    restored: 0,
    unmatched_moves: 0,
    refused: false,
    refused_reason: null,
    unmatched_paths: [],
    unmatched_count: 0,
    acknowledge_token: null,
    errors: 0,
    last_error: null,
    started_at: Date.now() / 1000,
    finished_at: null,
    transcribe_batch_started: null,
    transcribe_batch_note: null,
  };
  let statusRequests = 0;
  await page.route("**/api/scan/status", async (route) => {
    statusRequests += 1;
    await route.fulfill({ json: scanningStatus });
  });

  await page.goto("/#/");
  await page.waitForResponse((r) => r.url().includes("/api/scan/status"));

  // (a) The button shows the scanning state, though this page never started
  // one - Library.svelte cannot and must not tell "a scan I started" from "a
  // scan somebody else started" apart (see its own comment above the poll),
  // so the text is the same one a self-started scan would show.
  await expect(page.getByRole("button", { name: /^Scan library$|^Scanning \d+\/\d+…$/ })).toHaveText(
    `Scanning ${scanningStatus.processed}/${scanningStatus.total}…`,
  );

  // (b) At least three requests inside five seconds - only reachable at the
  // FAST (1.5s) cadence the poll is supposed to use while `scanning` reads
  // true. The idle (15s) cadence would produce at most the one mount-time
  // request in that window, so this also catches a poll that noticed the
  // scan once and then fell back to idle spacing instead of staying fast
  // for as long as it is told a scan is running.
  await expect
    .poll(() => statusRequests, {
      timeout: 5000,
      message: "expected at least 3 status requests within 5s at the fast cadence",
    })
    .toBeGreaterThanOrEqual(3);
});
