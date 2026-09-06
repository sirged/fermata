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
const metronomeButton = (page) => page.locator('button:has-text("Metronome")');
const baseNote = (page) => page.locator(".metronome-base");

// The same eight-note fixture with a real printed tempo on bar one - a
// <metronome> mark AND the <sound tempo> alphaTab reads, which is how
// fixtures/metronome-score.js writes one. 92 rather than 120 so it cannot be
// confused with the renderer's own fallback, which is what "assumed 120 bpm"
// reports.
const MARKED_TEMPO = 92;
const TEMPO_MUSICXML = EDITOR_MUSICXML.replace(
  "      </attributes>\n",
  `      </attributes>
      <direction placement="above">
        <direction-type>
          <metronome><beat-unit>quarter</beat-unit><per-minute>${MARKED_TEMPO}</per-minute></metronome>
        </direction-type>
        <sound tempo="${MARKED_TEMPO}" />
      </direction>
`,
);

// Every upload gets its own name: the suite shares one server and one library
// for the WHOLE run, so a fixed name would have the second test open the first
// test's already-edited score.
//
// A per-module counter is NOT enough, and the reason is worth writing down
// because it cost a red run to find. Playwright starts a FRESH WORKER after a
// failing test, which re-imports this module and resets any counter in it - so
// a later test re-used an earlier one's filename, waitForScore matched the row
// that name already had, and the page opened a score whose content was not the
// one just uploaded (seen as "expected 52 notes, received 8" during the
// mutation runs, where a deliberate failure caused exactly that restart). A
// random name cannot collide across a restart.
function uniqueName() {
  return `native-editor-${crypto.randomUUID()}.musicxml`;
}

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
  const name = uniqueName();
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

// The whole suite shares ONE server and ONE throwaway library for the entire
// run, and this file is the only editor spec that puts real scores in it. Left
// there, its uploads would be sitting in the library when every later
// spec makes its own "this is the throwaway instance, not a real library"
// refusal check - viewer-practice.spec.js, zz-library-missing.spec.js,
// instruments.spec.js and others - and those specs would go red on scores they
// never created. Measured: without this hook, running this file and then
// viewer-practice.spec.js failed six of viewer-practice's tests at its
// beforeEach. Same two-step as guitar-pro-import.spec.js's own cleanup: a
// DELETE only moves a score to the trash, so the trash has to be emptied too
// or the row is still there to be counted.
async function emptyTheLibrary(request) {
  for (const score of await (await request.get("/api/scores")).json()) {
    await request.delete(`/api/scores/${score.id}`);
  }
  for (const score of await (await request.get("/api/trash")).json()) {
    await request.delete(`/api/trash/${score.id}`);
  }
}

