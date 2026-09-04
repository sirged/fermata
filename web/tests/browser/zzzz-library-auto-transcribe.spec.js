// A freshly scanned library transcribes itself, and says so (issue #190).
//
// WHAT server/tests/test_transcribe_batch_api.py CANNOT PROVE: that any of
// this reaches the screen. That file (and test_scanner.py) pin the scan's
// own hook - one bulk pass over exactly the ids a chain of scans added, never
// one per scan, never a second pass while one is already running by hand -
// at the process level. #95's lesson, recorded in zz-library-missing.spec.js,
// is that a guarantee nothing renders is a guarantee nobody has. So what is
// asserted here is the seam:
//
//   1. uploading an extractable PDF (which triggers its own scan - see
//      api.upload) ends with that score transcribed, with no bulk-
//      transcription click anywhere in this test, and the library card
//      shows a mark for it; an upload that is NOT extractable ends with no
//      transcription and no mark, so the mark is not merely "the card was
//      touched by a scan";
//   2. the transcription filter narrows the grid to exactly the transcribed
//      set, and its complement to exactly the untranscribed one.
//
// WHY IT IS NAMED TO SORT AMONG THE OTHER zzzz-library-* SPECS: it uploads
// and deletes scores the same way they do, and carries the same refusal-
// unless-throwaway-instance guard zzz-library-organise.spec.js's own header
// explains. Fully emptying the library after EVERY test - rather than
// tracking its own small "OWN" list, the way zz-library-missing does - is
// deliberate here: this file's whole subject is what a FRESH scan does, so
// starting one from a library that already has a transcribed score in it
// would silently defeat its own premise.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const ENGRAVED_DIR = path.join(here, "..", "..", "..", "server", "tests", "fixtures", "engraved");

// Real committed fixtures (see server/tests/conftest.py's extractable_pdf /
// non_extractable_pdf) - a genuine tab-staff score and a genuine notation-
// only one, so "transcribed itself" and "did not" are both real extractor
// outcomes rather than something this file has to fake.
const EXTRACTABLE_PDF = fs.readFileSync(path.join(ENGRAVED_DIR, "notation_and_tab.pdf"));
const NON_EXTRACTABLE_PDF = fs.readFileSync(path.join(ENGRAVED_DIR, "notation_only.pdf"));

const DEADLINE_MS = 30_000;

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function scanStatus(request) {
  return (await request.get("/api/scan/status")).json();
}

async function batchStatus(request) {
  return (await request.get("/api/transcribe/batch/status")).json();
}

/**
 * The barrier this whole file needs, and the exact race
 * zz-library-missing.spec.js's own scanAndWait warns about, one layer
 * further down: `scanning` going false only means the SCAN is done, not
 * that its own chain-completion hook (scanner._finish_scan_chain) has
 * decided anything about a bulk pass, and certainly not that a bulk pass it
 * started has finished. `transcribe_batch_started` moving away from `null`
 * IS that hook having decided (see scanner.py and ScanStatusOut) - only
 * once it has is polling /api/transcribe/batch/status for `running` to go
 * false actually about the pass this upload's own scan may have started,
 * rather than one from earlier that just happened to still be running.
 */
async function autoTranscribePassSettled(request) {
  const deadline = Date.now() + DEADLINE_MS;
  let status;
  for (;;) {
    status = await scanStatus(request);
    if (!status.scanning && status.transcribe_batch_started !== null) break;
    if (Date.now() > deadline) {
      throw new Error(
        `the scan's own chain never finished deciding about a bulk pass: ${JSON.stringify(status)}`,
      );
    }
    await sleep(100);
  }
  for (;;) {
    const b = await batchStatus(request);
    if (!b.running) return { scan: status, batch: b };
    if (Date.now() > deadline) throw new Error("the automatic bulk transcription pass never finished");
    await sleep(100);
  }
}

