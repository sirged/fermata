// Bar-scoped editing (#300), end to end against the real alphaTab render and
// the real editor/document.js parse - the same fixtures and stub the earlier
// editor increments use (score-editor.spec.js, fixtures/editor-score.js), only
// the HTTP transport stubbed. Every assertion is on state the app publishes
// onto the DOM (data-editor-*), the read-only renderer hook
// window.__scoreEditor (whose barCount/barInfo are this increment's own
// addition - alphaTab's OWN read of the re-imported bars), the opt-in model
// harness window.__scoreEditorHarness, or the exact MusicXML the model holds -
// never on an internal reached into.
//
// Until now every mutator the document offered was addressed by NOTE ordinal:
// a person could change any note in a bar and nothing about the bar itself.
// This increment adds four measure-addressed ones - the key signature, the
// time signature, inserting a bar and deleting one - each written into the
// selected measure's own <attributes>, each run through the SAME applyEdit the
// note edits use, so undo, redo, the dirty flag, the refusal message and the
// re-render are the ones already there.
//
// The document-model arithmetic is asserted here rather than in tests/unit
// because editor/document.js parses with DOMParser, which the Node-side unit
// specs do not have.
//
// The two decisions this suite exists to pin down, both of them refusals:
//
//   - A time signature the bar's music does not already add up to is REFUSED,
//     naming the bar and both sums. Nothing is re-barred; a person changes the
//     durations first. The check spans the bars the change actually governs -
//     the selected one and every following bar that states no meter of its own
//     and so inherits this one - because Rule 8 is a property of the whole
//     document and this is the edit that could break it three bars away.
//   - The last remaining bar cannot be deleted.
//
// And the one thing a key change must NOT do: re-spell anything. <fifths> is
// written and nothing else, so every note keeps the printed pitch it had.
//
// What each mutation would turn red, so this suite is falsifiable rather than
// green by construction:
//   - drop the Rule 8 sum check from document.js's timeChangeRefusal: the 3/4
//     change over a 4/4 bar applies instead of being refused -> "a time
//     signature the bar's content does not sum to is refused" red.
//   - narrow that check to the selected bar only: nothing here reds (both bars
//     of this fixture hold the same music) - which is why "a bar that inherits
//     the new meter is checked too" builds a document whose SECOND bar is the
//     one that does not fit, and reds on exactly that narrowing.
//   - make setKey also re-spell (recompute pitches against the new fifths):
//     the notes' <step>/<alter> move -> "no note's printed pitch changes" red.
//   - drop the attributes migration from deleteMeasure: deleting the opening
//     bar takes the staff tuning with it and the model cannot be rebuilt ->
//     "deleting the opening bar keeps the transcription openable" red.
//   - drop renumberMeasures after an insert: two bars carry the same number,
//     the ids collide -> "an inserted bar leaves the bars a numbered run" red.
//   - skip the doc rebuild in applyEdit's structural branch: the model still
//     reports the old bar count -> every insert/delete test red.
import { test, expect } from "@playwright/test";

import { EDITOR_MUSICXML, CHORD_MUSICXML, stubEditorApi } from "./fixtures/editor-score.js";

const wrap = (page) => page.locator(".staff-render .wrap");
const host = (page) => page.locator(".staff-render .at-host");
const barFields = (page) => page.locator(".edit-fields.bar-fields");
const keySelect = (page) => barFields(page).locator("label", { hasText: "Key" }).locator("select");
const timeSelect = (page) => barFields(page).locator("select.meter");
const insertBar = (page) => barFields(page).locator("button.bar-insert");
const deleteBar = (page) => barFields(page).locator("button.bar-delete");
const undoButton = (page) => page.locator(".edit-actions button", { hasText: "Undo" });
const saveButton = (page) => page.locator(".edit-actions button.primary");

async function renderedOk(page) {
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true");
}

// Turn the already-loaded score view into a full-width staff in edit mode,
// waiting until `expected` sounding notes are laid out - the barrier every read
// below waits behind. Separate from openEditor so a test can do it again after
// a page reload.
async function enterEditor(page, expected) {
  await page.waitForSelector(".staff-render");
  // Full-width staff, so the whole score is one active pane whose window key
  // handler answers the keyboard (side-by-side would cede the arrows to the PDF
  // pane - see TabViewer's `active` prop).
  await page.getByRole("button", { name: "Staff", exact: true }).click();
  await renderedOk(page);
  await page.getByRole("button", { name: "Edit notes" }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
  await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(expected);
  await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness?.count() ?? 0)).toBe(expected);
}

