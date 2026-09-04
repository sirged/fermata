// Bulk transcription (issue #55), against the real backend and the real
// build.
//
// WHAT server/tests/test_transcribe_batch_api.py CANNOT PROVE: that any of
// this reaches the screen. That file pins the per-score outcomes and the
// scan-interaction matrix at the process level; #95's lesson, recorded in
// zz-library-missing.spec.js, is that a guarantee nothing renders is a
// guarantee nobody has. So what is asserted here is the seam:
//
//   1. selecting scores in Organise mode and starting a bulk pass shows
//      live progress and, once it finishes, a per-score outcome line for
//      EVERY score selected - transcribed, already-had-one and
//      not-extractable are all real outcomes this asserts by literal text,
//      not merely "some list rendered";
//   2. a hand-edited transcription's own line says it was never touched,
//      and the score's transcription content confirms that afterwards;
//   3. the sidebar's per-folder Transcribe button selects only that
//      folder's scores, not the whole library.
//
// WHY IT IS NAMED TO SORT AFTER zzz-library-organise AND BEFORE
// zzzz-score-progress: it uploads and deletes scores like both of them, and
// carries the same refusal-unless-throwaway-instance guard zzz-library-
// organise.spec.js's own header explains.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const ENGRAVED_DIR = path.join(here, "..", "..", "..", "server", "tests", "fixtures", "engraved");

// Real committed fixtures (see server/tests/conftest.py's extractable_pdf /
// non_extractable_pdf) - a genuine tab-staff score and a genuine notation-
// only one, so "transcribed" and "not extractable" are both real extractor
// outcomes rather than something this file has to fake.
const EXTRACTABLE_PDF = fs.readFileSync(path.join(ENGRAVED_DIR, "notation_and_tab.pdf"));
const NON_EXTRACTABLE_PDF = fs.readFileSync(path.join(ENGRAVED_DIR, "notation_only.pdf"));

const SCAN_DEADLINE_MS = 30_000;

/** The throwaway library's root on disk (see playwright.config.js) - needed
 * only by the mixed-selection test below, to place a file WITHOUT going
 * through POST /api/upload, so that file's own first scan can be triggered
 * at a moment the test controls precisely rather than through the upload
 * endpoint's own immediate scan. */
const libraryDir = () => {
  const dir = process.env.FERMATA_TEST_LIBRARY_DIR;
  if (!dir) throw new Error("FERMATA_TEST_LIBRARY_DIR is not set - see playwright.config.js");
  return dir;
};

