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

test("a bulk pass over a mixed selection shows live progress and every score's real outcome", async ({
  page,
  request,
}) => {
  const fresh = await upload(request, EXTRACTABLE_PDF, "fresh.pdf", "Uploads", "Fresh score");
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

  // "fresh.pdf" runs through its OWN dialog, with reconvert ticked, rather
  // than alongside the other three below. #190 means uploading an
  // extractable PDF now transcribes it automatically, almost immediately -
  // upload()'s own wait for the scan to settle already gives that automatic
  // pass time to run, so by the time Organise mode can select it, "fresh.pdf"
  // already has an extracted transcription of its own. Reconvert is what
  // still proves the "transcribed" outcome renders correctly here: it asks
  // for exactly the re-extraction the automatic pass already did, and
  // reports the same "transcribed" outcome a genuinely first-time pass
  // would. (The automatic pass itself is asserted directly in
  // zzzzz-library-scan-transcribes.spec.js, not here.)
  await organise(page);
  await choose(page, fresh);
  await page.locator(".transcribe-open").click();
  let dialog = page.locator(".dialog.transcribe");
  await expect(dialog).toBeVisible();
  await dialog.locator(".reconvert-option input").check();
  await dialog.locator(".transcribe-apply").click();
  await waitForBatchDone(page);
  await expect(dialog.locator(".transcribe-line", { hasText: "Fresh score" })).toContainText(
    "transcribed",
  );
  await expect(dialog.locator(".transcribe-progress").first()).toContainText("1 transcribed");
  await dialog.locator(".dialog-cancel").click();
  await expect(page.locator(".dialog.transcribe")).toHaveCount(0);

  // The other three, in their own run with reconvert OFF - #190's automatic
  // pass never touches "already.pdf" or "edited.pdf" in a way that matters
  // here (they already carry a transcription of their own from the explicit
  // POST /transcribe calls above, whichever pass happened to write it first)
  // or "bad.pdf" (not extractable, so an automatic attempt has nothing to
  // write) - and reconvert OFF is exactly what proves "already had one"
  // rather than reconverting them regardless.
  await organise(page);
  await choose(page, already);
  await choose(page, edited);
  await choose(page, bad);
  await expect(page.locator(".selected-count")).toHaveText("3 selected");

  await page.locator(".transcribe-open").click();
  dialog = page.locator(".dialog.transcribe");
  await expect(dialog).toBeVisible();
  await dialog.locator(".transcribe-apply").click();

  // Progress is shown WHILE it runs, not only once it is done.
  await expect(dialog.locator(".transcribe-progress")).toContainText("Transcribing");

  await waitForBatchDone(page);

  const line = (title) => dialog.locator(".transcribe-line", { hasText: title });
  await expect(line("Already transcribed score")).toContainText("already had one");
  await expect(line("Already transcribed score")).toContainText("reconvert");
  await expect(line("Hand-edited score")).toContainText("already had one");
  await expect(line("Hand-edited score")).toContainText("never overwrites");
  await expect(line("Not-extractable score")).toContainText("not extractable");

  // The aggregate summary, not just the per-line detail.
  await expect(dialog.locator(".transcribe-progress").first()).toContainText("0 transcribed");
  await expect(dialog.locator(".transcribe-progress").first()).toContainText(
    "2 already had one",
  );
  await expect(dialog.locator(".transcribe-progress").first()).toContainText(
    "1 not extractable",
  );

  await dialog.locator(".dialog-cancel").click();
  await expect(page.locator(".dialog.transcribe")).toHaveCount(0);
  await expect(page.locator(".notice")).toContainText("2 already had one");

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