async function openEditor(page, content, expected) {
  await page.addInitScript(() => {
    window.__fermataEditorHarness = true;
  });
  const handle = await stubEditorApi(page, content);
  await page.goto("/#/score/1");
  await enterEditor(page, expected);
  return handle;
}

async function selectNote(page, ordinal) {
  const point = await page.evaluate((o) => window.__scoreEditor.headPoint(o), ordinal);
  expect(point, `note ${ordinal} has a clickable head`).toBeTruthy();
  await page.mouse.click(point.x, point.y);
  await expect(wrap(page)).toHaveAttribute("data-editor-selected", String(ordinal));
}

const modelText = (page) => page.evaluate(() => window.__scoreEditorHarness.text());
const measureCount = (page) => page.evaluate(() => window.__scoreEditorHarness.measureCount());
const everyNote = (page) =>
  page.evaluate(() => {
    const h = window.__scoreEditorHarness;
    return Array.from({ length: h.count() }, (_, i) => h.noteAt(i));
  });
// The renderer's own read of a bar, not the document's - the independent half
// of every structural assertion below.
const renderedBar = (page, index) => page.evaluate((i) => window.__scoreEditor.barInfo(i), index);

// The two-bar fixture with every note in its SECOND bar twice as long, so the
// two bars hold different amounts of music (1920 and 3840 divisions) - the
// shape a meter change at bar 1 can fit and bar 2 cannot. Bar 2 states no
// attributes of its own, so it inherits whatever bar 1 declares.
const LONG_SECOND_BAR = EDITOR_MUSICXML.replace(
  /<measure number="2">[\s\S]*?<\/measure>/,
  (m) =>
    m
      .replace(/<duration>480<\/duration>/g, "<duration>960</duration>")
      .replace(/<type>quarter<\/type>/g, "<type>half</type>"),
);

// The <measure number=> attributes in document order, as written.
function measureNumbers(xml) {
  return [...xml.matchAll(/<measure number="(-?\d+)"/g)].map((m) => Number(m[1]));
}

// Every note's printed pitch, as written - the thing a key change must leave
// alone. Deliberately read off the TEXT rather than off a descriptor, so it
// fails on the document and not only on the model's read of it.
function printedPitches(xml) {
  return [...xml.matchAll(/<pitch>(.*?)<\/pitch>/gs)].map((m) => m[1].replace(/\s+/g, ""));
}

