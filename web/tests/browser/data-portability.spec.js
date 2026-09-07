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
//
// WHAT NEVER GOES THROUGH THE API HERE: a POST to /api/trainer/attempts or
// /api/trainer/chord-attempts. Neither table has a DELETE route (see
// fret-weak-spots.spec.js's own file header on why trainer_attempts has to
// stay untouched by every OTHER spec in this shared-database suite), so a
// row logged here would leak into the rest of the run with no way to clean
// it back up. The "newer tables" test below needs a real, nonzero fact in
// both of those tables anyway - so it edits a real exported archive's own
// manifest.json directly (via tests/browser/fixtures/zip.js) rather than
// seed either table through its endpoint. That is exactly the path under
// test - _apply_import reads `tables["trainer_attempts"]` verbatim, never
// anything about how the row got into the archive - and it leaves the live
// tables exactly as they were found, proved by reading their counts before
// and after.
import fs from "node:fs";

import { expect, test } from "@playwright/test";

import { buildZip, readZip } from "./fixtures/zip.js";

const exportButton = (page) => page.getByTestId("export-button");
const fileInput = (page) => page.getByTestId("import-file-input");
const importError = (page) => page.getByTestId("import-error");
const importPreview = (page) => page.getByTestId("import-preview");
const importRenames = (page) => page.getByTestId("import-renames");
const importCleaned = (page) => page.getByTestId("import-cleaned");

// The 14 tables a manifest's `tables` object always carries (api.py's
// EXPORT_TABLE_NAMES) - every key `_read_and_validate_manifest` requires
// present as a list, empty unless a test below fills one in. Kept once here
// rather than repeated per test, the way the "only scores" manifest above
// builds its own inline (that one predates this helper).
function emptyTables() {
  return {
    instruments: [],
    tags: [],
    scores: [],
    score_tags: [],
    transcriptions: [],
    practice_sessions: [],
    practice_goals: [],
    settings: [],
    setlists: [],
    setlist_scores: [],
    trainer_attempts: [],
    trainer_chord_attempts: [],
    trainer_scope_presets: [],
    trainer_scope_preset_strings: [],
  };
}

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
  // history that the preview never named before this (issue #284). The
  // setlist, its membership and the preset below are seeded through the
  // real API and torn down in `finally` regardless of how the assertions
  // come out, the same discipline the rename test above uses - every one of
  // those tables has a real DELETE route, so a real POST here leaves
  // nothing behind.
  //
  // trainer_attempts and trainer_chord_attempts are the two exceptions:
  // NEITHER has a delete route at all (see fret-weak-spots.spec.js's own
  // file header on why trainer_attempts has to stay untouched by every
  // OTHER spec in this shared-database suite - the same is true of
  // trainer_chord_attempts, just not yet load-bearing for an ordering
  // rule). A row POSTed to either endpoint here would leak into every later
  // spec in this run with no way to clean it up again - which is exactly
  // what a real archive's own export/import round trip already carries
  // without ever touching those live tables: the two rows below are
  // written directly into a REAL exported archive's own manifest.json (the
  // import path this test actually means to exercise, never the POST
  // routes), and the counts read before and after prove neither table was
  // touched.
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
  const attemptsBefore = (await (await request.get("/api/trainer/attempts")).json()).total;
  const chordAttemptsBefore = (
    await (await request.get("/api/trainer/chord-attempts")).json()
  ).total;
  try {
    await request.post(`/api/setlists/${setlist.id}/scores`, { data: { score_id: score.id } });

    await page.goto("/#/settings");
    const downloadPromise = page.waitForEvent("download");
    await exportButton(page).click();
    const download = await downloadPromise;
    const archivePath = await download.path();

    // The real export, edited rather than replayed through the two POST
    // routes that leak (see the comment above): read its own manifest.json
    // back out, add one fret-to-note and one chord attempt directly to the
    // tables the import path reads, and re-pack the SAME files/ entries
    // (untouched, still hashing to what the manifest already claims for
    // them) into a new archive. Every OTHER table here - the score, the
    // setlist, the preset - is still the real export's own real row;
    // nothing about this fabricates a whole manifest from nothing (that is
    // the OTHER new test in this file, which needs exactly that).
    const entries = readZip(fs.readFileSync(archivePath));
    const manifestEntry = entries.find((e) => e.name === "manifest.json");
    const manifest = JSON.parse(manifestEntry.data.toString("utf-8"));
    manifest.tables.trainer_attempts.push({
      owner: "local",
      session_id: null,
      drill: "fret_to_note",
      direction: "position_to_note",
      target_string: 6,
      target_fret: 3,
      target_note: "G",
      given_string: null,
      given_fret: null,
      given_note: "G",
      correct: 1,
      response_ms: null,
    });
    manifest.tables.trainer_chord_attempts.push({
      owner: "local",
      session_id: null,
      drill: "chord_flashcards",
      direction: "shape_to_name",
      target_root: "C",
      target_quality: "major",
      target_shape: JSON.stringify([{ string: 5, fret: 3 }]),
      given_root: "C",
      given_quality: "major",
      given_notes: null,
      given_shape: null,
      correct: 1,
      response_ms: null,
    });
    manifestEntry.data = Buffer.from(JSON.stringify(manifest), "utf-8");
    const editedArchive = buildZip(entries);

    await fileInput(page).setInputFiles({
      name: "edited-export.zip",
      mimeType: "application/zip",
      buffer: editedArchive,
    });
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
  // Neither trainer table was ever written to - the archive was edited, not
  // replayed through the POST routes that leak - so both counts are exactly
  // what they were before this test ran.
  expect((await (await request.get("/api/trainer/attempts")).json()).total).toBe(attemptsBefore);
  expect((await (await request.get("/api/trainer/chord-attempts")).json()).total).toBe(
    chordAttemptsBefore,
  );
});

