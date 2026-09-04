// Issue #237: README.md and server/fermata/config.py both claim Guitar Pro
// import (`.gp3`-`.gp5`, `.gpx`, `.gp`) is supported, but before this file no
// fixture and no test ever loaded one through the real importer -
// server/tests/test_scanner.py's `.gp` bytes are arbitrary, by its own
// docstring. This spec closes that gap: it uploads a real, original `.gp`
// fixture through the real `/api/upload` endpoint, lets the real scanner pick
// it up, opens it through the real `Viewer.svelte` -> `TabViewer.svelte` ->
// `score-render.js` path (a non-"pdf" `file_type` always routes to
// `TabViewer`, whose `score` prop makes `score-render.js` fetch the raw
// bytes and hand them to `api.load()` - alphaTab's own importer, never a
// re-implementation), and asserts what that importer actually produced:
// the score rendered at all, the right number of bars, the right number of
// notes, and the right tuning.
//
// THE FIXTURE. web/test-fixtures/guitar-pro-import-fixture.gp is original
// content written for this test - a four-bar scale-fragment-and-chord
// sketch, standard 6-string guitar tuning, no lyrics, no borrowed
// arrangement - encoded as alphaTeX in the sibling
// guitar-pro-import-fixture.alphatex (also committed, so the fixture's exact
// origin is in the repo, not just asserted) and turned into a real Guitar Pro
// 7 (.gp) binary with alphaTab 1.8.4's OWN exporter
// (`alphaTab.exporter.Gp7Exporter`, called from Node against the parsed
// alphaTex score) - never a licensed editor, and never hand-authored binary
// bytes. The exact command is in a comment at the top of the .alphatex file.
// Both directions were checked against the real importer/exporter before
// this fixture was committed: alphaTeX -> Score -> Gp7Exporter -> bytes ->
// ScoreLoader.loadScoreFromBytes -> Score came back with identical bars (4),
// beats (13), notes (18) and tuning ([64,59,55,50,45,40], standard EADGBE)
// on both ends, and the title/artist tags ("Guitar Pro import fixture" /
// "Fermata test fixture") round-tripped too.
//
// WHY BAR/NOTE COUNTS ARE READ OFF THE DRAWN SVG, NOT AN INTERNAL HOOK.
// score-render.js exposes no dataset attribute for bar or note counts (only
// score-multi-part.spec.js's SVG-counting approach exists for that), and this
// spec is explicitly about the REAL importer having produced a REAL render -
// a count read from the score model rather than what was actually drawn
// would not catch a renderer that parsed correctly but drew the wrong thing.
// alphaTab draws one bar-number text node per bar in this fixture's short,
// single-system layout ("1  ", "2  ", ... - note the padding, which is why
// bar numbers are matched with trailing whitespace and fret numbers, which
// have none, are matched without it) and one plain digit text node per
// fretted note (its fret number) on the tab staff - both confirmed by
// dumping `.at-host svg text` against this exact fixture before writing the
// assertions below, not assumed from another fixture's shape.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, "..", "..", "test-fixtures", "guitar-pro-import-fixture.gp");
const SCORE_NAME = "guitar-pro-import-fixture.gp";

const EXPECTED_BARS = 4;
const EXPECTED_NOTES = 18;
const EXPECTED_TUNING_TEXT = "Guitar Standard Tuning";

const OWN_PATH = `Uploads/${SCORE_NAME}`;

const host = (page) => page.locator(".at-host");
const playButton = (page) => page.locator(".player button.primary");

async function waitForScore(request, name) {
  // The scan runs in a background thread (see viewer-practice.spec.js's own
  // copy of this wait for the same reason), so the row appears a moment
  // after the upload responds.
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const scores = await (await request.get("/api/scores")).json();
    const found = scores.find((s) => s.path.endsWith(name));
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`the uploaded score ${name} never appeared in the library`);
}

async function uploadFixture(request) {
  const res = await request.post("/api/upload?folder=Uploads", {
    multipart: {
      file: { name: SCORE_NAME, mimeType: "application/octet-stream", buffer: fs.readFileSync(FIXTURE) },
    },
  });
  expect(res.ok(), await res.text()).toBe(true);
  return waitForScore(request, SCORE_NAME);
}

async function openScore(page, id) {
  await page.goto(`/#/score/${id}`);
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true", { timeout: 30_000 });
}

async function drawnText(page) {
  return await page.evaluate(() =>
    [...document.querySelectorAll(".at-host svg text")].map((t) => t.textContent),
  );
}

// One text node per bar, in this fixture's single-system layout - see the
// file header. Matched with trailing whitespace so a plain fret-number digit
// ("0", no padding) can never be miscounted as a bar number.
function barCount(texts) {
  return new Set(texts.filter((t) => /^\d+\s+$/.test(t))).size;
}

// One plain digit text node per fretted note - no padding, unlike a bar
// number, and no other glyph on this tab-plus-notation staff renders as a
// bare digit string.
function noteCount(texts) {
  return texts.filter((t) => /^\d+$/.test(t)).length;
}

test.describe("a real Guitar Pro file loads through the real importer", () => {
  test.beforeEach(async ({ request }) => {
    // The same refusal viewer-practice.spec.js makes: this suite must not be
    // pointed at a real library that already has scores in it.
    const existing = await (await request.get("/api/scores")).json();
    expect(
      existing.filter((s) => s.path !== OWN_PATH),
      "refusing to run: this backend has scores in its library this spec did not put " +
        "there, so it is not the throwaway instance the suite creates",
    ).toEqual([]);
  });

  test("bar count, note count and tuning match the fixture, through the real Guitar Pro importer", async ({
    page,
    request,
  }) => {
    const consoleErrors = [];
    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m.text());
    });
    page.on("pageerror", (e) => consoleErrors.push(String(e)));

    const score = await uploadFixture(request);
    expect(score.file_type).toBe("gp");

    await openScore(page, score.id);

    // The renderer actually drew something, not the two states where
    // data-score-render-ok reads "true" without a real render (see
    // web/test-fixtures/tab-profile-selection.md, "A score that draws
    // nothing") - both the render-ok flag and a nonzero render count.
    await expect(host(page)).toHaveAttribute("data-score-renders", /^[1-9]\d*$/);

    const texts = await drawnText(page);
    expect(barCount(texts), `bar-number text nodes: ${JSON.stringify(texts)}`).toBe(EXPECTED_BARS);
    expect(noteCount(texts), `fret-number text nodes: ${JSON.stringify(texts)}`).toBe(EXPECTED_NOTES);
    expect(texts).toContain(EXPECTED_TUNING_TEXT);

    // The fixture's own alphaTeX staff supports every profile (like the
    // bundled demo, tab-profile-selection.md Scenario 3) - all three
    // buttons are offered and each one still renders something. Matched by
    // .seg button + hasText, the same idiom toolbar-responsive.spec.js and
    // practice-shortcuts.spec.js use for these buttons - their accessible
    // name also carries the keyboard-shortcut hint (e.g. "Notation ((1))"),
    // which a plain getByRole(..., { name: "Notation", exact: true }) never
    // matches.
    expect(await page.locator(".seg button").allTextContents()).toEqual(["Notation", "Tab", "Both"]);
    for (const label of [/^Notation$/, /^Tab$/, /^Both$/]) {
      await page.locator(".seg button", { hasText: label }).click();
      await expect(host(page).locator("svg")).not.toHaveCount(0);
    }

    expect(consoleErrors).toEqual([]);
  });
});
