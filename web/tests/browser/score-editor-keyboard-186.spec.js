// The note editor's keyboard core loop (#186), end to end against the real
// alphaTab render and the real editor/document.js parse - the same fixture and
// stub the first-increment suite uses (score-editor.spec.js, fixtures/
// editor-score.js), only the HTTP transport stubbed. Every assertion is on
// state the app publishes onto the DOM (data-editor-* / data-score-* /
// data-cursor-*) or the read-only geometry hook window.__scoreEditor, never on
// an internal reached into.
//
// This increment adds, on the shared window key handler beside the transport
// shortcuts: arrows move the SELECTION (note-to-note and bar-to-bar) starting no
// playback; a digit sets the selected note's fret, a quick second digit
// extending it to two digits; Backspace deletes the selected note (to a rest).
// The arbitration under test is that the editor claims those keys ONLY while a
// note is selected, so the number-key staff-profile switch and the transport
// keep working when it is not.
//
// What each mutation would turn red, so this suite is falsifiable, not green by
// construction:
//   - drop the editor's arrow handling: ArrowRight moves the playback cursor
//     instead of the selection -> "arrows move the selection" red (the
//     selection does not change, and data-cursor-tick leaves 0).
//   - drop the digit handling: a digit switches the profile instead of setting
//     the fret -> "a digit sets the fret" and "a digit sets the fret, not the
//     profile" red.
//   - drop the two-digit window: "1" then "2" reads fret 2, not 12 -> the
//     two-digit test red.
//   - drop the writable-pitch bound reuse: a two-digit fret past B9 is written
//     -> "a two-digit fret past the writable bound is refused" red.
//   - drop the Backspace handling (or deleteNote): the sounding-note count
//     stays 8 -> "Backspace deletes the selected note" red.
//   - claim the keys unconditionally (ignore the selection guard): pressing a
//     digit with nothing selected stops switching the profile -> "a number key
//     still switches the staff profile" red.
import { test, expect } from "@playwright/test";

import { EDITOR_MUSICXML, stubEditorApi } from "./fixtures/editor-score.js";

const wrap = (page) => page.locator(".staff-render .wrap");
const host = (page) => page.locator(".staff-render .at-host");
// Scoped to the transport row: the edit panel's Save button is also
// button.primary, so a bare button.primary would match two elements in edit
// mode. This is the Play/Pause button only.
const playButton = (page) => page.locator(".staff-render .player button.primary");

// Longer than the component's own two-digit window (TWO_DIGIT_MS = 600ms in
// TabViewer.svelte) - the pause after which a second digit is a fresh
// single-digit fret rather than an extension of the first.
const PAST_TWO_DIGIT_WINDOW_MS = 900;

async function renderedOk(page) {
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true");
}

