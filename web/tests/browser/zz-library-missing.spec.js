// What a person actually SEES when Fermata cannot find their files.
//
// WHY THIS EXISTS AS A BROWSER TEST. #95's fix was, for a while, entirely
// invisible: the scan status carried `refused`, `refused_reason`, `missing` and
// `restored`, the score payload carried `missing_since`, and the interface
// rendered none of it. Verified live at the time with 296 of 297 files gone, the
// page showed a healthy scan, a full library, and a collection count that said
// 297 for a folder holding one file. Somebody pressing "Scan library" after a
// refusal watched the button return to normal and got nothing else. A guard
// nobody can see is not a guard, and the deployment guide was meanwhile telling
// people to look for scores "marked as missing" in a view that had no such
// thing.
//
// So these are the assertions that the two states this change introduces reach
// the screen at all:
//
//   1. a score whose file is gone is LISTED, flagged, and still opens;
//   2. a refused reconciliation says so, says why, and offers the one control
//      that can get past it - because a refusal with no way out is worse than
//      the loss it prevents.
//
// WHY IT IS NAMED TO SORT LAST, like viewer-practice.spec.js. It puts files in
// the throwaway library, and a score whose file is deleted now leaves its ROW
// behind on purpose - so this spec cannot return the library to empty however
// carefully it cleans up, and every other spec here refuses to run against a
// backend that has scores in it. Sorting last is what keeps that true for them.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, "..", "..", "test-fixtures", "notation-only.musicxml");

// Two files, because the two states need different evidence. Marking a row
// missing needs at least one file still present - a library that reads as
// EMPTY is refused outright, which is the other half of this spec.
const FIRST = "missing-flag-fixture-one.musicxml";
const SECOND = "missing-flag-fixture-two.musicxml";
const OWN = [FIRST, SECOND].map((name) => `Uploads/${name}`);

const libraryDir = () => {
  const dir = process.env.FERMATA_TEST_LIBRARY_DIR;
  if (!dir) throw new Error("FERMATA_TEST_LIBRARY_DIR is not set - see playwright.config.js");
  return dir;
};

const filePath = (name) => path.join(libraryDir(), "Uploads", name);

async function scores(request) {
  return (await request.get("/api/scores")).json();
}

const SCAN_DEADLINE_MS = 30_000;

