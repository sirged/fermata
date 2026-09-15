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
//   - skip an <attributes> child whenever the next bar has one of the same
//     name, rather than merging <staff-details> into it: the six staff tunings
//     go with the deleted bar -> "an attribute the next bar only partly
//     restates is still carried forward" red.
//   - drop applyEdit's rebuild GUARD (let createDocument throw out of it): the
//     session keeps a mutated document it cannot reopen, with Save live over
//     it -> "a bar edit that would leave an unopenable transcription is
//     refused" red.
//   - leave the range extent to refreshSelection's clamp instead of collapsing
//     it: the panel still claims four notes over renumbered survivors -> "a
//     multi-note selection is collapsed by a structural edit" red.
//   - drop the repeat/ending check from deleteMeasureRefusal: the bar goes and
//     its half of the repeat with it -> "a bar carrying a repeat is refused"
//     red.
//   - renumber from 1 rather than from the number the document already started
//     at: an excerpt's bar 40 becomes bar 1 -> "an excerpt keeps the numbers
//     its bars already had" red.
//   - report the sums in <divisions> again: the refusal stops saying how many
//     beats the bar holds -> both refusal tests red.
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

// As openEditor, but waiting only on the MODEL - for a document whose staff
// legitimately draws no note-head bounds (see the unstrung-staff guard, #165).
// Nothing else differs: the same stub, the same edit mode, the same panel.
async function openEditorNoHeads(page, content, expected) {
  await page.addInitScript(() => {
    window.__fermataEditorHarness = true;
  });
  const handle = await stubEditorApi(page, content);
  await page.goto("/#/score/1");
  await page.waitForSelector(".staff-render");
  await page.getByRole("button", { name: "Staff", exact: true }).click();
  await renderedOk(page);
  await page.getByRole("button", { name: "Edit notes" }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
  await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness?.count() ?? 0)).toBe(expected);
  return handle;
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

// The staff description this fixture declares in its opening bar, lifted out so
// the documents below can put it somewhere else.
const TUNING_BLOCK = EDITOR_MUSICXML.match(/<staff-details>[\s\S]*?<\/staff-details>/)[0];

// Bar 2 restates PART of the staff description - its line count, and nothing
// else. A legal document, and the shape that catches a migration which skips a
// whole <attributes> child whenever the next bar has one of the same name: the
// six <staff-tuning> elements live inside bar 1's <staff-details>, and bar 2
// having a <staff-details> of its own is not the same as having them.
const PARTIAL_STAFF_DETAILS = EDITOR_MUSICXML.replace(
  '<measure number="2">',
  '<measure number="2">\n      <attributes><staff-details><staff-lines>6</staff-lines></staff-details></attributes>',
);

// The tuning declared in the LAST bar and nowhere else. Unusual, and legal -
// the model reads a tuning from wherever in the document it is written - and
// the one shape where deleting a bar leaves a document with no tuning at all
// however carefully its attributes are migrated, because there is no later bar
// to migrate them to.
const TUNING_IN_LAST_BAR = EDITOR_MUSICXML.replace(
  TUNING_BLOCK,
  "<staff-details><staff-lines>6</staff-lines></staff-details>",
).replace('<measure number="2">', `<measure number="2">\n      <attributes>${TUNING_BLOCK}</attributes>`);

// Bar 2 opens a repeated section (Rule 15). Deleting it would leave the
// backward repeat that closes the section pointing at the start of the piece.
const FORWARD_REPEAT = EDITOR_MUSICXML.replace(
  '<measure number="2">',
  '<measure number="2">\n      <barline location="left"><bar-style>heavy-light</bar-style>' +
    '<repeat direction="forward"/></barline>',
);

// Bar 2 ends with a plain final barline, which carries no repeat and no ending
// - the common case, and one this must go on allowing.
const FINAL_BARLINE = EDITOR_MUSICXML.replace(
  "</measure>\n  </part>",
  '  <barline location="right"><bar-style>light-heavy</bar-style></barline>\n    </measure>\n  </part>',
);

