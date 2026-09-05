// Issue #226: the note editor's positional map was built from the first
// <part> only, so a document with two <part>s was drawn in full but only its
// first part could be reached - a click on the second part's heads selected
// nothing, and the arrow keys walked ordinals past what the renderer could
// show at all. The chosen fix (the S-appetite option the issue's bet took) is
// to keep createDocument's own docstring's promise: refuse a document with
// more than one part, plainly, the same way it already refuses a non-partwise
// one (document.js ~117).
//
// This file was `editor-multipart-selection-probe` (commit b0d3f2b): three of
// its four tests were red on main because they asserted every drawn note
// stayed reachable. That premise no longer holds once the editor refuses the
// document outright, so they are reworked here into what the refusal actually
// does: shows a plain message and selects nothing, while the VIEWER (not the
// editor) still draws every part read-only - refusing an edit is not the same
// as failing to render.
//
// The fixture is the probe's own two-part TAB document, unchanged: the shape
// Fermata's own emitter writes (a TAB staff, six-string standard tuning,
// <divisions>480</divisions>) duplicated into a second <part>, moved down two
// octaves and onto the two lowest strings so a wrongly-drawn note would be
// obvious. 8 sounding notes per part, 16 in the document - copied into
// web/test-fixtures/editor-two-part-tab.musicxml (original synthetic content,
// nothing borrowed) rather than re-inlined, the same reasoning
// fixtures/multi-part-score.js gives for reading its own fixture off disk.
//
// window.__scoreEditor - the read-only geometry hook the other editor specs
// use (see score-editor.spec.js's header) - is only published once edit mode
// is actually entered (TabViewer.svelte's own $effect gates it on `editMode`).
// A refused document never sets editMode true, so that hook never exists here
// - "the viewer still renders both parts" is checked the same way
// score-multi-part.spec.js (#93) checks it: directly against the drawn SVG,
// not through an editor-only instrumentation surface a refusal never reaches.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect } from "@playwright/test";

import { EDITOR_MUSICXML, stubEditorApi } from "./fixtures/editor-score.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.join(here, "..", "..");
const TWO_PART_MUSICXML = fs.readFileSync(
  path.join(WEB_ROOT, "test-fixtures", "editor-two-part-tab.musicxml"),
  "utf-8",
);
const PART_COUNT = 2;
const REFUSAL_MESSAGE = `The note editor works on one part at a time; this document has ${PART_COUNT}.`;

// The refusal counts <part> elements (document.js's `partCount`), not
// <score-part> entries in <part-list> - a document can legitimately list more
// score-parts than it actually writes as <part>s (or the reverse, an
// under-specified upload), and only the latter is what the editor cannot map.
// Built from EDITOR_MUSICXML - still exactly one <part id="P1">, with a
// second <score-part> appended to <part-list> so it names two parts while
// writing one.
const TWO_SCORE_PART_LIST_MUSICXML = EDITOR_MUSICXML.replace(
  '<score-part id="P1"><part-name>Guitar</part-name></score-part>',
  '<score-part id="P1"><part-name>Guitar</part-name></score-part>\n    <score-part id="P2"><part-name>Ghost</part-name></score-part>',
);

const wrap = (page) => page.locator(".staff-render .wrap");
const host = (page) => page.locator(".staff-render .at-host");

async function renderedOk(page) {
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true", { timeout: 30_000 });
}

async function openStaff(page, content) {
  await stubEditorApi(page, content);
  await page.goto("/#/score/1");
  await page.waitForSelector(".staff-render");
  await page.getByRole("button", { name: "Staff", exact: true }).click();
  await renderedOk(page);
}

// Trimmed the same way score-multi-part.spec.js's own drawnText() is - track
// names come back padded and NBSP-joined for a two-word name, and neither has
// anything to do with a string's identity here.
async function drawnTexts(page) {
  return await page.evaluate(() =>
    [...document.querySelectorAll(".at-host svg text")].map((t) => t.textContent.replace(/ /g, " ").trim()),
  );
}

// Staff lines: the thin horizontal rects alphaTab draws (score-multi-part.spec.js's
// own technique, and its own comment on why height - not colour - is what
// identifies one). A six-string TAB staff draws 6; two of them, unambiguously,
// draw 12 - measured directly against this fixture below, not assumed.
async function staffLineRowCount(page) {
  return await page.evaluate(() => {
    const rects = [...document.querySelectorAll(".at-host svg rect")];
    const lines = rects.filter((r) => Number(r.getAttribute("height")) < 1.3);
    return new Set(lines.map((r) => Number(r.getAttribute("y")).toFixed(1))).size;
  });
}

