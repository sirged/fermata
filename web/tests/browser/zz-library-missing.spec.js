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
// WHY IT IS NAMED TO SORT LATE, like viewer-practice.spec.js. It puts files in
// the throwaway library, and a score whose file is deleted now leaves its ROW
// behind on purpose - so this spec cannot return the library to empty however
// carefully it cleans up, and several specs here refuse to run against a
// backend that has scores in it. Sorting late is what keeps that true for them.
//
// WHAT THAT NAME NO LONGER CARRIES (#250). It used to be load-bearing in the
// other direction too: the refusal test below took the scanner's categorical
// refusal, which needs the library folder to read as EMPTY, so any spec sorting
// before this one that left a file behind broke it - reproducibly, and only in
// a full-suite run. That is why #233's spec is named `zzzzzz-`, to sort after
// this one. The refusal test now builds the library it needs and is refused
// whatever anybody else left lying around; see its own comment.
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

// The refusal test below needs a library of its own SIZE, not just of its own
// files - see its own comment. These are the extra ones it uses, named by a
// pattern so cleanup can find them without knowing how many a given run built.
const REFUSAL_PREFIX = "refusal-fixture-";
const refusalName = (n) => `${REFUSAL_PREFIX}${String(n).padStart(2, "0")}.musicxml`;
const isRefusalFile = (name) => name.startsWith(REFUSAL_PREFIX) && name.endsWith(".musicxml");

// scanner.LOSS_FLOOR, mirrored. Below this many scores the proportional test is
// switched off entirely (a library of three becoming a library of one is a
// Tuesday), so a test that wants the proportional refusal has to get the
// library above it first.
const LOSS_FLOOR = 10;

const libraryDir = () => {
  const dir = process.env.FERMATA_TEST_LIBRARY_DIR;
  if (!dir) throw new Error("FERMATA_TEST_LIBRARY_DIR is not set - see playwright.config.js");
  return dir;
};

const filePath = (name) => path.join(libraryDir(), "Uploads", name);

/** Every file this spec is responsible for, gone from disk. The ROWS stay, on
 *  purpose - a score whose file is deleted leaves its row behind (#95), which
 *  is the whole subject of this file. */
function removeOwnFiles() {
  const dir = path.join(libraryDir(), "Uploads");
  if (!fs.existsSync(dir)) return;
  for (const name of fs.readdirSync(dir)) {
    if (name === FIRST || name === SECOND || isRefusalFile(name)) {
      fs.rmSync(path.join(dir, name));
    }
  }
}

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
  removeOwnFiles();
});

