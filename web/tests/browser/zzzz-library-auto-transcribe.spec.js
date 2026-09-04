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

/** The throwaway library's root on disk (see playwright.config.js) - needed
 * only by the unmocked F3 test below, to place many files WITHOUT going
 * through POST /api/upload (which would scan each one immediately, one at a
 * time), so a single "Scan library" click discovers all of them together and
 * the automatic pass it starts has real, multi-second work to do. */
const libraryDir = () => {
  const dir = process.env.FERMATA_TEST_LIBRARY_DIR;
  if (!dir) throw new Error("FERMATA_TEST_LIBRARY_DIR is not set - see playwright.config.js");
  return dir;
};

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

/** Upload a real PDF and wait only for its row to appear - never for
 * whatever automatic pass its own scan might start (see uploadAndSettle,
 * above, for the version that does). Used by the F1 test below, which
 * mocks POST /api/transcribe/batch itself and so needs a score to select
 * but not the automatic pass its upload would otherwise start. */
async function uploadRowOnly(request, buffer, name) {
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
  return found;
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

  // scan.transcribe_batch_note reaches the screen (#190 review, F3) - the
  // one place a person could otherwise learn what the last scan's own
  // automatic pass decided was by noticing which cards got marked, or not
  // noticing at all when it was refused rather than started.
  await expect(page.locator(".scan-note", { hasText: "transcribing" })).toContainText(
    "started transcribing",
  );
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

// Both tests below reproduce #190 review's F1 and F3 by mocking the ONE
// response each needs, rather than racing a real background pass against
// the dialog or the page. Measured directly (server/tests/fixtures/engraved
// /notation_and_tab.pdf, three runs): tabextract.extract() over it takes
// 15-40ms - far too fast, against ordinary HTTP and scan round trips, to
// hold the batch slot open reliably from a black-box test even with a large
// occupying selection; an earlier version of this file tried exactly that
// and lost the race. What F1 and F3 are actually about - what the LIBRARY
// PAGE does with a given API response - does not need a real pass behind
// it: server/tests/test_transcribe_batch_api.py already proves the real
// pass produces these responses (a scan's own hook setting
// transcribe_batch_started/note, and POST /transcribe/batch answering
// `started: false` with the running pass's own status - see
// TranscribeBatchTriggerOut) at the process level.

test("selecting a score while a pass is already running is refused, not silently adopted", async ({
  page,
  request,
}) => {
  // #190 review, F1. On main this needed two people; the automatic pass
  // (#190 itself) makes it reachable with one ordinary click, right after
  // an upload or a boot scan: select a score, click Apply while SOME OTHER
  // pass is running, and POST /api/transcribe/batch answers 200 with the
  // OTHER pass's own status (`started: false`) rather than one for this
  // selection. The dialog used to bind that straight to its own state
  // regardless, showing somebody else's progress and results as though
  // they belonged to what was just chosen, and closing having transcribed
  // nothing this person selected - with nothing saying so.
  const target = await uploadRowOnly(request, EXTRACTABLE_PDF, "target.pdf");

  // The refusal shape POST /api/transcribe/batch/status actually answers
  // when a DIFFERENT pass is running - TranscribeBatchTriggerOut's own
  // fields, `started: false` and the RUNNING pass's own totals (2 scores
  // that are not "target.pdf" at all, which is exactly the point: nothing
  // about this selection).
  await page.route("**/api/transcribe/batch", (route) =>
    route.fulfill({
      json: {
        started: false,
        running: true,
        total: 2,
        processed: 0,
        transcribed: 0,
        already_transcribed: 0,
        non_extractable: 0,
        errored: 0,
        with_defective_bars: 0,
        reconvert: false,
        results: [],
        started_at: Date.now() / 1000,
        finished_at: null,
      },
    }),
  );

  await page.goto("/#/");
  await page.locator(".organise-toggle").click();
  const card = page.locator(`.card[href="#/score/${target.id}"]`);
  await expect(card).toBeVisible();
  await card.click();
  await page.locator(".transcribe-open").click();
  const dialog = page.locator(".dialog.transcribe");
  await expect(dialog).toBeVisible();
  await dialog.locator(".transcribe-apply").click();

  await expect(dialog.locator(".alert-error")).toContainText(
    "already running in the background",
  );
  await expect(dialog.locator(".alert-error")).toContainText("your selection was not started");
  // Never adopted the running pass's own progress or results - the bug
  // this pins: 2 scores, 0 processed, would have rendered as "Transcribing…
  // 0/2" and closed with a "0 transcribed..." summary belonging to nothing
  // this person chose, had the fix not checked `started`.
  await expect(dialog.locator(".transcribe-progress")).toHaveCount(0);
  await expect(dialog.locator(".transcribe-line")).toHaveCount(0);
});

test("a pass this page did not start is visible while it runs and after it is refused", async ({
  page,
}) => {
  // #190 review, F3: transcribe_batch_started/note were produced (scanner.
  // _finish_scan_chain sets them) but reached no reader anywhere in
  // web/src, and while a pass this page never started was running, the
  // page polled nothing and showed nothing about it - the one moment F1
  // happens was invisible until the click itself failed.
  await page.route("**/api/transcribe/batch/status", (route) =>
    route.fulfill({
      json: {
        running: true,
        total: 7,
        processed: 3,
        transcribed: 2,
        already_transcribed: 1,
        non_extractable: 0,
        errored: 0,
        with_defective_bars: 0,
        reconvert: false,
        results: [],
        started_at: Date.now() / 1000,
        finished_at: null,
      },
    }),
  );
  await page.route("**/api/scan/status", (route) =>
    route.fulfill({
      json: {
        scanning: false,
        total: 1,
        processed: 1,
        added: 1,
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
        finished_at: Date.now() / 1000,
        transcribe_batch_started: false,
        transcribe_batch_note:
          "did not start transcribing the newly scanned scores because a bulk " +
          "transcription pass was already running - start one by hand from the " +
          "library view to pick them up",
      },
    }),
  );

  await page.goto("/#/");

  // The live indicator: ambient awareness of a pass this page never
  // started, visible with no dialog open at all. Matched on "Transcribing
  // 7" rather than "in the background" - hasText is a case-insensitive
  // substring match, and the SUCCESS note (`_finish_scan_chain`'s own
  // "started transcribing N newly scanned score(s) in the background")
  // contains "in the background" too, so that phrase alone does not
  // distinguish the live line from a chain's own after-the-fact note when
  // both could plausibly render together. "Transcribing 7" is the live
  // line's own template start (`Transcribing {total} score(s) in the
  // background…`) and never appears in the backend's differently-worded
  // note.
  await expect(page.locator(".scan-note", { hasText: "Transcribing 7" })).toContainText(
    "in the background",
  );
  // The static note: what the scan's own chain decided, in words, since a
  // scan runs unattended on every boot and this is the one place anybody
  // could read that it declined to start a pass.
  await expect(page.locator(".scan-note", { hasText: "did not start" })).toContainText(
    "already running",
  );
});

test("clicking Scan while the page is open shows the live background-pass line, unmocked", async ({
  page,
  request,
}) => {
  // #190 review, F3, unmocked. The two tests above prove the LIBRARY PAGE
  // renders `backgroundBatch` correctly given a canned response; neither
  // proves the actual reactive poll (Library.svelte's own $effect) ever
  // NOTICES a real pass that starts AFTER the page is already open - the
  // exact scenario the review measured directly against the real build: 250
  // files, click Scan, the pass provably running, no live line on screen
  // after 8s, because the first version of that effect only checked once,
  // at mount, before the click that starts the pass had even happened.
  //
  // Many real files, placed on disk directly (never through POST /api/upload,
  // which would scan and auto-transcribe each one individually the instant
  // it lands) so ONE "Scan library" click discovers all of them together and
  // the automatic pass that scan's own chain starts has real, multi-second
  // work to do - long enough to outlast this page's own 3s poll interval by
  // a wide margin, the same reasoning zzzz-library-transcribe-batch.spec.js's
  // WARMUP_COUNT uses for the same reason, scaled up here because a poll that
  // only rechecks every 3s (rather than that file's own 100ms out-of-band
  // wait) needs a wider margin to reliably catch the pass before it ends.
  // Measured directly on this box (server/tests/fixtures/engraved/
  // notation_and_tab.pdf, the same fixture EXTRACTABLE_PDF loads):
  // tabextract.extract() alone runs ~15ms/file, but the FULL scan+transcribe
  // pipeline this test actually exercises (hashing, DB writes, the scan's
  // own walk) runs closer to the ~90ms/score the PR body's 293-file
  // reference-library run measured end to end. 120 undershot that margin
  // once (measured: the occupying pass had already finished by the time
  // this test reached the Apply click, so it succeeded instead of being
  // refused) - 400 keeps the pass running for tens of seconds, comfortably
  // outlasting this page's own 3s poll interval and every round trip below.
  const BULK_COUNT = 400;
  const dir = path.join(libraryDir(), "Uploads");
  fs.mkdirSync(dir, { recursive: true });
  for (let i = 0; i < BULK_COUNT; i++) {
    fs.writeFileSync(path.join(dir, `bulk${i}.pdf`), EXTRACTABLE_PDF);
  }

  await page.goto("/#/");
  await page.locator("button", { hasText: "Scan library" }).click();

  // Out of band, via the API directly rather than the page's own poll -
  // this is the moment a real pass this page did not (yet) know about
  // starts running, confirmed independently of whatever the UI shows.
  await expect(async () => {
    const b = await batchStatus(request);
    expect(b.running, JSON.stringify(b)).toBe(true);
  }).toPass({ timeout: DEADLINE_MS });

  // The live line, on the real page, having noticed a pass it did not
  // start on its own. Matched with a regex rather than a literal string:
  // the template that renders it (Library.svelte) wraps across two source
  // lines with no <br>, so the rendered text carries a literal newline
  // between "in" and "the background" - \s+ absorbs that (and any other
  // whitespace collapsing a browser applies) instead of asserting on it.
  await expect(
    page.locator(".scan-note", { hasText: /Transcribing \d+ scores? in\s+the background/ }),
  ).toBeVisible({ timeout: DEADLINE_MS });

  // Selecting a score while that real pass is still running is refused,
  // not silently adopted - F1's own claim, unmocked: a real occupying pass
  // this click did not start, a real refusal from the real backend.
  await page.locator(".organise-toggle").click();
  const anyCard = page.locator(".card").first();
  await expect(anyCard).toBeVisible({ timeout: DEADLINE_MS });
  await anyCard.click();
  await page.locator(".transcribe-open").click();
  const dialog = page.locator(".dialog.transcribe");
  await expect(dialog).toBeVisible();
  await dialog.locator(".transcribe-apply").click();
  await expect(dialog.locator(".alert-error")).toContainText(
    "already running in the background",
  );
  await expect(dialog.locator(".alert-error")).toContainText("your selection was not started");

  // Settled before this test ends, not left running - a leftover pass over
  // BULK_COUNT files would occupy the batch slot for whatever the suite
  // runs next, the same reasoning uploadAndSettle uses throughout this file.
  await dialog.locator(".dialog-cancel").click();
  await autoTranscribePassSettled(request);
});
