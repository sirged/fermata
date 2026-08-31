// The note editor (#10), end to end against the real alphaTab render and the
// real editor/document.js parse - only the HTTP transport is stubbed (see
// fixtures/editor-score.js). Every assertion is on state the app publishes onto
// the DOM (data-editor-*) or persists through the save client, never on an
// internal this test reaches into - the one exception being window.__scoreEditor,
// the read-only geometry hook the app exposes so a click can land on a KNOWN
// note (the same window.__ instrumentation pattern ear-training.spec.js uses).
//
// What each mutation would turn red, so this suite is falsifiable, not just
// green:
//   - break the document write (setFret et al. no-op): the fret/pitch never
//     changes on reload -> "change fret" and "undo/redo" go red.
//   - break the positional map (buildNoteOrdinals off by one): clicking a
//     known note selects the wrong one -> "click selects the right note" red.
//   - break the Rule 5 string mirror in the seam: the renderer's mxString stops
//     matching the document's -> data-editor-divergence-ok goes false -> the
//     divergence test red.
//   - break the notation flip: only 8 note bounds instead of 16 -> "both staves
//     drawn" red.
import { test, expect } from "@playwright/test";

import { EDITOR_MUSICXML, UNSTRUNG_MUSICXML, stubEditorApi } from "./fixtures/editor-score.js";

const wrap = (page) => page.locator(".staff-render .wrap");
const host = (page) => page.locator(".staff-render .at-host");

// Waits for a render to have finished (data-score-render-ok is published by
// score-render.js after every successful render).
async function renderedOk(page) {
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true");
}

async function openEditor(page, content) {
  const handle = await stubEditorApi(page, content);
  await page.goto("/#/score/1");
  await page.waitForSelector(".staff-render");
  // Full-width staff, so every note is on screen to click (side-by-side would
  // give the staff half the width). The layout toggle is ScoreCompare's.
  await page.getByRole("button", { name: "Staff", exact: true }).click();
  await renderedOk(page);
  await page.getByRole("button", { name: "Edit notes" }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
  // The notation staff flip re-renders; wait until the geometry hook reports
  // the whole score (8 sounding notes) is laid out before clicking anything.
  await expect
    .poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0))
    .toBe(8);
  return handle;
}

async function selectNote(page, ordinal) {
  const point = await page.evaluate((o) => window.__scoreEditor.headPoint(o), ordinal);
  expect(point, `note ${ordinal} has a clickable head`).toBeTruthy();
  await page.mouse.click(point.x, point.y);
  await expect(wrap(page)).toHaveAttribute("data-editor-selected", String(ordinal));
}

const fretInput = (page) => page.locator(".edit-fields input");
const durationSelect = (page) => page.locator(".edit-fields label", { hasText: "Duration" }).locator("select");

test.describe("note editor", () => {
  test("edit mode is reachable from the score view and draws both staves", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    // 8 sounding notes, each with a tab head AND a notation head = 16 note
    // bounds. Off (notation not shown) it would be 8 - so this is the proof the
    // linked notation staff is actually drawn, not merely asked for.
    await expect
      .poll(() => page.evaluate(() => window.__scoreEditor?.boundsCount() ?? 0))
      .toBe(16);
    await expect(host(page)).toHaveAttribute("data-score-profile", "scoretab");
  });

  test("clicking a note selects that exact note", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    // Note ordinal 2 is string 1, fret 3, id n1-1-2-0 (see the fixture table).
    // Clicking its head must select ordinal 2 and read back exactly that fret,
    // string and id - which only holds if the positional map lines the
    // rendered note up with the same document note.
    await selectNote(page, 2);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "3");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-string", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-note-id", "n1-1-2-0");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  test("changing the fret moves both the tab and the sounding pitch", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    // Note 0: string 1 (E4), fret 0, MIDI 64.
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "64");
    await fretInput(page).fill("5");
    await fretInput(page).blur();
    // fret 5 on the high E string sounds A4 = 69 = 64 + 5.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "5");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "69");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "true");
    // Both staves still drawn after the edit's reload.
    await expect
      .poll(() => page.evaluate(() => window.__scoreEditor?.boundsCount() ?? 0))
      .toBe(16);
  });

  test("moving a note to another string re-pitches it", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    // Note 0: string 1 (E4), fret 0, MIDI 64. Move it to string 2 (B3) at the
    // same fret 0 -> B3 = 59.
    await selectNote(page, 0);
    await page.locator(".edit-fields label", { hasText: "String" }).locator("select").selectOption("2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-string", "2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "59");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  test("changing the duration is applied to the document", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-type", "quarter");
    await durationSelect(page).selectOption("half");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-type", "half");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  test("undo restores the previous value and redo re-applies it", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    await selectNote(page, 0);
    await fretInput(page).fill("7");
    await fretInput(page).blur();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "7");
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "true");

    await page.getByRole("button", { name: "Undo" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "0");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "64");
    await expect(wrap(page)).toHaveAttribute("data-editor-can-redo", "true");

    await page.getByRole("button", { name: "Redo" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "7");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "71");
  });

  test("the divergence guard stays green across several edits", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    await selectNote(page, 3);
    await fretInput(page).fill("4");
    await fretInput(page).blur();
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    await selectNote(page, 5);
    await page.locator(".edit-fields label", { hasText: "String" }).locator("select").selectOption("3");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    await durationSelect(page).selectOption("eighth");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  test("a saved edit persists and is there after a reload", async ({ page }) => {
    const { saved } = await openEditor(page, EDITOR_MUSICXML);
    await selectNote(page, 0);
    await fretInput(page).fill("7");
    await fretInput(page).blur();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "7");

    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    // The persisted MusicXML carries the new fret on that note - stored
    // verbatim through the edited-transcription path.
    await expect.poll(() => saved.content).toContain("<fret>7</fret>");

    // Reload the whole page: the stub now serves the saved (source='edited')
    // content, so re-opening the editor and selecting the same note shows the
    // edit still there.
    await page.reload();
    await page.waitForSelector(".staff-render");
    await page.getByRole("button", { name: "Staff", exact: true }).click();
    await renderedOk(page);
    await page.getByRole("button", { name: "Edit notes" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
    await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(8);
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "7");
  });

  test("the source PDF page sits alongside the staff for correcting an import", async ({ page }) => {
    await stubEditorApi(page, EDITOR_MUSICXML);
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    await page.getByRole("button", { name: "Side by side", exact: true }).click();
    // Both panes visible at once: the PDF to check against, the editable staff
    // to fix - the side-by-side ScoreCompare already provides, which is the
    // "source page alongside while correcting" this increment reuses.
    await expect(page.locator(".panes.side")).toBeVisible();
    await expect(page.locator(".pane.staff-pane")).toBeVisible();
    await expect(page.locator(".pane").first()).toBeVisible();
  });

  test("an under-strung note does not crash the render", async ({ page }) => {
    // A note on a string the staff does not have (issue #165) - the shape a
    // directly uploaded or hand-edited file can carry, which the editor itself
    // never writes. Turning on note bounds for the editor must not reintroduce
    // the paint crash disqualifyUnstrungTabStaves guards against: the staff is
    // withheld and a plain notice shown, not a stack trace.
    await stubEditorApi(page, UNSTRUNG_MUSICXML);
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    await page.getByRole("button", { name: "Staff", exact: true }).click();
    await expect(page.locator(".staff-render .notice")).toBeVisible();
    // The page is alive, not frozen on a thrown render.
    await expect(page.locator(".staff-render .wrap")).toBeVisible();
  });
});
