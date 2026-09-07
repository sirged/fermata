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
const importRenames = (page) => page.getByTestId("import-renames");

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
  // schema_version_read (#282) - always readable on a dry run, since a
  // preview never gets far enough to see an archive whose version this
  // Fermata refuses.
  await expect(importPreview(page)).toContainText("schema version");
  // ImportOut.tags_reused is 0 on every dry run by construction (the merge
  // decision it counts is never made without a write transaction open - see
  // ImportOut's own docstring), which makes this the one count in the whole
  // response a preview can never show as nonzero. Its absence here is what
  // proves the zero-fold in importSummaryText actually folds it away rather
  // than the field simply never having been wired up.
  await expect(importPreview(page)).not.toContainText("reused");
  // The confirm control is offered - proving this got as far as a real,
  // applicable preview rather than an error rendered under a different
  // testid - but is never clicked; see the module comment on why.
  await expect(page.getByTestId("import-confirm")).toBeVisible();
});

test("a preview names the newer tables too, when the archive actually carries them", async ({
  page,
  request,
}) => {
  // ImportOut carries counts for setlists, saved drill scopes and drill
  // history that the preview never named before this (issue #284). Seeded
  // here so each is a real, nonzero fact in the exported archive rather than
  // depending on whatever the shared library happens to hold when this spec
  // runs - and torn down in `finally` regardless of how the assertions come
  // out, the same discipline the rename test above uses.
  // /api/upload answers with only { saved: <path> } - the score row it
  // starts a scan to create is read back separately, the same way
  // zzz-library-organise.spec.js's own upload() helper does.
  const uploadName = "data-portability-seed.musicxml";
  await request.post("/api/upload?folder=Uploads", {
    multipart: {
      file: {
        name: uploadName,
        mimeType: "application/xml",
        buffer: Buffer.from(
          '<?xml version="1.0"?><score-partwise><part-list>' +
            '<score-part id="P1"><part-name>Seed</part-name></score-part>' +
            "</part-list><part id=\"P1\"></part></score-partwise>",
        ),
      },
    },
  });
  let score;
  await expect(async () => {
    const found = (await (await request.get("/api/scores")).json()).find(
      (s) => s.path === `Uploads/${uploadName}`,
    );
    expect(found, `${uploadName} never appeared in the library`).toBeTruthy();
    score = found;
  }).toPass({ timeout: 30_000 });
  const setlist = await (
    await request.post("/api/setlists", { data: { name: "Data portability setlist check" } })
  ).json();
  const preset = await (
    await request.post("/api/trainer/presets", {
      data: {
        name: "Data portability preset check",
        start_fret: 0,
        end_fret: 4,
        strings: [1, 2],
      },
    })
  ).json();
  try {
    await request.post(`/api/setlists/${setlist.id}/scores`, { data: { score_id: score.id } });
    await request.post("/api/trainer/attempts", {
      data: {
        drill: "fret_to_note",
        direction: "position_to_note",
        target_string: 6,
        target_fret: 3,
        target_note: "G",
        given_note: "G",
      },
    });
    await request.post("/api/trainer/chord-attempts", {
      data: {
        drill: "chord_flashcards",
        direction: "shape_to_name",
        target_root: "C",
        target_quality: "major",
        target_shape: [{ string: 5, fret: 3 }],
        given_root: "C",
        given_quality: "major",
      },
    });

    await page.goto("/#/settings");
    const downloadPromise = page.waitForEvent("download");
    await exportButton(page).click();
    const download = await downloadPromise;
    const archivePath = await download.path();

    await fileInput(page).setInputFiles(archivePath);
    const preview = importPreview(page);
    await expect(preview).toBeVisible();
    // One count named for each table this component could not say anything
    // about before - the wording each field's own label produces in
    // importSummaryText, not a paraphrase of it.
    await expect(preview).toContainText("setlist");
    await expect(preview).toContainText("setlist entr"); // entry/entries
    await expect(preview).toContainText("saved drill scope");
    await expect(preview).toContainText("drill scope string set");
    await expect(preview).toContainText("fret-to-note drill attempt");
    await expect(preview).toContainText("chord drill attempt");
  } finally {
    await request.delete(`/api/trainer/presets/${preset.id}`);
    await request.delete(`/api/setlists/${setlist.id}`);
    await request.delete(`/api/scores/${score.id}`);
    await request.delete(`/api/trash/${score.id}`);
  }
});

test("previewing an archive that collides with a preset already here shows the rename", async ({
  page,
  request,
}) => {
  // #260's rename is reported on a dry run too (server/fermata/api.py's
  // _derive_preset_renames), which is exactly what keeps this test inside
  // the module comment's own rule above: the archive is only ever
  // PREVIEWED, never confirmed, so this never adds a second copy of
  // anything to the shared library. A preset made here, still present when
  // the same library's own export is re-uploaded, collides with itself -
  // deleted again in `finally` regardless of how the assertions come out,
  // so this spec leaves the shared library exactly as it found it.
  const created = await (
    await request.post("/api/trainer/presets", {
      data: {
        name: "Data portability rename check",
        start_fret: 0,
        end_fret: 4,
        strings: [6],
      },
    })
  ).json();
  try {
    await page.goto("/#/settings");
    const downloadPromise = page.waitForEvent("download");
    await exportButton(page).click();
    const download = await downloadPromise;
    const archivePath = await download.path();

    await fileInput(page).setInputFiles(archivePath);
    await expect(importPreview(page)).toBeVisible();
    await expect(importRenames(page)).toBeVisible();
    // The rename line now also names WHY (#260 vs #268's cleaning), read from
    // ImportOut.trainer_scope_presets_renamed's own `reason` field - a plain
    // name collision here, since nothing about this preset's name needed
    // cleaning up.
    await expect(importRenames(page)).toContainText(
      "Data portability rename check → Data portability rename check (imported) " +
        "(that name was already taken)",
    );
  } finally {
    await request.delete(`/api/trainer/presets/${created.id}`);
  }
});
