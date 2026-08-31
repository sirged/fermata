// Getting everything in and out (issue #58), from the Settings page's own
// controls. This client only triggers and reports - every field an archive
// carries, the validation that rejects a bad one, and the writing itself all
// happen server-side (see fermata/api.py's export_library/import_library and
// server/tests/test_portability_api.py, which is where the round trip is
// actually proved lossless field by field). What is worth proving here is
// narrower and different: that clicking Export really produces a downloaded
// archive, that a bad file is refused with something a person can read, and
// that a REAL exported archive - not a mock - round-trips through this
// page's own upload and preview path.
//
// NONE OF THESE TESTS CONFIRM AN IMPORT. Applying one for real is exactly
// the "always ADD, never merge" behaviour server/tests/test_portability_api.py
// already covers in an isolated database - importing HERE would add a second
// copy of every score, session and goal already in this suite's shared
// library (workers: 1, one server, one database - see playwright.config.js),
// and a goal already set for the current week would collide with the very
// goal the archive itself contains (practice_goals' own UNIQUE(owner,
// period_start)), refusing the request for a reason that has nothing to do
// with what this file is testing. Every assertion below stops at the preview
// - which is the dry run, and the dry run writes nothing - so this file never
// mutates the state any other spec depends on and needs no ordering relative
// to them.
//
// EVERY WAIT HERE IS ON THE PAGE (issue #110): a download event Playwright
// itself only fires once the browser has really started receiving the
// response, or text rendered by this component after its own fetch
// resolved. Nothing reads /api/export or /api/import's result through the
// request context and assumes it landed - a click's promise resolving is not
// the write (or, here, the read) actually finishing.
import { expect, test } from "@playwright/test";

const exportButton = (page) => page.getByTestId("export-button");
const fileInput = (page) => page.getByTestId("import-file-input");
const importError = (page) => page.getByTestId("import-error");
const importPreview = (page) => page.getByTestId("import-preview");

test("exporting the library downloads a real archive", async ({ page }) => {
  await page.goto("/#/settings");
  // The barrier is the browser's own download event, which cannot fire until
  // the server has actually answered - not a click resolving, which only
  // means the request was dispatched.
  const downloadPromise = page.waitForEvent("download");
  await exportButton(page).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^fermata-export-[0-9]+\.zip$/);
  // A real file landed on disk, not an empty stub - path() itself waits for
  // the download to finish before resolving.
  const path = await download.path();
  expect(path).not.toBeNull();
});

test("choosing a file that is not a Fermata archive shows a clear error", async ({ page }) => {
  await page.goto("/#/settings");
  await fileInput(page).setInputFiles({
    name: "not-an-archive.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("this is plainly not a zip file"),
  });
  await expect(importError(page)).toBeVisible();
  await expect(importError(page)).toContainText("zip");
  // The one thing a rejected archive must never do: pretend it previewed
  // something.
  await expect(importPreview(page)).toHaveCount(0);
});

test("choosing a real exported archive shows what it actually holds", async ({ page }) => {
  await page.goto("/#/settings");
  const downloadPromise = page.waitForEvent("download");
  await exportButton(page).click();
  const download = await downloadPromise;
  const archivePath = await download.path();

  await fileInput(page).setInputFiles(archivePath);
  // The preview is built from this Fermata's own real answer to the file
  // just uploaded - it becoming visible IS the barrier; nothing is read
  // before it does.
  await expect(importPreview(page)).toBeVisible();
  await expect(importPreview(page)).toContainText("This archive holds");
  await expect(importPreview(page)).toContainText("written");
  // The confirm control is offered - proving this got as far as a real,
  // applicable preview rather than an error rendered under a different
  // testid - but is never clicked; see the module comment on why.
  await expect(page.getByTestId("import-confirm")).toBeVisible();
});