test("previewing an archive that carries only scores names just that, with every other count folded away", async ({
  page,
}) => {
  // importSummaryText's zero-fold (issue #284): every IMPORT_COUNT_FIELD
  // that is 0 disappears into one closing clause rather than being named
  // one by one, and the ALL-zero case gets its own sentence rather than
  // reusing that clause with nothing named first - see that function's own
  // comment on the wording bug this replaced ("This archive holds nothing
  // else was in the archive"). None of the five tests above build an
  // archive isolated enough to prove the PARTIAL fold - one real count,
  // every other one genuinely zero - still reads correctly: the shared
  // library's own export always carries a mix of nonzero counts. So this
  // one is built from nothing rather than downloaded and edited, with a
  // single scores row and every other table empty - format, schema_version,
  // exported_at and fermata_version are still the real export's own (read
  // back the same way the test above does), only `tables` is replaced, so
  // this is never a guess at what a valid manifest looks like.
  await page.goto("/#/settings");
  const downloadPromise = page.waitForEvent("download");
  await exportButton(page).click();
  const download = await downloadPromise;
  const real = JSON.parse(
    readZip(fs.readFileSync(await download.path()))
      .find((e) => e.name === "manifest.json")
      .data.toString("utf-8"),
  );

  const onlyScores = {
    ...real,
    tables: {
      instruments: [],
      tags: [],
      // file_included: false, so nothing here needs a matching files/ entry
      // (see api.py's _referenced_files) - only the row, which is all this
      // preview ever reads a count from.
      scores: [
        {
          id: 1,
          path: "Uploads/only-scores-preview.musicxml",
          hash: "0".repeat(40),
          deleted_at: null,
          file_included: false,
        },
      ],
      score_tags: [],
      transcriptions: [],
      practice_sessions: [],
      practice_goals: [],
      settings: [],
      setlists: [],
      setlist_scores: [],
      trainer_attempts: [],
      trainer_chord_attempts: [],
      trainer_scope_presets: [],
      trainer_scope_preset_strings: [],
    },
  };
  const archive = buildZip([
    { name: "manifest.json", data: Buffer.from(JSON.stringify(onlyScores), "utf-8") },
  ]);

  await fileInput(page).setInputFiles({
    name: "only-scores.zip",
    mimeType: "application/zip",
    buffer: archive,
  });
  await expect(importPreview(page)).toBeVisible();
  // The exact sentence importSummaryText produces when exactly one count is
  // real and every other one folds away - not a substring, so a mutation
  // that breaks that fold (dropping the "- nothing else..." clause, or
  // reusing the all-zero branch's own wording here instead) shows up as a
  // mismatch here rather than passing unnoticed the way it did before this
  // test existed.
  const expected =
    `This archive holds 1 score - nothing else was in the archive, written ` +
    `${real.exported_at} by Fermata ${real.fermata_version}. Read from a schema version ` +
    `${real.schema_version} archive into this Fermata's own schema version ` +
    `${real.schema_version}.`;
  await expect(page.getByTestId("import-counts")).toHaveText(expected);
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

test("a row a normaliser only tidies is reported before AND after the import, and a clean archive says nothing", async ({
  page,
  request,
}) => {
  // ImportOut.cleaned (#286): the counts above name what arrived, but never
  // said which of those rows a normaliser had to tidy on the way in - a
  // lowercase pitch stored as "E2", say. This is the guaranteed case
  // instruments.normalise makes (see server/tests/test_portability_api.py's
  // own `test_an_archived_instrument_is_imported_with_its_name_and_pitches_cleaned`,
  // which proves the same fact server-side): a POST to /api/instruments
  // already normalises, so no instrument stored through the real API is
  // ever dirty - the only way to get one INTO an archive is to hand-edit a
  // manifest directly, exactly as the newer-tables test above does for
  // trainer_attempts and trainer_chord_attempts.
  //
  // Two manifests, built from nothing rather than downloaded and edited
  // (the "only scores" test's own technique) so applying the dirty one
  // never touches anything already in the shared library: format,
  // schema_version, exported_at and fermata_version are the real export's
  // own, only `tables` is replaced, with a single instruments row and every
  // other table empty.
  await page.goto("/#/settings");
  const downloadPromise = page.waitForEvent("download");
  await exportButton(page).click();
  const download = await downloadPromise;
  const real = JSON.parse(
    readZip(fs.readFileSync(await download.path()))
      .find((e) => e.name === "manifest.json")
      .data.toString("utf-8"),
  );

  const instrumentName = "Data portability cleaning check";
  const baseRow = {
    id: 1,
    owner: "local",
    kind: "string",
    name: instrumentName,
    fretted: 1,
    string_count: 6,
    fret_count: 19,
    capo: 0,
    reference_pitch: 440.0,
    created_at: "2024-01-01T00:00:00",
    updated_at: "2024-01-01T00:00:00",
  };

  // The column holds text written by Python's own `json.dumps` (api.py's
  // create_instrument, and _apply_import's own `_store_cleaned` call, both
  // use its default separators - see server/fermata/api.py) - `", "`
  // between items, not JS's compact `JSON.stringify`. A "clean" row's
  // string_pitches has to be byte-identical to what the normaliser would
  // itself produce, or `_store_cleaned`'s own `row[key] != value` string
  // comparison sees a spacing difference as a change and (wrongly, for the
  // purposes of this test) counts the row as cleaned.
  function pyJsonStringArray(items) {
    return `[${items.map((item) => JSON.stringify(item)).join(", ")}]`;
  }

  function archiveWith(stringPitches) {
    const manifest = {
      ...real,
      tables: {
        ...emptyTables(),
        instruments: [{ ...baseRow, string_pitches: pyJsonStringArray(stringPitches) }],
      },
    };
    return buildZip([
      { name: "manifest.json", data: Buffer.from(JSON.stringify(manifest), "utf-8") },
    ]);
  }

  // Nothing to tidy: every pitch is already the canonical spelling
  // instruments.normalise would store, so `cleaned` is `{}` and no line
  // renders at all - the common case this line must stay silent for.
  const cleanArchive = archiveWith(["E2", "A2", "D3", "G3", "B3", "E4"]);
  await fileInput(page).setInputFiles({
    name: "clean-instrument.zip",
    mimeType: "application/zip",
    buffer: cleanArchive,
  });
  await expect(importPreview(page)).toBeVisible();
  await expect(importCleaned(page)).toHaveCount(0);

  // One pitch spelled lowercase - a row instruments.normalise tidies rather
  // than refuses. Choosing a new file resets this component's own import
  // state (chooseFile calls resetImportState first), so the clean preview
  // above is gone before this one is built.
  const dirtyArchive = archiveWith(["e2", "A2", "D3", "G3", "B3", "E4"]);
  await fileInput(page).setInputFiles({
    name: "dirty-instrument.zip",
    mimeType: "application/zip",
    buffer: dirtyArchive,
  });
  await expect(importPreview(page)).toBeVisible();
  // BEFORE anything is applied - the whole point of a dry run reporting this
  // at all - a person sees the exact row that will be changed.
  await expect(importCleaned(page)).toHaveText(
    "1 instrument row was tidied to the stored form.",
  );

  let insertedId;
  try {
    await page.getByTestId("import-confirm").click();
    await expect(page.getByTestId("import-success")).toBeVisible();
    // AFTER applying, the same receipt - identical wording, since `cleaned`
    // reads the same on a dry run and an applied import (ImportOut.cleaned's
    // own docstring).
    await expect(importCleaned(page)).toHaveText(
      "1 instrument row was tidied to the stored form.",
    );

    const stored = (await (await request.get("/api/instruments")).json()).find(
      (i) => i.name === instrumentName,
    );
    expect(stored, `${instrumentName} never appeared in the library`).toBeTruthy();
    insertedId = stored.id;
    // The value POST would have stored, not the raw one the archive
    // carried - "E2", never "e2".
    expect(stored.string_pitches[0]).toBe("E2");
  } finally {
    if (insertedId !== undefined) {
      await request.delete(`/api/instruments/${insertedId}`);
    }
  }
});