async function openEditor(page, content) {
  const handle = await stubEditorApi(page, content);
  await page.goto("/#/score/1");
  await page.waitForSelector(".staff-render");
  // Full-width staff, so the whole score is one active pane whose window key
  // handler answers the keyboard (side-by-side would cede the arrows to the PDF
  // pane - see TabViewer's `active` prop).
  await page.getByRole("button", { name: "Staff", exact: true }).click();
  await renderedOk(page);
  await page.getByRole("button", { name: "Edit notes" }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
  // The notation-staff flip re-renders; wait until the geometry hook reports the
  // whole score (8 sounding notes) is laid out before touching anything.
  await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(8);
  return handle;
}

async function selectNote(page, ordinal) {
  const point = await page.evaluate((o) => window.__scoreEditor.headPoint(o), ordinal);
  expect(point, `note ${ordinal} has a clickable head`).toBeTruthy();
  await page.mouse.click(point.x, point.y);
  await expect(wrap(page)).toHaveAttribute("data-editor-selected", String(ordinal));
}

test.describe("note editor keyboard core loop", () => {
  test("arrow keys move the selection note-to-note and bar-to-bar, starting no playback", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    await selectNote(page, 0);
    // Baseline transport: not playing, and wherever the playback cursor is
    // parked (captured, not assumed - the point under test is that the arrows do
    // not MOVE it, not where it starts).
    await expect(playButton(page)).toHaveText(/Play/);
    const tick0 = await host(page).getAttribute("data-cursor-tick");
    const bar0 = await host(page).getAttribute("data-cursor-bar");

    // ArrowRight -> the next note (ordinal 1, fret 2). The editor claimed the
    // arrow, so the playback cursor did NOT advance and nothing began playing -
    // the same key on the transport would have moved the beat cursor off tick0.
    await page.keyboard.press("ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "2");
    await expect(host(page)).toHaveAttribute("data-cursor-tick", tick0 ?? "0");
    await expect(playButton(page)).toHaveText(/Play/);

    // ArrowLeft -> back to the first note.
    await page.keyboard.press("ArrowLeft");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "0");

    // ArrowDown -> the same position one bar on: measure 2's first note is
    // ordinal 4, on string 2. The transport's own ArrowDown moves a WHOLE BAR of
    // playback cursor (data-cursor-bar off bar0); this must not, since the
    // editor owns the key while a note is selected.
    await page.keyboard.press("ArrowDown");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "4");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-string", "2");
    await expect(host(page)).toHaveAttribute("data-cursor-bar", bar0 ?? "0");

    // ArrowUp -> back to measure 1's first note.
    await page.keyboard.press("ArrowUp");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "0");

    // Still not playing, still parked where it began after all four arrows, and
    // the document and render still agree about the selected note.
    await expect(playButton(page)).toHaveText(/Play/);
    await expect(host(page)).toHaveAttribute("data-cursor-tick", tick0 ?? "0");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  test("typing a digit sets the selected note's fret", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    // Note 0: string 1 (E4), fret 0, MIDI 64.
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "64");
    await page.keyboard.press("5");
    // Fret 5 on the high E string sounds A4 = 69 = 64 + 5.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "5");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "69");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "true");
  });

  test("two digits in quick succession set a two-digit fret", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    // Note 0: string 1 (E4 = 64). "1" then a quick "2" is fret 12, not 1 then 2.
    await selectNote(page, 0);
    await page.keyboard.press("1");
    await page.keyboard.press("2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "12");
    // fret 12 on the high E string sounds E5 = 76 = 64 + 12.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "76");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  test("the two-digit window expiring commits a single-digit fret", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    await selectNote(page, 0);
    await page.keyboard.press("1");
    // The single digit is committed at once.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "1");
    // Past the window, the next digit is a fresh single-digit fret, not an
    // extension to 12.
    await page.waitForTimeout(PAST_TWO_DIGIT_WINDOW_MS);
    await page.keyboard.press("2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "2");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  test("a two-digit fret past the writable bound is refused", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    // Note 4: string 2 (B3 = 59). Fret 9 sounds 68 (writable); a second "9"
    // would make fret 99 = MIDI 158 = octave 12, which MusicXML's <octave> (0-9)
    // cannot express (Rule 11) - the same writable-pitch bound the panel's Fret
    // field enforces. The extension is refused; the committed single digit
    // stays.
    await selectNote(page, 4);
    await page.keyboard.press("9");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "9");
    await page.keyboard.press("9");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "9");
    await expect(wrap(page)).toHaveAttribute("data-editor-warn", /octaves 0–9/);
  });

  test("Backspace deletes the selected note to a rest, re-rendering, guard green", async ({ page }) => {
    const { saved } = await openEditor(page, EDITOR_MUSICXML);
    // Note 2: string 1, fret 3 (G4), id n1-1-2-0.
    await selectNote(page, 2);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "3");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");

    await page.keyboard.press("Backspace");
    // One fewer sounding note: 8 -> 7, proving the delete re-imported and
    // re-rendered rather than only mutating the document.
    await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(7);
    // The note that followed slid into ordinal 2 (old ordinal 3: A4, fret 5),
    // still selected, and the document and render still agree about it (guard
    // green after the operation).
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "5");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "true");

    // Persisted as a rest through the edited-transcription path - the model's
    // own notion of delete (the note kept its place as silence).
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    await expect.poll(() => saved.content).toContain("<rest");
  });

  test("a number key still switches the staff profile when no note is selected", async ({ page }) => {
    await stubEditorApi(page, EDITOR_MUSICXML);
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    await page.getByRole("button", { name: "Staff", exact: true }).click();
    await renderedOk(page);
    // Not in edit mode, nothing selected: the editor claims no keys, so the
    // digits drive the staff-profile switch exactly as they did before #186.
    // ("2" and "3" - this score's tab has no separate notation-only profile, so
    // its options are tab and scoretab; "1" would be a no-op here.)
    await page.keyboard.press("2");
    await expect(host(page)).toHaveAttribute("data-score-profile", "tab");
    await page.keyboard.press("3");
    await expect(host(page)).toHaveAttribute("data-score-profile", "scoretab");
  });

  test("a number key sets the fret, not the profile, when a note is selected", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    // Edit mode forces the both-staves profile; hold that fixed as the control.
    await expect(host(page)).toHaveAttribute("data-score-profile", "scoretab");
    await selectNote(page, 0);
    // "2" is the staff-profile "tab" key when nothing is selected - so if the
    // digit leaked through with a note selected, the profile would flip to
    // "tab". It must instead set the fret and leave the profile alone.
    await page.keyboard.press("2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "2");
    await expect(host(page)).toHaveAttribute("data-score-profile", "scoretab");
  });

  test("the transport still plays while a note is selected", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML);
    await selectNote(page, 0);
    // Editing does not disable playback: the transport is live with a note
    // selected - Play starts it and Pause stops it (a correction can be heard
    // the moment it is made), and the selection survives the round trip. (Driven
    // through the Play control, not the Space key: after clicking a note the
    // focus rests on a toolbar button, where Space is that button's own
    // activation - the pre-existing #92 rule this feature deliberately leaves
    // alone. What target 5 pins is that the transport stays enabled and works
    // mid-edit, which the control exercises directly.)
    await expect(playButton(page)).toBeEnabled();
    await playButton(page).click();
    await expect(playButton(page)).toHaveText(/Pause/, { timeout: 10_000 });
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "0");
    await playButton(page).click();
    await expect(playButton(page)).toHaveText(/Play/);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "0");
  });
});