test.describe("bar-scoped editing", () => {
  test("a key change is written at the selected bar and no note's printed pitch changes", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    const before = await modelText(page);
    const notesBefore = await everyNote(page);
    expect(printedPitches(before)).toHaveLength(8);

    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-index", "0");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-fifths", "0");

    await keySelect(page).selectOption("3");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-fifths", "3");

    const after = await modelText(page);
    expect(after).toContain("<fifths>3</fifths>");
    expect(after).not.toContain("<fifths>0</fifths>");
    // Written ONCE, at the selected bar: the bar after it inherits rather than
    // being re-signed itself.
    expect(after.match(/<fifths>/g)).toHaveLength(1);
    // Not one printed pitch moved - not the letters, not the alterations.
    expect(printedPitches(after)).toEqual(printedPitches(before));
    expect(await everyNote(page)).toEqual(notesBefore);

    // And the RENDER carries it: alphaTab's own read of both bars, the second
    // of which states no key of its own.
    await expect.poll(() => renderedBar(page, 0).then((b) => b.keySignature)).toBe(3);
    await expect.poll(() => renderedBar(page, 1).then((b) => b.keySignature)).toBe(3);
  });

  test("one undo returns the key signature, in a single step", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    const before = await modelText(page);
    await selectNote(page, 0);
    await keySelect(page).selectOption("-2");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-fifths", "-2");
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "true");

    await undoButton(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "false");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-fifths", "0");
    expect(await modelText(page)).toBe(before);
    await expect.poll(() => renderedBar(page, 0).then((b) => b.keySignature)).toBe(0);
  });

  test("an inserted bar appears after the selected one, holding a whole-bar rest per voice", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    const notesBefore = await everyNote(page);
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-count", "2");

    await insertBar(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-count", "3");
    await expect.poll(() => measureCount(page)).toBe(3);
    // The renderer drew three bars too - the insert reached the screen.
    await expect.poll(() => page.evaluate(() => window.__scoreEditor.barCount())).toBe(3);

    // One whole-bar rest, in the one voice the preceding bar is written in,
    // spanning the 4/4 bar: 4 x 480 divisions.
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.restCount())).toBe(1);
    const rest = await page.evaluate(() => window.__scoreEditorHarness.restAt(0));
    expect(rest).toMatchObject({ duration: 1920, voice: 1, measure: 2, type: "whole" });

    // No sounding note was touched - same count, same everything, except the
    // bar NUMBER the notes after the insertion now sit in.
    const notesAfter = await everyNote(page);
    expect(notesAfter).toHaveLength(8);
    expect(notesAfter.map((n) => ({ ...n, measure: null, id: null }))).toEqual(
      notesBefore.map((n) => ({ ...n, measure: null, id: null })),
    );
    expect(notesAfter.map((n) => n.measure)).toEqual([1, 1, 1, 1, 3, 3, 3, 3]);

    // The inserted bar states no attributes of its own, so it inherits the key
    // and meter of the bar before it - which is what the renderer read.
    const drawn = await renderedBar(page, 1);
    expect(drawn).toMatchObject({ beats: 4, beatType: 4, keySignature: 0 });
  });

  test("an inserted bar leaves the bars a numbered run, and their note ids with them", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    expect(measureNumbers(await modelText(page))).toEqual([1, 2]);
    await selectNote(page, 0);
    await insertBar(page).click();
    await expect.poll(() => measureCount(page)).toBe(3);

    const after = await modelText(page);
    expect(measureNumbers(after)).toEqual([1, 2, 3]);
    // Rule 17 ids name a POSITION and carry the bar number, so the bar that
    // moved has had its ids re-derived; the bar that did not move keeps its.
    expect(after).toContain('id="n1-1-0-0"');
    expect(after).toContain('id="n3-1-0-0"');
    expect(after).not.toContain('id="n2-1-3-0"'); // the old bar 2's last note id
  });

  test("the inserted bar can be selected and deleted, leaving the document as it was", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    const before = await modelText(page);

    await selectNote(page, 3); // the last note of bar 1
    await insertBar(page).click();
    await expect.poll(() => measureCount(page)).toBe(3);

    // A bar of pure silence has no note-head to click (the renderer's
    // positional map indexes sounding notes only), so it is reached the way
    // #238 made rests reachable: the arrow key steps onto it.
    await page.keyboard.press("ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest", "0");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-index", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-number", "2");

    await deleteBar(page).click();
    await expect.poll(() => measureCount(page)).toBe(2);
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.restCount())).toBe(0);
    await expect.poll(() => page.evaluate(() => window.__scoreEditor.barCount())).toBe(2);
    // Character for character what it was: the bar numbers and the Rule 17 ids
    // came back with the structure.
    expect(await modelText(page)).toBe(before);
  });

  test("one undo returns an inserted bar, in a single step", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    const before = await modelText(page);
    await selectNote(page, 0);
    await insertBar(page).click();
    await expect.poll(() => measureCount(page)).toBe(3);

    await undoButton(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "false");
    await expect.poll(() => measureCount(page)).toBe(2);
    await expect.poll(() => page.evaluate(() => window.__scoreEditor.barCount())).toBe(2);
    expect(await modelText(page)).toBe(before);
  });

  test("a time signature the bar's content does not sum to is refused, naming the bar and both sums", async ({
    page,
  }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    const before = await modelText(page);
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-beats", "4");

    await timeSelect(page).selectOption("3/4");
    await expect(wrap(page)).toHaveAttribute("data-editor-warn", /Bar 1/);
    const warn = await wrap(page).getAttribute("data-editor-warn");
    // Both sums, in the divisions the document is written in: what the bar
    // holds, and what the meter asks for.
    expect(warn).toContain("1920");
    expect(warn).toContain("1440");
    expect(warn).toContain("3/4");

    // Nothing was written, and nothing was pushed onto the undo stack.
    expect(await modelText(page)).toBe(before);
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "false");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-beats", "4");
    expect(await renderedBar(page, 0)).toMatchObject({ beats: 4, beatType: 4 });
  });

  test("a bar that inherits the new meter is checked too, not only the selected one", async ({ page }) => {
    // Bar 1 holds 1920 divisions, which 2/2 fits exactly. Bar 2 holds twice
    // that and states no meter of its own, so the change at bar 1 governs it -
    // and the refusal names BAR 2, the bar that does not fit, which a check
    // scoped to the selected bar alone could not do.
    await openEditor(page, LONG_SECOND_BAR, 8);
    const before = await modelText(page);
    await selectNote(page, 0);

    await timeSelect(page).selectOption("2/2");
    const warn = await wrap(page).getAttribute("data-editor-warn");
    expect(warn).toContain("Bar 2");
    expect(warn).toContain("3840");
    expect(warn).toContain("1920");
    expect(await modelText(page)).toBe(before);
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "false");
  });

  test("a bar that states its own meter is left out of the check, and out of the change", async ({ page }) => {
    // The same document, except bar 2 declares its own 4/4. MusicXML carries a
    // meter forward only until one is restated, so the change at bar 1 does not
    // reach bar 2 - and bar 2's own arithmetic is therefore none of its
    // business. The decision this pins: write at the selected bar only.
    const content = LONG_SECOND_BAR.replace(
      '<measure number="2">',
      '<measure number="2">\n      <attributes><time><beats>4</beats><beat-type>4</beat-type></time></attributes>',
    );
    await openEditor(page, content, 8);
    await selectNote(page, 0);

    await timeSelect(page).selectOption("2/2");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-beats", "2");
    const after = await modelText(page);
    expect(after.match(/<time>/g)).toHaveLength(2);
    // Bar 2's own meter, untouched.
    await expect.poll(() => renderedBar(page, 1)).toMatchObject({ beats: 4, beatType: 4 });
  });

  test("a time signature the bar's content does fit is applied, and undone in one step", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    const before = await modelText(page);
    const notesBefore = await everyNote(page);
    await selectNote(page, 0);

    // 2/2 is the same 1920 divisions as 4/4, written differently - the change
    // the bar's content does fit.
    await timeSelect(page).selectOption("2/2");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-beats", "2");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-beat-type", "2");
    const after = await modelText(page);
    expect(after).toContain("<beats>2</beats>");
    expect(after).toContain("<beat-type>2</beat-type>");
    expect(after.match(/<time>/g)).toHaveLength(1);
    // The music is untouched: only the meter it is counted against changed.
    expect(await everyNote(page)).toEqual(notesBefore);
    await expect.poll(() => renderedBar(page, 0)).toMatchObject({ beats: 2, beatType: 2 });
    await expect.poll(() => renderedBar(page, 1)).toMatchObject({ beats: 2, beatType: 2 });

    await undoButton(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "false");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-beats", "4");
    expect(await modelText(page)).toBe(before);
  });

  test("the only bar in a transcription cannot be deleted", async ({ page }) => {
    await openEditor(page, CHORD_MUSICXML, 5);
    const before = await modelText(page);
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-count", "1");

    await deleteBar(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-warn", /only bar/);
    await expect.poll(() => measureCount(page)).toBe(1);
    expect(await modelText(page)).toBe(before);
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
  });

  test("deleting the opening bar keeps the transcription openable", async ({ page }) => {
    // The opening bar is the one that declares divisions, key, meter, clef and
    // the staff tuning (Rule 4), and the rest of the document inherits all of
    // them. Deleting it outright would leave a document editor/document.js
    // refuses to open - "no string tuning" - so what it declared moves to the
    // bar that follows it.
    await openEditor(page, EDITOR_MUSICXML, 8);
    await selectNote(page, 0);
    await deleteBar(page).click();

    await expect.poll(() => measureCount(page)).toBe(1);
    // The model still parses, still knows the staff has six strings, and still
    // holds the four notes of what used to be bar 2.
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.count())).toBe(4);
    expect(await page.evaluate(() => window.__scoreEditorHarness.stringCount())).toBe(6);
    const after = await modelText(page);
    expect(after).toContain("<staff-tuning line=");
    expect(after).toContain("<divisions>480</divisions>");
    expect(after).toContain("<beats>4</beats>");
    expect(measureNumbers(after)).toEqual([1]);
    // And the render survived it, with the meter the moved attributes carry.
    await renderedOk(page);
    await expect.poll(() => renderedBar(page, 0)).toMatchObject({ beats: 4, beatType: 4 });
  });

  test("a structurally edited document survives the save and re-import", async ({ page }) => {
    const handle = await openEditor(page, EDITOR_MUSICXML, 8);
    await selectNote(page, 0);
    await keySelect(page).selectOption("3");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-fifths", "3");
    await timeSelect(page).selectOption("2/2");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-beats", "2");
    await insertBar(page).click();
    await expect.poll(() => measureCount(page)).toBe(3);

    await saveButton(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    expect(handle.saved.content).toContain("<fifths>3</fifths>");

    // Re-imported from what was actually persisted, through the same client the
    // app uses - not from the model that was in memory.
    await page.reload();
    await enterEditor(page, 8);
    await expect.poll(() => measureCount(page)).toBe(3);
    const reopened = await page.evaluate(() => window.__scoreEditorHarness.measureAt(0));
    expect(reopened).toMatchObject({ index: 0, number: 1, count: 3, fifths: 3, beats: 2, beatType: 2 });
    await expect.poll(() => renderedBar(page, 0)).toMatchObject({ keySignature: 3, beats: 2, beatType: 2 });
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.restCount())).toBe(1);
  });
});