// An excerpt: the same two bars, numbered as bars 40 and 41 of something
// longer. Nothing in the editor put those numbers there, and nothing in a bar
// edit should take them away.
const EXCERPT_FROM_BAR_40 = EDITOR_MUSICXML.replace('<measure number="1">', '<measure number="40">').replace(
  '<measure number="2">',
  '<measure number="41">',
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
    // Said in beats, not in <divisions>: "1920" means nothing to a reader who
    // does not know this document declares 480 of them to a quarter note.
    expect(warn).toContain("4 quarter-notes");
    expect(warn).toContain("3/4 holds 3");
    expect(warn).not.toContain("1920");

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
    expect(warn).toContain("4 half-notes");
    expect(warn).toContain("2/2 holds 2");
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

  test("an attribute the next bar only partly restates is still carried forward", async ({ page }) => {
    // Bar 2 declares a <staff-details> holding its line count and nothing else.
    // Deleting bar 1 has to merge the six <staff-tuning> elements INTO that
    // element rather than treat the bar as having declared its own.
    await openEditor(page, PARTIAL_STAFF_DETAILS, 8);
    await selectNote(page, 0);
    await deleteBar(page).click();

    await expect.poll(() => measureCount(page)).toBe(1);
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.count())).toBe(4);
    expect(await page.evaluate(() => window.__scoreEditorHarness.stringCount())).toBe(6);
    const after = await modelText(page);
    expect(after.match(/<staff-tuning line=/g)).toHaveLength(6);
    // One <staff-details>, not two: they were merged, not stacked.
    expect(after.match(/<staff-details>/g)).toHaveLength(1);
    expect(after).toContain("<staff-lines>6</staff-lines>");
    await renderedOk(page);
  });

  test("a bar edit that would leave an unopenable transcription is refused, not written", async ({ page }) => {
    // The guard this pins is general, not about tunings: a structural mutator
    // writes into the LIVE document, so a result the model cannot be rebuilt
    // from has already been written by the time that is discovered. The
    // pre-edit text is put back and the edit is refused. Without the guard, the
    // rebuild throws out of applyEdit: the re-render never runs, the dirty flag
    // stays true, and Save stays live over a document that cannot be reopened
    // once it is saved.
    //
    // The staff of this fixture draws no note HEADS - its staff definition
    // carries no <staff-tuning>, so the renderer's unstrung-tab guard (#165)
    // drops the bounds - which is why the selection here is made through the
    // model harness rather than by clicking one. That is a property of this
    // deliberately odd document, not of the behaviour under test: every
    // assertion below is on the document and the panel, both of which work
    // exactly as they do on any other score.
    await openEditorNoHeads(page, TUNING_IN_LAST_BAR, 8);
    const before = await modelText(page);
    expect(before).toContain("<staff-tuning line=");

    // Bar 2 is the last bar, and the only one that declares the tuning - there
    // is no later bar for its attributes to move to, so deleting it leaves a
    // document with no tuning at all: one createDocument refuses to open.
    await page.evaluate(() => window.__scoreEditorHarness.select(4));
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "4");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-index", "1");
    await deleteBar(page).click();

    await expect(wrap(page)).toHaveAttribute("data-editor-warn", /cannot open/);
    // Nothing written, nothing on the undo stack, and Save still not offered.
    expect(await modelText(page)).toBe(before);
    await expect.poll(() => measureCount(page)).toBe(2);
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "false");
    // And the session still holds a document it can read: the tuning is still
    // there, and the model still answers for every note.
    expect(await page.evaluate(() => window.__scoreEditorHarness.stringCount())).toBe(6);
    expect(await everyNote(page)).toHaveLength(8);
    // The selection survived the refusal, on the bar it was made on.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "4");
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-index", "1");
  });

  test("a multi-note selection is collapsed by a structural edit, not carried over it", async ({ page }) => {
    // A range is a span of ORDINALS, and a bar deletion renumbers them. Carried
    // over, the panel would claim a run of notes the player never selected, and
    // the next range operation - a delete above all - would act on them in one
    // undo entry with no warning.
    await openEditor(page, EDITOR_MUSICXML, 8);
    await selectNote(page, 2);
    for (let i = 0; i < 3; i++) await page.keyboard.press("Shift+ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-count", "4");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "2,3,4,5");

    await deleteBar(page).click(); // the anchor's own bar
    await expect.poll(() => measureCount(page)).toBe(1);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-count", "1");
    await expect(wrap(page)).not.toHaveAttribute("data-editor-selected-extent", /.+/);

    // And a Backspace now destroys exactly the one selected note.
    const notesBefore = await everyNote(page);
    await page.keyboard.press("Backspace");
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.restCount())).toBe(1);
    expect(await everyNote(page)).toHaveLength(notesBefore.length - 1);
  });

  test("a bar carrying a repeat is refused, naming it, rather than losing the repeat with the bar", async ({
    page,
  }) => {
    // A <barline> is a child of <measure>, not of <attributes>, so a deletion
    // takes it with the bar. A repeat is a PAIR: delete the half that opens the
    // section and the half that closes it means "repeat from the start of the
    // piece", silently changing the form of every bar before it. Re-anchoring
    // that pair is its own piece of work; this refuses instead of guessing.
    await openEditor(page, FORWARD_REPEAT, 8);
    const before = await modelText(page);
    await selectNote(page, 4);
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-index", "1");

    await deleteBar(page).click();
    const warn = await wrap(page).getAttribute("data-editor-warn");
    expect(warn).toContain("Bar 2");
    expect(warn).toContain("repeat");
    expect(await modelText(page)).toBe(before);
    await expect.poll(() => measureCount(page)).toBe(2);
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
  });

  test("a bar carrying only a plain final barline is still deletable", async ({ page }) => {
    // The refusal above is about repeats and endings, not about barlines: a
    // final double bar carries neither, and a bar with one has to stay
    // deletable or the last bar of every finished transcription is stuck.
    await openEditor(page, FINAL_BARLINE, 8);
    await selectNote(page, 4);
    await deleteBar(page).click();
    await expect.poll(() => measureCount(page)).toBe(1);
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.count())).toBe(4);
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "true");
  });

  test("an excerpt keeps the numbers its bars already had", async ({ page }) => {
    // Nothing in this editor gave these bars their numbers, and a bar edit is
    // not a reason to renumber someone's excerpt from 1 - the panel would start
    // naming a different bar than the page the person is reading from.
    await openEditor(page, EXCERPT_FROM_BAR_40, 8);
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-number", "40");

    await insertBar(page).click();
    await expect.poll(() => measureCount(page)).toBe(3);
    expect(measureNumbers(await modelText(page))).toEqual([40, 41, 42]);
    await expect(wrap(page)).toHaveAttribute("data-editor-bar-number", "40");
  });

  test("a refused meter leaves the control reading the bar's real meter", async ({ page }) => {
    // The <select> holds the value that was refused otherwise, and because the
    // bar did not change, nothing re-applies the binding - so the control goes
    // on reporting a meter the document does not have, through every later
    // selection.
    await openEditor(page, EDITOR_MUSICXML, 8);
    await selectNote(page, 0);
    await timeSelect(page).selectOption("3/4");
    await expect(wrap(page)).toHaveAttribute("data-editor-warn", /Bar 1/);

    await expect(timeSelect(page)).toHaveValue("4/4");
    // Still true after moving the selection somewhere else and back.
    await selectNote(page, 4);
    await expect(timeSelect(page)).toHaveValue("4/4");
    await selectNote(page, 0);
    await expect(timeSelect(page)).toHaveValue("4/4");
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