/** Wait until no scan at all is running, then hand back the idle status. */
async function scanSettled(request, deadline) {
  for (;;) {
    const status = await (await request.get("/api/scan/status")).json();
    if (!status.scanning) return status;
    if (Date.now() > deadline) throw new Error("a scan never finished");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

/**
 * Run a scan THIS CALL started, and hand back its final status.
 *
 * "This call started" is the whole point, and used to be assumed rather than
 * checked. POST /api/scan does not queue: scanner.start_scan() declines
 * outright when a scan is already running, answering `{"started": false}` -
 * and every upload starts one of its own (see api.upload's own
 * scanner.start_scan()). Posting and then waiting only for `scanning` to go
 * false therefore waits out WHOEVER'S scan was in flight and reads its
 * findings as though they were this call's - findings from a directory
 * listing taken before this test's files were in place.
 *
 * Both of this file's CI failures were that, and the traces say so outright.
 * The refusal test uploaded two files 68ms apart, the first upload's own scan
 * was still running when the second file landed, its POST /api/scan was
 * declined, and the scan it waited out had already listed the library without
 * the second file - so the freshly uploaded score came back with
 * `missing_since` still set. The restored-count test hit the same decline one
 * scan earlier, which left a `restored` for the NEXT scan to report:
 * `{"missing":1,"restored":1}` where the test had just asserted there was
 * nothing yet to report, and the page duly showed the "found again" notice
 * the next assertion required to be absent.
 *
 * This is issue #110's pattern - reading state out of band without waiting
 * for the thing that produces it - so the fix is the same shape: keep every
 * assertion, and make the read a barrier. Nothing here retries an assertion;
 * it retries until the scan being asserted about is genuinely this one.
 */
async function scanAndWait(request) {
  const deadline = Date.now() + SCAN_DEADLINE_MS;
  for (;;) {
    // Let whatever is already running finish, so the post below is not
    // declined for it. Something else can still slip in between this and the
    // post, which is why `started` is checked rather than assumed.
    await scanSettled(request, deadline);
    const res = await (await request.post("/api/scan")).json();
    if (res.started) break;
    if (Date.now() > deadline) throw new Error("no scan this call started ever began");
  }
  return scanSettled(request, deadline);
}

async function upload(request, name) {
  // The file has to be DISTINCT per name, or the content-hash relink correctly
  // treats the second upload as a rename of the first and this spec ends up with
  // one row instead of two.
  const body = Buffer.concat([
    fs.readFileSync(FIXTURE),
    Buffer.from(`<!-- ${name} -->\n`),
  ]);
  const res = await request.post("/api/upload?folder=Uploads", {
    multipart: { file: { name, mimeType: "application/xml", buffer: body } },
  });
  expect(res.ok(), await res.text()).toBe(true);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const found = (await scores(request)).find((s) => s.path === `Uploads/${name}`);
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`the uploaded score ${name} never appeared`);
}

test.beforeEach(async ({ request }) => {
  // The same refusal every other spec makes: nothing whose file is actually
  // present may be here that this spec (or the practice spec before it) did not
  // put there. Rows already marked missing are inert leftovers by design.
  const existing = await scores(request);
  const foreign = existing.filter(
    (s) => !s.missing_since && !OWN.includes(s.path) && !s.path.startsWith("Uploads/"),
  );
  expect(
    foreign,
    "refusing to run: this backend has scores in its library that the suite did not put " +
      "there, so it is not the throwaway instance the suite creates",
  ).toEqual([]);
  for (const name of [FIRST, SECOND]) {
    if (fs.existsSync(filePath(name))) fs.rmSync(filePath(name));
  }
});

test.afterAll(() => {
  for (const name of [FIRST, SECOND]) {
    if (fs.existsSync(filePath(name))) fs.rmSync(filePath(name));
  }
});

test("a score whose file has gone is shown as missing rather than vanishing", async ({
  page,
  request,
}) => {
  const first = await upload(request, FIRST);
  await upload(request, SECOND);

  // While the file is there, nothing is flagged.
  await page.goto("/#/");
  const card = page.locator(`.card[href="#/score/${first.id}"]`);
  await expect(card).toBeVisible();
  await expect(card.locator(".missing-flag")).toHaveCount(0);

  // One of the two goes. The other stays, so this is an ordinary reconciliation
  // rather than the empty-library case below.
  fs.rmSync(filePath(FIRST));
  const status = await scanAndWait(request);
  expect(status.refused, JSON.stringify(status)).toBe(false);
  expect(status.missing).toBe(1);

  // The row is still there, still listed, and now says what happened.
  const row = (await scores(request)).find((s) => s.id === first.id);
  expect(row, "the score row was deleted rather than marked").toBeTruthy();
  expect(row.missing_since).toBeTruthy();

  await page.reload();
  const flagged = page.locator(`.card[href="#/score/${first.id}"]`);
  await expect(flagged).toBeVisible();
  await expect(flagged.locator(".missing-flag")).toHaveText("file missing");
  // And the sidebar says how much of the collection is not there, instead of
  // counting a folder that has partly gone as though it were whole.
  await expect(page.locator(".side-item", { hasText: "Uploads" }).first()).toContainText(
    "missing",
  );

  // Putting it back clears the flag by itself - no action, no confirmation.
  await upload(request, FIRST);
  await page.reload();
  const restored = page.locator(`.card[href="#/score/${first.id}"]`);
  await expect(restored.locator(".missing-flag")).toHaveCount(0);
});

test("a refused scan says so on the page, and can be confirmed", async ({ page, request }) => {
  await upload(request, FIRST);
  await upload(request, SECOND);
  // Settled explicitly before anything is deleted. An upload triggers its own
  // background scan, and the helper above returns as soon as the ROW exists -
  // which it may already have done, marked missing, from an earlier test. Both
  // rows have to be believed present for the refusal below to be about both of
  // them.
  await scanAndWait(request);
  const startingPoint = await scores(request);
  for (const name of OWN) {
    const row = startingPoint.find((s) => s.path === name);
    expect(row, `${name} is not in the library`).toBeTruthy();
    expect(row.missing_since, `${name} should be present before this test starts`).toBeFalsy();
  }

  // Both files go: a library that reads as empty while scores are on record is
  // refused categorically, because that is what an unmounted drive looks like.
  fs.rmSync(filePath(FIRST));
  fs.rmSync(filePath(SECOND));
  const refused = await scanAndWait(request);
  expect(refused.refused, JSON.stringify(refused)).toBe(true);
  expect(refused.missing).toBe(0);
  expect(refused.acknowledge_token).toBeTruthy();

  await page.goto("/#/");
  const alert = page.locator(".alert");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("Fermata did not update your library");
  await expect(alert).toContainText("no readable score files at all");
  await expect(alert).toContainText("Confirming never deletes anything");
  // It lists what it could not find, so a person can recognise which part of
  // their library it is talking about.
  await alert.locator("details summary").click();
  await expect(alert.locator("details li")).toHaveCount(refused.unmatched_count);

  // The way out. Without it the same files are unmatched on every subsequent
  // pass, so the refusal would repeat for ever with nothing a person could do.
  await alert.getByRole("button", { name: /I meant to do that/i }).click();
  await expect(page.locator(".alert")).toHaveCount(0, { timeout: 30_000 });

  // Polled until the rescan the acknowledgement started has actually LANDED,
  // rather than read once. The alert disappearing is a client-side signal - the
  // page refetched and saw the refusal gone - while the reconciliation that
  // marks both rows happens in the scan behind it, and this read goes out of
  // band through the request context rather than through the page, so it is not
  // ordered against that scan finishing. Read once, it overtook the scan and
  // reported `missing: 0`: a real failure, seen once in a full-suite run and
  // never in isolation, which is the signature of exactly this race.
  //
  // The same trap instruments.spec.js already names - "those assertions are
  // barriers, not only checks" - and the same one this file's own comment below
  // records having been caught by on a slower runner. Every assertion is kept;
  // only the reading of them becomes a barrier.
  await expect(async () => {
    const after = await (await request.get("/api/scan/status")).json();
    expect(after.scanning, JSON.stringify(after)).toBe(false);
    expect(after.refused, JSON.stringify(after)).toBe(false);
    expect(after.missing, JSON.stringify(after)).toBe(2);
  }).toPass({ timeout: 30_000 });

  // Marked, never deleted. Ordered after the barrier above, so this reads the
  // library the reconciliation actually left behind.
  const listed = await scores(request);
  for (const name of OWN) {
    const row = listed.find((s) => s.path === name);
    expect(row, `${name} was deleted rather than marked`).toBeTruthy();
    expect(row.missing_since).toBeTruthy();
  }
});

// LAST IN THIS FILE, and that is not tidiness. Sitting between the two tests
// above it, this one changed what scans had happened just before the refusal
// test ran, and that test began failing in CI on an assertion about the
// acknowledge token - green locally, red on a slower runner. The file's other
// tests are older than this one and were passing; the ordering that keeps them
// seeing exactly the state they saw before is the ordering they had. Anything
// added here belongs after them, for the same reason the whole file is named to
// sort last.
test("a scan that found a missing file again says so, rather than only clearing the flag", async ({
  page,
  request,
}) => {
  // Issue #103. The scanner counts rows whose file turned up again AT THE PATH
  // IT LEFT FROM - deliberately not a content-hash relink, which is a guess
  // about identity - specifically so the count can stand as evidence that a
  // remount really did recover. It stood as evidence to nobody: `restored` was
  // on /api/scan/status and nothing in the interface read it, so somebody who
  // put a drive back saw flags quietly disappear and no statement that
  // anything had been recovered.
  const first = await upload(request, FIRST);
  await upload(request, SECOND);
  await scanAndWait(request);
  const bytes = fs.readFileSync(filePath(FIRST));

  // Gone, and marked - the other one stays, so this is an ordinary
  // reconciliation rather than the refused empty-library case.
  fs.rmSync(filePath(FIRST));
  const marked = await scanAndWait(request);
  expect(marked.refused, JSON.stringify(marked)).toBe(false);
  expect(marked.missing).toBe(1);
  // Nothing to report yet, so nothing is reported. Asserted before the notice
  // appears, so the notice below is known to be caused by the recovery rather
  // than being permanently on the page.
  await page.goto("/#/");
  await expect(page.locator(`.card[href="#/score/${first.id}"] .missing-flag`)).toBeVisible();
  await expect(page.locator(".scan-note")).toHaveCount(0);

  // Back at the path it left from.
  fs.writeFileSync(filePath(FIRST), bytes);
  const recovered = await scanAndWait(request);
  expect(recovered.restored, JSON.stringify(recovered)).toBe(1);

  await page.reload();
  const note = page.locator(".scan-note");
  await expect(note).toBeVisible();
  await expect(note).toContainText("1 score found again");
  await expect(note).toContainText("at the path it went missing from");
  // ...and the flag really has gone, so the statement and the library agree.
  await expect(page.locator(`.card[href="#/score/${first.id}"] .missing-flag`)).toHaveCount(0);
});
