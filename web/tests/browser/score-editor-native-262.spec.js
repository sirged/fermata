// Issue #262: the note editor edited transcriptions only. A native MusicXML
// score in the library rendered through the same engine, with the same notes,
// and could not be edited at all - TabViewer's canEditNotes() wanted a
// transcription row (`tex != null`) and Viewer.svelte never fetched one for a
// non-PDF score, so `editable` was never even passed.
//
// Measured on main 939a8b3 before any of this was written, by uploading the
// editor fixture through the real /api/upload and opening it: file_type
// "musicxml", data-editor-available "false", zero "Edit notes" buttons, and
// GET /transcription 404. The premise held.
//
// WHY THIS SPEC RUNS AGAINST THE REAL SERVER rather than stubbing /api the way
// every other score-editor*.spec.js does. Two of the things #262 has to prove
// are facts about the DISK, not about the client:
//
//   - the library file is never written. A stub cannot show that: there is no
//     file. Here the score is a real upload, so there is a real path under
//     FERMATA_TEST_LIBRARY_DIR, and its SHA-256 is read straight off the
//     filesystem before the edit and after the save.
//   - the edited row is a real row, written by the real PUT and read back by
//     the real GET, with the real 404 that DELETE answers for a score that has
//     no extraction underneath the edit - which is the exact response the
//     revert path has to treat as SUCCESS rather than as a failure.
//
// The fixture is EDITOR_MUSICXML (fixtures/editor-score.js) uploaded as a
// .musicxml file rather than served as a transcription - the same document
// every other editor spec asserts against, so its note table (ordinal 0 =
// string 1 fret 0, ordinal 3 = string 1 fret 5, ...) applies here unchanged.
// It is uploaded from memory rather than added as a new test-fixtures file:
// the bytes have to be identical to a fixture already in the repo for the hash
// assertion to mean "the file we uploaded", and one copy of them is better
// than two that can drift.
//
// What each assertion would catch, so this file is falsifiable rather than
// merely green:
//   - the viewer keeps rendering the file after a save (the load decision in
//     Viewer.svelte not routing an edited row to `tex`): the post-save and
//     post-reload data-staff-source / fret assertions go red.
//   - a save that writes the library file instead of the row: the SHA-256
//     assertion goes red.
//   - a revert that leaves the edited row behind: the post-revert
//     data-staff-source and the GET-404 assertion go red.
// All three were run as real mutations - see the pull request.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import {
  healStaleDivergence,
  plantStaleDivergence,
  runSeededEdits,
} from "./fixtures/editor-fuzz-driver.js";
import { POLY_MUSICXML } from "./fixtures/editor-poly.js";
import { EDITOR_MUSICXML } from "./fixtures/editor-score.js";

const FOLDER = "Uploads";
const wrap = (page) => page.locator(".wrap").first();
const host = (page) => page.locator(".at-host");
const sourceLine = (page) => page.locator(".staff-source");
const fretInput = (page) => page.locator(".edit-fields input");

// Every test uploads under its own name: the suite shares one server and one
// library for the whole run, so a fixed name would have the second test open
// the first test's already-edited score.
let uploadSeq = 0;

function libraryPath(name) {
  return path.join(process.env.FERMATA_TEST_LIBRARY_DIR, FOLDER, name);
}

// The bytes ON DISK, hashed - not the bytes the API hands back, which a server
// that had rewritten the file would still be able to synthesise from the row it
// wrote. This reads the actual file the scanner indexed.
function fileHash(name) {
  return crypto.createHash("sha256").update(fs.readFileSync(libraryPath(name))).digest("hex");
}

// The scan runs on a background thread, so the row appears a moment after the
// upload responds (the same wait guitar-pro-import.spec.js makes, for the same
// reason).
async function waitForScore(request, name) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const scores = await (await request.get("/api/scores")).json();
    const found = scores.find((s) => s.path.endsWith(name));
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`the uploaded score ${name} never appeared in the library`);
}

async function uploadFixture(request, content = EDITOR_MUSICXML) {
  uploadSeq += 1;
  const name = `native-editor-${uploadSeq}.musicxml`;
  const res = await request.post(`/api/upload?folder=${FOLDER}`, {
    multipart: {
      file: {
        name,
        mimeType: "application/octet-stream",
        buffer: Buffer.from(content, "utf-8"),
      },
    },
  });
  expect(res.ok(), await res.text()).toBe(true);
  const score = await waitForScore(request, name);
  expect(score.file_type, "the fixture was indexed as a native MusicXML score").toBe("musicxml");
  return { name, score };
}

async function openScore(page, id) {
  await page.goto(`/#/score/${id}`);
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true", { timeout: 30_000 });
}

async function selectNote(page, ordinal) {
  const point = await page.evaluate((o) => window.__scoreEditor.headPoint(o), ordinal);
  expect(point, `note ${ordinal} has a clickable head`).toBeTruthy();
  await page.mouse.click(point.x, point.y);
  await expect(wrap(page)).toHaveAttribute("data-editor-selected", String(ordinal));
}