test.afterAll(() => {
  removeOwnFiles();
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

// THIS TEST BUILDS THE LIBRARY IT NEEDS, rather than inheriting one (#250).
//
// It used to upload two files, delete both, and rely on the scanner's
// CATEGORICAL refusal - "the library folder contains no readable score files at
// all" - which needs the library folder to be genuinely empty when the scan
// walks it. That was never this test's own doing: it held only while no spec
// running earlier had left a single file behind. Any spec sorting before this
// one that leaves files in the library makes `found` non-zero, the categorical
// test cannot fire, and with a high-water mark under scanner.LOSS_FLOOR the
// proportional test is switched off too - so the scan is simply not refused and
// this test fails. Reproduced on 1527d0a with a five-file spec placed before
// it: `refused: false`, `total: 5`.
//
// #233 worked around that by naming its own spec `zzzzzz-` so it would sort
// AFTER this one, and said so in its header. That is a constraint on every
// future spec author, enforced by nothing, to protect an assumption this test
// never stated.
//
// So this test now establishes its own mark instead, through the ordinary
// upload route rather than any test-only hook: it counts what is already
// believed present, uploads enough files of its own that losing all of them
// takes the library to half or less of the high-water mark those uploads
// themselves set, and takes the PROPORTIONAL refusal. That clears whatever
// number of already-ROWED scores anybody leaves lying around, because the
// number of its own files is worked out from that count - and it clears a
// couple of unrowed-but-scannable files too (see the margin below), though
// not an unbounded pile of them, because `believed_present` only reflects a
// file once something has rowed it.
//
// The categorical refusal is not left uncovered by the move: test_scanner.py
// covers it at the function boundary and again through the API, where a library
// really can be empty.
test("a refused scan says so on the page, and can be confirmed", async ({ page, request }) => {
  // What is already here and believed present, whoever put it there. Counted
  // BEFORE this test's own uploads, because it is the number those uploads have
  // to outweigh. Rows already marked missing are not counted: the scanner does
  // not count them either (see `believed_present`), and a row whose file is
  // gone only makes the loss below larger.
  const before = (await scores(request)).filter((s) => !s.missing_since).length;
  // Enough that this test's own loss is decisive whatever `before` is:
  //   - `before * 2 + 2` rather than `before + 1`. `before` counts ROWS, not
  //     files, but the settling scan below (`scanAndWait`) walks every file
  //     under Uploads/ - including any scannable file an earlier spec left
  //     behind WITHOUT ever uploading it, so it never got a row and was never
  //     part of `before`. That scan rows it anyway, adding it to
  //     `believed_present` on the same pass this test's mark is taken from,
  //     which this test's own loss then has to outweigh even though it never
  //     touches that file. `before + 1` gave a margin of exactly half a file
  //     over that - measured failing against two such leftovers, on this
  //     branch before this fix, as `{"total":7,"missing":6,"refused":false}`.
  //     `before * 2 + 2` clears two of them with room to spare;
  //   - `LOSS_FLOOR - before` so the mark clears the floor below which the
  //     proportional test is switched off entirely;
  //   - two at minimum, because marking a row missing needs a library that does
  //     not read as empty, which is the other refusal and the other test.
  const count = Math.max(before * 2 + 2, LOSS_FLOOR - before, 2);
  const names = [FIRST, SECOND, ...Array.from({ length: count - 2 }, (_, i) => refusalName(i))];

  for (const name of names) await upload(request, name);
  // Settled explicitly before anything is deleted. An upload triggers its own
  // background scan, and the helper above returns as soon as the ROW exists -
  // which it may already have done, marked missing, from an earlier test. Every
  // row has to be believed present for the refusal below to be about all of
  // them, and this settled scan is also what writes the high-water mark this
  // test then measures its own loss against.
  await scanAndWait(request);
  const startingPoint = await scores(request);
  for (const name of names) {
    const row = startingPoint.find((s) => s.path === `Uploads/${name}`);
    expect(row, `${name} is not in the library`).toBeTruthy();
    expect(row.missing_since, `${name} should be present before this test starts`).toBeFalsy();
  }

  // All of them go at once: a scan that can account for half or less of the
  // library this install last held whole is refused, because that is what a
  // folder that stopped being readable looks like, not what pruning looks like.
  for (const name of names) fs.rmSync(filePath(name));
  const refused = await scanAndWait(request);
  expect(refused.refused, JSON.stringify(refused)).toBe(true);
  expect(refused.missing).toBe(0);
  expect(refused.acknowledge_token).toBeTruthy();
  // Every one of this test's own files is in the loss it is being refused over
  // - so the refusal is about what this test did, not about something a spec
  // before it left in the way.
  expect(refused.unmatched_count, JSON.stringify(refused)).toBeGreaterThanOrEqual(names.length);

  await page.goto("/#/");
  const alert = page.locator(".alert");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("Fermata did not update your library");
  // The reason the SERVER gave, in full, rather than a phrase from one of the
  // two it can give. Which one fires depends on whether anything else is in
  // the library when this runs - a library that reads as completely empty is
  // refused categorically before the proportional test is reached - and that
  // is precisely the thing this test must not have an opinion about. Comparing
  // against the payload is also a stronger claim than any substring: the page
  // shows what it was told, whole.
  const shown = (await alert.locator(".alert-body").innerText()).replace(/\s+/g, " ").trim();
  expect(shown).toBe(refused.refused_reason.replace(/\s+/g, " ").trim());
  // Both reasons end in the promise that makes a refusal survivable, and the
  // page has to be carrying it whichever one was given.
  expect(shown).toContain("NOTHING HAS BEEN CHANGED");
  expect(shown).toContain("Your practice history, tags and transcriptions are untouched");
  await expect(alert).toContainText("Confirming never deletes anything");
  // It lists what it could not find, so a person can recognise which part of
  // their library it is talking about. Against the paths the payload actually
  // carries, not the total: a refusal lists at most scanner.UNMATCHED_SAMPLE of
  // them and says so separately, and this test can produce more than that.
  await alert.locator("details summary").click();
  await expect(alert.locator("details li")).toHaveCount(refused.unmatched_paths.length);

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
    // Exactly the loss that was shown and confirmed - the acknowledgement is
    // named after that set of paths (scanner._acknowledge_token), so a rescan
    // that marked a different number of rows would mean it had been accepted
    // for something else.
    expect(after.missing, JSON.stringify(after)).toBe(refused.unmatched_count);
  }).toPass({ timeout: 30_000 });

  // Marked, never deleted. Ordered after the barrier above, so this reads the
  // library the reconciliation actually left behind.
  const listed = await scores(request);
  for (const name of names.map((n) => `Uploads/${n}`)) {
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