/** Upload a real PDF, wait for its row to appear, and wait out everything a
 * fresh upload can set in motion - its own scan (api.upload triggers one)
 * AND the bulk transcription pass that scan's chain may have started on its
 * own (#190) - before handing back the score exactly as the library holds
 * it now. */
async function uploadAndSettle(request, buffer, name, title) {
  const res = await request.post("/api/upload?folder=Uploads", {
    multipart: { file: { name, mimeType: "application/pdf", buffer } },
  });
  expect(res.ok(), await res.text()).toBe(true);
  let found;
  await expect(async () => {
    const scores = await (await request.get("/api/scores")).json();
    found = scores.find((s) => s.path === `Uploads/${name}`);
    expect(found, `${name} never appeared in the library`).toBeTruthy();
  }).toPass({ timeout: DEADLINE_MS });
  await autoTranscribePassSettled(request);
  const patched = await request.patch(`/api/scores/${found.id}`, { data: { title } });
  expect(patched.ok(), await patched.text()).toBe(true);
  return patched.json();
}

async function emptyTheLibrary(request) {
  for (const score of await (await request.get("/api/scores")).json()) {
    await request.delete(`/api/scores/${score.id}`);
  }
  for (const score of await (await request.get("/api/trash")).json()) {
    await request.delete(`/api/trash/${score.id}`);
  }
}

test.beforeEach(async ({ request }) => {
  const existing = await (await request.get("/api/scores")).json();
  expect(
    existing,
    "refusing to run: this backend already has scores in its library, so it is not the " +
      "throwaway instance the suite creates - and this file's whole subject is what a FRESH " +
      "scan does",
  ).toEqual([]);
});

// After EVERY test, not only at the end - each test in this file starts from
// a library that reads as genuinely fresh (see beforeEach above), which a
// later test in the same file could not claim if an earlier one's uploads
// were still sitting in it.
test.afterEach(async ({ request }) => {
  await emptyTheLibrary(request);
});

test("an uploaded extractable score transcribes itself, and the card shows it", async ({
  page,
  request,
}) => {
  const good = await uploadAndSettle(
    request, EXTRACTABLE_PDF, "auto-extractable.pdf", "Auto extractable score",
  );
  expect(good.has_transcription, JSON.stringify(good)).toBe(true);

  const bad = await uploadAndSettle(
    request, NON_EXTRACTABLE_PDF, "auto-non-extractable.pdf", "Auto non-extractable score",
  );
  expect(bad.has_transcription, JSON.stringify(bad)).toBe(false);

  await page.goto("/#/");
  await expect(page.locator(`.card[href="#/score/${good.id}"] .transcribed-mark`)).toBeVisible();
  await expect(page.locator(`.card[href="#/score/${bad.id}"] .transcribed-mark`)).toHaveCount(0);
});

test("the transcription filter narrows the grid to the transcribed set and its complement", async ({
  page,
  request,
}) => {
  const good = await uploadAndSettle(
    request, EXTRACTABLE_PDF, "filter-extractable.pdf", "Filter extractable score",
  );
  const bad = await uploadAndSettle(
    request, NON_EXTRACTABLE_PDF, "filter-non-extractable.pdf", "Filter non-extractable score",
  );
  expect(good.has_transcription).toBe(true);
  expect(bad.has_transcription).toBe(false);

  await page.goto("/#/");
  await expect(page.locator(".card")).toHaveCount(2);

  await page.locator(".transcribed-filter").selectOption("yes");
  await expect(page.locator(".card")).toHaveCount(1);
  await expect(page.locator(`.card[href="#/score/${good.id}"]`)).toBeVisible();
  await expect(page.locator(`.card[href="#/score/${bad.id}"]`)).toHaveCount(0);

  await page.locator(".transcribed-filter").selectOption("no");
  await expect(page.locator(".card")).toHaveCount(1);
  await expect(page.locator(`.card[href="#/score/${bad.id}"]`)).toBeVisible();
  await expect(page.locator(`.card[href="#/score/${good.id}"]`)).toHaveCount(0);
});