async function setFret(page, ordinal, fret) {
  await selectNote(page, ordinal);
  await fretInput(page).fill(String(fret));
  await fretInput(page).blur();
  await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", String(fret));
}

test.describe("editing a native MusicXML score (#262)", () => {
  test("the file's own notes open in the editor, with no transcription row anywhere", async ({
    page,
    request,
  }) => {
    const { score } = await uploadFixture(request);
    // Nothing has been extracted or edited - the score is its file and nothing
    // else. This is the state that was read-only before #262.
    const before = await request.get(`/api/scores/${score.id}/transcription`);
    expect(before.status()).toBe(404);

    await openScore(page, score.id);
    await expect(sourceLine(page)).toHaveAttribute("data-staff-source", "file");
    await expect(sourceLine(page)).toContainText("Showing the file as it is in your library");
    await expect(wrap(page)).toHaveAttribute("data-editor-available", "true");

    await page.getByRole("button", { name: "Edit notes" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
    await expect(wrap(page)).not.toHaveAttribute("data-editor-error", /.+/);

    // The document the editor opened on is the FILE's, not some other score's:
    // the fixture's eight sounding notes, and ordinal 3 reading string 1 fret 5
    // exactly as fixtures/editor-score.js's table says.
    expect(await page.evaluate(() => window.__scoreEditor.noteCount())).toBe(8);
    await selectNote(page, 3);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-string", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "5");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  test("a fret change saves as an edited row, survives a reload, and never touches the file", async ({
    page,
    request,
  }) => {
    const { name, score } = await uploadFixture(request);
    const hashBefore = fileHash(name);
    // Sanity on the hash helper itself: it is reading the file we uploaded, so
    // a "before equals after" that came from reading the wrong path (or the
    // same cached buffer twice) cannot pass unnoticed.
    expect(hashBefore).toBe(
      crypto.createHash("sha256").update(Buffer.from(EDITOR_MUSICXML, "utf-8")).digest("hex"),
    );

    await openScore(page, score.id);
    await page.getByRole("button", { name: "Edit notes" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
    await setFret(page, 0, 7);

    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");

    // The row is real, is an EDIT (never an extraction), and carries the change.
    const stored = await (await request.get(`/api/scores/${score.id}/transcription`)).json();
    expect(stored.source).toBe("edited");
    expect(stored.format).toBe("musicxml");
    expect(stored.content).toContain("<fret>7</fret>");
    // Rule 11 on what was written, the same guard score-editor.spec.js makes:
    // every <octave> inside MusicXML's 0-9, so a validating consumer takes it.
    expect(stored.content).not.toMatch(/<octave>(1\d|\d\d\d)<\/octave>/);

    // THE FILE IS NOT WRITTEN. Byte-identical on disk, hashed before and after.
    expect(fileHash(name), "the library file was rewritten by the save").toBe(hashBefore);
    // And it still holds the ORIGINAL fret, not the edited one - a hash match
    // would already say this, but this names what would have changed.
    expect(fs.readFileSync(libraryPath(name), "utf-8")).toBe(EDITOR_MUSICXML);

    // The viewer now draws the edit, not the file - the load decision.
    await expect(sourceLine(page)).toHaveAttribute("data-staff-source", "edited");
    await expect(sourceLine(page)).toContainText("Showing your edit of this score");

    // And still does after a full page reload, which is the round trip that
    // matters: this rebuilds from the server's own answer, not from anything
    // the page was still holding.
    await page.reload();
    await openScore(page, score.id);
    await expect(sourceLine(page)).toHaveAttribute("data-staff-source", "edited");
    await page.getByRole("button", { name: "Edit notes" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "7");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    // Unchanged on disk across the reload too.
    expect(fileHash(name)).toBe(hashBefore);
  });

  test("revert throws the edit away and the file's own notes come back", async ({
    page,
    request,
  }) => {
    const { name, score } = await uploadFixture(request);
    const hashBefore = fileHash(name);

    await openScore(page, score.id);
    await page.getByRole("button", { name: "Edit notes" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
    await setFret(page, 0, 9);
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(sourceLine(page)).toHaveAttribute("data-staff-source", "edited");

    await page.getByRole("button", { name: "Revert to the file" }).click();
    await expect(sourceLine(page)).toHaveAttribute("data-staff-source", "file", { timeout: 20_000 });
    await expect(sourceLine(page)).toContainText("Showing the file as it is in your library");
    // No revert control left to press - there is no edit to throw away.
    await expect(page.getByRole("button", { name: "Revert to the file" })).toHaveCount(0);

    // The row is gone from the server, not merely hidden: back to the 404 this
    // score started at. DELETE answering 404 here (no extraction underneath) is
    // the case the revert path has to read as success.
    const after = await request.get(`/api/scores/${score.id}/transcription`);
    expect(after.status()).toBe(404);

    // And the file - which was never written at any point - still renders its
    // own original fret 0 on ordinal 0.
    expect(fileHash(name)).toBe(hashBefore);
    await expect(host(page)).toHaveAttribute("data-score-render-ok", "true", { timeout: 30_000 });
    await expect(wrap(page)).toHaveAttribute("data-editor-available", "true");
    // The revert was pressed WITH THE EDITOR STILL OPEN, which is what a player
    // correcting a mistake actually does - so the session must not be left
    // holding the document that was just thrown away. It re-seeds from what is
    // now on screen: the same ordinal reads the file's own fret 0, not the 9
    // that was saved and deleted, and there is nothing left to undo.
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "false");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "0");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });
});

// The #189 fuzz guard, pointed at a NATIVE score.
//
// #189 proved the positional map holds across N seeded random edits on a
// polyphonic document served as a TRANSCRIPTION. #262 changes where the
// editor's document comes from - the library file's own bytes, read by
// score-render.js's readMusicXml and handed over as text, rather than a row's
// content - so the same document arriving by the new route has to be shown to
// hold the same property, on the real byte path (fetch -> arrayBuffer ->
// decode), not by argument from the old one.
//
// It IS the same document: POLY_MUSICXML, uploaded as a .musicxml file rather
// than served as a row, driven by the same seeded sequence
// (fixtures/editor-fuzz-driver.js, shared with score-editor-fuzz-189.spec.js
// rather than copied) at the same SEED and N. The op draws depend on the
// document, and the document is identical, so this replays #189's exact
// sequence over the file path.
const SEED = Number(process.env.FERMATA_FUZZ_SEED ?? 0x1a2b3c);
const N = Number(process.env.FERMATA_FUZZ_N ?? 60);
const POLY_NOTES = 52;

async function openNativeFuzzEditor(page, request) {
  await page.addInitScript(() => {
    window.__fermataEditorHarness = true;
  });
  const { score } = await uploadFixture(request, POLY_MUSICXML);
  await openScore(page, score.id);
  await expect(wrap(page)).toHaveAttribute("data-editor-available", "true");
  await page.getByRole("button", { name: "Edit notes" }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
  // The editor really is on the FILE's document, at its full note count -
  // a half-read file would otherwise fuzz a shorter score and prove less.
  await expect
    .poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0))
    .toBe(POLY_NOTES);
  await expect
    .poll(() => page.evaluate(() => window.__scoreEditorHarness?.count() ?? 0))
    .toBe(POLY_NOTES);
  return score;
}

test.describe("the fuzz guard over a native MusicXML score (#262)", () => {
  test(`${N} seeded random edits on the uploaded file keep the model and the render identical (seed ${SEED})`, async ({
    page,
    request,
  }) => {
    await openNativeFuzzEditor(page, request);
    const result = await runSeededEdits(page, { seed: SEED, n: N });

    expect(
      result.firstBadStep,
      `per-edit divergence flag went red. seed=${SEED} step=${result.firstBadStep} ` +
        `detail=${JSON.stringify(result.firstBad)}`,
    ).toBe(-1);
    // The sequence actually exercised something - a run that refused
    // everything, or one whose ops never reordered a note, would prove nothing.
    expect(result.applied, "no edit applied - the sequence exercised nothing").toBeGreaterThan(5);
    expect(
      (result.opCounts.voice ?? 0) + (result.opCounts.delete ?? 0),
      "no voice-move or delete was attempted",
    ).toBeGreaterThan(0);
    expect(
      result.audit.divergences,
      `after ${N} edits the re-import diverged from the render. seed=${SEED} ` +
        `divergences=${JSON.stringify(result.audit.divergences)}`,
    ).toEqual([]);
    expect(result.audit.ok).toBe(true);
    expect(result.audit.docCount).toBe(result.audit.renderCount);
    expect(result.renderCount).toBe(result.finalCount);
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  // The crucial half: the guard is not green-by-construction ON THIS INPUT.
  // An INDEPENDENT divergence - the document changed with the re-render skipped,
  // which no edit path can produce - is planted, and the audit is shown to go
  // red and NAME the note. Then a real edit reloads the view and it heals,
  // proving the red was the divergence and not a guard that is always red.
  test("the audit reds and names a note when the file-backed render is left stale", async ({
    page,
    request,
  }) => {
    await openNativeFuzzEditor(page, request);

    const clean = await page.evaluate(() => window.__scoreEditorHarness.audit());
    expect(clean.ok, `baseline audit should be clean: ${JSON.stringify(clean.divergences)}`).toBe(true);

    const planted = await plantStaleDivergence(page);
    expect(planted.changed, "the planted document edit should have applied").toBe(true);
    expect(planted.audit.ok).toBe(false);
    const named = planted.audit.divergences.filter(
      (d) => d.ordinal === 0 && (d.field === "fret" || d.field === "midi"),
    );
    expect(
      named.length,
      `the audit should name ordinal 0's fret/midi divergence, got ${JSON.stringify(planted.audit.divergences)}`,
    ).toBeGreaterThan(0);
    const fretDiv = named.find((d) => d.field === "fret");
    expect(fretDiv.render).toBe(planted.before.fret);
    expect(fretDiv.doc).not.toBe(planted.before.fret);

    const healed = await healStaleDivergence(page);
    expect(healed.applied).toBe(true);
    expect(
      healed.audit.ok,
      `after a real reload the audit should be clean: ${JSON.stringify(healed.audit.divergences)}`,
    ).toBe(true);
  });
});