async function scanSettled(request) {
  const deadline = Date.now() + SCAN_DEADLINE_MS;
  for (;;) {
    const status = await (await request.get("/api/scan/status")).json();
    if (!status.scanning) return status;
    if (Date.now() > deadline) throw new Error("a scan never finished");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

/** Upload a real PDF and wait for its score row, then wait out the scan the
 * upload started - the same two-wait pattern zzz-library-organise.spec.js
 * uses and explains: a move or a bulk action started the instant the row
 * appears can race the scan itself.
 *
 * Every copy of EXTRACTABLE_PDF carries the SAME embedded PDF title (MuseScore
 * writes its source filename into the document's own metadata, and
 * metadata.parse_path prefers that over the uploaded filename) - so distinct
 * filenames alone do not give distinct titles the way they do for the plain
 * MusicXML fixtures other specs in this suite use. `title`, when given,
 * PATCHes the row to something distinct right after the scan settles, which
 * is what this file's assertions actually key on. */
async function upload(request, buffer, name, folder = "Uploads", title = null) {
  const res = await request.post(`/api/upload?folder=${encodeURIComponent(folder)}`, {
    multipart: { file: { name, mimeType: "application/pdf", buffer } },
  });
  expect(res.ok(), await res.text()).toBe(true);
  let found;
  await expect(async () => {
    const scores = await (await request.get("/api/scores")).json();
    found = scores.find((s) => s.path === `${folder}/${name}`);
    expect(found, `${name} never appeared in the library`).toBeTruthy();
  }).toPass({ timeout: SCAN_DEADLINE_MS });
  await scanSettled(request);
  if (title) {
    const patched = await request.patch(`/api/scores/${found.id}`, { data: { title } });
    expect(patched.ok(), await patched.text()).toBe(true);
    found = await patched.json();
  }
  return found;
}

async function emptyTheLibrary(request) {
  await scanSettled(request);
  for (const score of await (await request.get("/api/scores")).json()) {
    await request.delete(`/api/scores/${score.id}`);
  }
  for (const score of await (await request.get("/api/trash")).json()) {
    await request.delete(`/api/trash/${score.id}`);
  }
}

const SUITE_FOLDERS = new Set(["Uploads", "BulkFolder", "OtherFolder"]);

test.beforeEach(async ({ request }) => {
  const existing = await (await request.get("/api/scores")).json();
  const foreign = existing.filter(
    (s) => !s.missing_since && !SUITE_FOLDERS.has(s.path.split("/")[0]),
  );
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

async function organise(page) {
  await page.goto("/#/");
  await page.locator(".organise-toggle").click();
  await expect(page.locator(".organise-bar")).toBeVisible();
}

async function choose(page, score) {
  const card = page.locator(`.card[href="#/score/${score.id}"]`);
  await expect(card).toBeVisible();
  await card.click();
  await expect(card).toHaveClass(/selected/);
}

/** Waits for the dialog's progress text to report the run has finished
 * (the "Close" button only renders once `running` is false) - not a fixed
 * sleep, since the pass runs for as long as real PDF extraction actually
 * takes. */
async function waitForBatchDone(page) {
  await expect(page.locator(".dialog.transcribe .dialog-cancel")).toBeVisible({
    timeout: SCAN_DEADLINE_MS,
  });
}

async function apiBatchSettled(request, timeout = SCAN_DEADLINE_MS) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const status = await (await request.get("/api/transcribe/batch/status")).json();
    if (!status.running) return status;
    if (Date.now() > deadline) throw new Error("a bulk transcription pass never finished");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

test("a bulk pass over a mixed selection shows live progress and every score's real outcome", async ({
  page,
  request,
}) => {
  // #190 means uploading an extractable PDF transcribes it automatically,
  // almost immediately - so getting a genuinely first-time "transcribed"
  // outcome for "fresh.pdf" below (rather than a reconvert of one the
  // automatic pass already did) means keeping that automatic pass from
  // ever reaching it. Attempted first with a single throwaway score to
  // occupy the batch slot and lost: a single small PDF's real re-extraction
  // is fast enough (measured directly: 15-40ms) that it had already
  // finished by the time a scan triggered right after "confirmed running"
  // reached its own chain decision - "confirmed running at some instant" is
  // not "still running a few round trips later". WARMUP_COUNT real
  // re-extractions, fired together as ONE explicit pass this test starts
  // itself, is what actually holds the slot open long enough: still
  // genuine PDF work (not a sleep a future refactor could silently race
  // again), just enough of it that the margin is measured in whole seconds
  // rather than milliseconds.
  //
  // "fresh.pdf" itself is placed on disk directly (never through POST
  // /api/upload, which would trigger its own scan immediately) so this test
  // controls exactly when its scan happens: only once the occupying pass is
  // CONFIRMED running - so its own automatic attempt (#190) is refused the
  // same way a hand-started pass refuses a second one (see
  // scanner._finish_scan_chain), not merely likely to be.
  const WARMUP_COUNT = 15;
  const warmups = await Promise.all(
    Array.from({ length: WARMUP_COUNT }, (_, i) =>
      upload(request, EXTRACTABLE_PDF, `warmup${i}.pdf`, "Uploads", `Warmup score ${i}`),
    ),
  );
  // upload() above only waits out each file's own SCAN, not whatever
  // automatic pass (#190) that scan's chain may have started - settled
  // explicitly here so the occupying pass below starts from a known idle
  // state, not racing an auto-batch of unpredictable timing (an earlier
  // version of this test raced exactly that and was flaky).
  await apiBatchSettled(request);

  const freshDir = path.join(libraryDir(), "Uploads");
  fs.mkdirSync(freshDir, { recursive: true });
  fs.writeFileSync(path.join(freshDir, "fresh.pdf"), EXTRACTABLE_PDF);

  const occupyRes = await request.post("/api/transcribe/batch", {
    data: { score_ids: warmups.map((w) => w.id), reconvert: true },
  });
  expect((await occupyRes.json()).started, "the occupying pass could not start").toBe(true);
  await expect(async () => {
    const status = await (await request.get("/api/transcribe/batch/status")).json();
    expect(status.running, JSON.stringify(status)).toBe(true);
  }).toPass({ timeout: 5_000 });

  const scanRes = await request.post("/api/scan");
  expect(scanRes.ok(), await scanRes.text()).toBe(true);
  const scanBody = await scanRes.json();
  expect(scanBody.started, JSON.stringify(scanBody)).toBe(true);
  await scanSettled(request);
  await apiBatchSettled(request);

  let fresh;
  await expect(async () => {
    const scores = await (await request.get("/api/scores")).json();
    fresh = scores.find((s) => s.path === "Uploads/fresh.pdf");
    expect(fresh, "fresh.pdf never appeared in the library").toBeTruthy();
  }).toPass({ timeout: SCAN_DEADLINE_MS });
  const freshPatched = await request.patch(`/api/scores/${fresh.id}`, {
    data: { title: "Fresh score" },
  });
  expect(freshPatched.ok(), await freshPatched.text()).toBe(true);
  fresh = await freshPatched.json();

  // The premise this test needs, checked rather than assumed: if the scan
  // above had somehow landed before the occupying pass was confirmed
  // running, "fresh.pdf" would already carry a transcription here, and the
  // assertions below would be proving something weaker than they claim to.
  expect(
    fresh.has_transcription,
    "fresh.pdf was auto-transcribed despite a bulk pass being confirmed running when its " +
      "scan was triggered",
  ).toBe(false);

  const already = await upload(
    request, EXTRACTABLE_PDF, "already.pdf", "Uploads", "Already transcribed score",
  );
  const transcribed = await request.post(`/api/scores/${already.id}/transcribe`);
  expect(transcribed.ok(), await transcribed.text()).toBe(true);
  const edited = await upload(request, EXTRACTABLE_PDF, "edited.pdf", "Uploads", "Hand-edited score");
  await request.post(`/api/scores/${edited.id}/transcribe`);
  const savedEdit = await request.put(`/api/scores/${edited.id}/transcription`, {
    data: { content: '\\title "hand edited"\n.\n:4 0.1 |' },
  });
  expect(savedEdit.ok(), await savedEdit.text()).toBe(true);
  const bad = await upload(
    request, NON_EXTRACTABLE_PDF, "bad.pdf", "Uploads", "Not-extractable score",
  );

  await organise(page);
  await choose(page, fresh);
  await choose(page, already);
  await choose(page, edited);
  await choose(page, bad);
  await expect(page.locator(".selected-count")).toHaveText("4 selected");

  await page.locator(".transcribe-open").click();
  const dialog = page.locator(".dialog.transcribe");
  await expect(dialog).toBeVisible();
  await dialog.locator(".transcribe-apply").click();

  // Progress is shown WHILE it runs, not only once it is done.
  await expect(dialog.locator(".transcribe-progress")).toContainText("Transcribing");

  await waitForBatchDone(page);

  const line = (title) => dialog.locator(".transcribe-line", { hasText: title });
  await expect(line("Fresh score")).toContainText("transcribed");
  await expect(line("Already transcribed score")).toContainText("already had one");
  await expect(line("Already transcribed score")).toContainText("reconvert");
  await expect(line("Hand-edited score")).toContainText("already had one");
  await expect(line("Hand-edited score")).toContainText("never overwrites");
  await expect(line("Not-extractable score")).toContainText("not extractable");

  // The aggregate summary, not just the per-line detail.
  await expect(dialog.locator(".transcribe-progress").first()).toContainText("1 transcribed");
  await expect(dialog.locator(".transcribe-progress").first()).toContainText(
    "2 already had one",
  );
  await expect(dialog.locator(".transcribe-progress").first()).toContainText(
    "1 not extractable",
  );

  await dialog.locator(".dialog-cancel").click();
  await expect(page.locator(".dialog.transcribe")).toHaveCount(0);
  await expect(page.locator(".notice")).toContainText("1 transcribed");

  // The edited score's hand-corrected content survived the run, literally.
  const stillEdited = await (
    await request.get(`/api/scores/${edited.id}/transcription`)
  ).json();
  expect(stillEdited.source).toBe("edited");
  expect(stillEdited.content).toBe('\\title "hand edited"\n.\n:4 0.1 |');

  // The fresh score really does have a transcription now, reflected on the
  // card once the grid refreshes.
  await page.reload();
  const freshTranscribed = await (await request.get(`/api/scores/${fresh.id}`)).json();
  expect(freshTranscribed.has_transcription).toBe(true);
});

test("the sidebar's per-folder Transcribe button selects only that folder", async ({
  page,
  request,
}) => {
  const inFolder = await upload(
    request, EXTRACTABLE_PDF, "in-folder.pdf", "BulkFolder", "In the folder",
  );
  await upload(request, EXTRACTABLE_PDF, "elsewhere.pdf", "OtherFolder", "Elsewhere entirely");

  await page.goto("/#/");
  await expect(page.locator(".side-item", { hasText: "BulkFolder" })).toBeVisible();
  const row = page.locator(".side-row", { has: page.locator(".side-item", { hasText: "BulkFolder" }) });
  await row.locator(".side-transcribe").click();

  const dialog = page.locator(".dialog.transcribe");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".dialog-head")).toContainText("BulkFolder");
  await dialog.locator(".transcribe-apply").click();
  await waitForBatchDone(page);

  // Only the one score in BulkFolder was touched - the elsewhere.pdf score
  // never appears in this run's results at all.
  await expect(dialog.locator(".transcribe-line")).toHaveCount(1);
  await expect(dialog.locator(".transcribe-line")).toContainText("In the folder");

  // The same claim, from the API's own results rather than only the
  // rendered line - the exact set of ids this pass actually ran over.
  const finished = await apiBatchSettled(request);
  expect(finished.results.map((r) => r.score_id)).toEqual([inFolder.id]);

  // elsewhere.pdf was never selected for THIS bulk pass - already proven
  // above by the dialog's own result count and title, not by
  // has_transcription: an extractable PDF gains one on its own the moment a
  // scan reaches it, whether or not anybody ever selects it for a bulk pass
  // (#190), so has_transcription can no longer distinguish "selected here"
  // from "scanned at some point".
  const scores = await (await request.get("/api/scores")).json();
  const inFolderNow = scores.find((s) => s.id === inFolder.id);
  expect(inFolderNow.has_transcription).toBe(true);
});