test.afterEach(async ({ request }) => {
  await emptyTheLibrary(request);
});

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

  // The tempo the metronome is a percentage OF keeps the reader's own word for
  // it across a save.
  //
  // "marked" is the only word the metronome uses that claims a number was read
  // off a page (Metronome.svelte's tempoUnverified), and this file really does
  // print one: <sound tempo="92"> on bar one, written by whoever exported it.
  // TabViewer used to decide between "marked" and "transcribed" from `tex !=
  // null` - "the notation arrived through a transcription row" - which was a
  // sound proxy only while the sole way to get a row was to extract one from a
  // scanned page. #262 makes an edit of a native file a row too, so one saved
  // fret change flipped this score's honest "marked ♩ = 92" into "● transcribed
  // ♩ = 92", carrying the unverified mark and an aria-label saying the number
  // came from a transcription rather than a printed marking. Nothing about the
  // reader's own file changed; only where the notes were being served from.
  //
  // WOULD CATCH: staffSource not reaching TabViewer, or TabViewer going back to
  // reading `tex` for this - the post-save half goes red on "transcribed" and
  // on the mark. (Reverted to the `tex != null` proxy by hand, it did: see the
  // mutation table on the pull request.)
  test("a printed tempo is still called marked after the file has been edited", async ({
    page,
    request,
  }) => {
    expect(TEMPO_MUSICXML, "the tempo direction was not injected").not.toBe(EDITOR_MUSICXML);
    const { score } = await uploadFixture(request, TEMPO_MUSICXML);

    await openScore(page, score.id);
    await expect(sourceLine(page)).toHaveAttribute("data-staff-source", "file");

    // Before any edit: the file's own marking, in the file's own words.
    await metronomeButton(page).click();
    await expect(baseNote(page)).toContainText(String(MARKED_TEMPO));
    await expect(baseNote(page)).toHaveText(/marked/);
    await expect(baseNote(page)).not.toContainText("transcribed");
    await expect(baseNote(page).locator(".mark")).toHaveCount(0);

    // One fret change, saved - so the notation now arrives through an edited
    // row instead of the file's bytes.
    await page.getByRole("button", { name: "Edit notes" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
    await setFret(page, 0, 7);
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    await expect(sourceLine(page)).toHaveAttribute("data-staff-source", "edited");

    // The stored document still carries the marking - so a "transcribed" here
    // would be a claim about provenance, not a side effect of the tempo having
    // gone missing (which reads "assumed 120 bpm" instead, and would pass a
    // bare not-toContainText assertion).
    const stored = await (await request.get(`/api/scores/${score.id}/transcription`)).json();
    expect(stored.source).toBe("edited");
    expect(stored.content).toContain(`<sound tempo="${MARKED_TEMPO}"`);

    // And the word is unchanged: the reader's file printed 92, and an edit of
    // that file is not a transcription of a scanned page.
    await expect(baseNote(page)).toContainText(String(MARKED_TEMPO));
    await expect(baseNote(page)).toHaveText(/marked/);
    await expect(baseNote(page)).not.toContainText("transcribed");
    await expect(baseNote(page).locator(".mark")).toHaveCount(0);
  });

  // A lookup that FAILED is not a score with no edit.
  //
  // The lookup's .catch used to land every status on staffEdit = null, which
  // the panel states as "Showing the file as it is in your library." and which
  // left the editor open over the file. With a stored edit sitting on the
  // server that is a false statement AND a trap: the editor seeds from the
  // file, and the reader's next Save writes that document over the edit they
  // cannot see. The page now says it could not tell, offers a retry, and
  // refuses to open the editor at all until the lookup has landed.
  //
  // WOULD CATCH: swallowing the failure again (any catch that resolves to
  // "ready"). Done by hand - see the mutation table on the pull request.
  test("a lookup the server will not answer says so, and offers no editor", async ({
    page,
    request,
  }) => {
    const { score } = await uploadFixture(request);
    // A REAL stored edit, written through the real PUT - the thing that would
    // be silently overwritten. It also makes has_transcription true, which is
    // what makes the viewer ask for the row at all.
    const put = await request.put(`/api/scores/${score.id}/transcription`, {
      data: { content: EDITOR_MUSICXML.replace("<fret>0</fret>", "<fret>7</fret>") },
    });
    expect(put.ok(), await put.text()).toBe(true);

    // Only the row lookup is broken; the score itself still loads, which is
    // exactly the shape of a transient server-side failure.
    await page.route("**/api/scores/*/transcription", (route) =>
      route.request().method() === "GET"
        ? route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' })
        : route.fallback(),
    );

    await openScore(page, score.id);
    await expect(sourceLine(page)).toHaveAttribute("data-staff-source", "unknown");
    await expect(sourceLine(page)).toContainText("Could not tell whether this score has an edit stored");
    // Neither of the two claims it is not entitled to make.
    await expect(sourceLine(page)).not.toContainText("Showing the file as it is in your library");
    await expect(sourceLine(page)).not.toContainText("Showing your edit of this score");

    // No editor, so nothing can be saved over the row that is really there.
    await expect(wrap(page)).toHaveAttribute("data-editor-available", "false");
    await expect(page.getByRole("button", { name: "Edit notes" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Save", exact: true })).toHaveCount(0);

    // A way out that is not "reload the page and hope".
    const retry = page.getByRole("button", { name: "Try again" });
    await expect(retry).toHaveCount(1);
    await page.unroute("**/api/scores/*/transcription");
    await retry.click();
    // The retry really re-asks, and the stored edit - which was there the whole
    // time - is what comes back.
    await expect(sourceLine(page)).toHaveAttribute("data-staff-source", "edited");
    await expect(wrap(page)).toHaveAttribute("data-editor-available", "true");
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