test.describe("a two-part transcription is refused by the note editor (#226)", () => {
  test("opening it in the editor shows the refusal and selects nothing", async ({ page }) => {
    await openStaff(page, TWO_PART_MUSICXML);
    await page.getByRole("button", { name: "Edit notes" }).click();
    // Never entered edit mode - createDocument threw before enterEdit() got to
    // set editMode true, so the panel that would show editError inline never
    // mounts either (it lives behind `{#if editMode}`). data-editor-error
    // carries the message regardless, the same as it already does for the
    // pre-existing partwise-only refusal - but a guitarist reads the page, not
    // an attribute, so the refusal also has to be actual rendered text (#226
    // follow-up: this used to be a silent no-op, the button just staying
    // enabled with nothing visibly happening).
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "false");
    await expect(wrap(page)).toHaveAttribute("data-editor-error", REFUSAL_MESSAGE);
    await expect(wrap(page)).not.toHaveAttribute("data-editor-selected", /.*/);
    await expect(page.locator(".wrap p.error", { hasText: REFUSAL_MESSAGE })).toBeVisible();
    await expect(page.locator("body")).toContainText(REFUSAL_MESSAGE);
  });

  test("no note is selected after clicking the staff or pressing arrow keys", async ({ page }) => {
    await openStaff(page, TWO_PART_MUSICXML);
    await page.getByRole("button", { name: "Edit notes" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "false");

    // A click on the render surface: with editMode false, the wrap's own
    // onclick is `undefined` (TabViewer.svelte's `editMode ? (e) => selectAt(...) :
    // undefined`), so nothing here can select anything - not the wrong note,
    // nothing at all.
    const box = await host(page).boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await expect(wrap(page)).not.toHaveAttribute("data-editor-selected", /.*/);
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "false");

    // Arrow keys: onKey only lets the editor claim them while editMode is true
    // AND a note or rest is already selected - neither holds here, so every
    // press either falls through to the transport or does nothing; either way
    // no selection appears.
    for (let i = 0; i < 8; i++) await page.keyboard.press("ArrowRight");
    await expect(wrap(page)).not.toHaveAttribute("data-editor-selected", /.*/);
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "false");
  });

  test("the viewer still draws both parts, read-only", async ({ page }) => {
    await openStaff(page, TWO_PART_MUSICXML);
    // Never touches "Edit notes" - this is what the plain viewer draws, which
    // the refusal above is not allowed to withhold (the refusal is the
    // editor's; the viewer is a different consumer of the same document).
    const texts = await drawnTexts(page);
    expect(texts).toContain("Upper");
    expect(texts).toContain("Lower");
    // Measured directly against this fixture (two six-string TAB staves):
    // 12 thin rows with both parts drawn, 6 if only the first one were.
    expect(await staffLineRowCount(page)).toBe(12);
  });

  test("a single-part document still opens in the editor", async ({ page }) => {
    await openStaff(page, EDITOR_MUSICXML);
    await page.getByRole("button", { name: "Edit notes" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
    await expect(wrap(page)).not.toHaveAttribute("data-editor-error", /.*/);
    await expect
      .poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0))
      .toBe(8);
  });

  test("a document with one <part> but two <score-part> entries still opens in the editor", async ({ page }) => {
    // The refusal counts <part> elements, not <part-list>'s <score-part>
    // entries (document.js's partCount) - this document names two parts in
    // its <part-list> but writes only one <part>, so it must open exactly
    // like EDITOR_MUSICXML, unaffected by the extra score-part entry.
    await openStaff(page, TWO_SCORE_PART_LIST_MUSICXML);
    await page.getByRole("button", { name: "Edit notes" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
    await expect(wrap(page)).not.toHaveAttribute("data-editor-error", /.*/);
    await expect
      .poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0))
      .toBe(8);
  });

  test("after the refusal, the viewer still renders both parts", async ({ page }) => {
    // Clicking "Edit notes" and seeing the refusal must not disturb the plain
    // viewer's own render of the document underneath - refusing an EDIT is not
    // the same as failing to render, so the staff stays exactly as the
    // read-only test above measures it (both part names, 12 TAB staff rows),
    // even after the editor has rejected this same document.
    await openStaff(page, TWO_PART_MUSICXML);
    await page.getByRole("button", { name: "Edit notes" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "false");
    await expect(page.locator("body")).toContainText(REFUSAL_MESSAGE);
    await renderedOk(page);
    const texts = await drawnTexts(page);
    expect(texts).toContain("Upper");
    expect(texts).toContain("Lower");
    expect(await staffLineRowCount(page)).toBe(12);
  });
});
