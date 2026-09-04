// The score toolbar's fit at real tablet and phone widths (issue #106).
//
// Every practice control lives in TabViewer's `.toolbar`: play, tempo, loop,
// metronome, count-in, the profile switch, the theme picker. Below ~869px
// (834 and 768 are ordinary portrait-tablet widths - the project's own
// stated primary form factor, a tablet on a music stand) the row used to
// hold its intrinsic width and let flex-shrink squeeze individual controls
// instead of moving anything to a new line.
//
// That shrinking hit `.seg` (the profile switch) hardest, because `.seg` has
// its own `overflow: hidden` for its rounded corners: shrunk below its
// buttons' combined width, the buttons it could not show were not merely
// squeezed, they were clipped away entirely - present in the DOM, invisible,
// and (this is the part a "does the element exist" assertion would miss)
// UNREACHABLE by touch or mouse, because overflow:hidden removes clipped
// content from hit-testing along with painting. At 430 the whole toolbar
// stopped fitting even after every control had shrunk as far as it could,
// and it overflowed the page itself, taking Metronome/Count-in/Ladder off
// the right edge with no way to reach them on a device with no horizontal
// scrollbar to grab.
//
// The fix (TabViewer.svelte) is to stop shrinking anything and let the row
// wrap instead, cascading through `.toolbar` -> `.player` -> `.practice` as
// each one, not something inside it, stops fitting - vertical space is what
// a stand has to spare, and reach for a clipped or off-screen control is
// what it does not. Below 900px, `.player` (Play/Stop/Speed/Loop/
// Metronome/Count-in/Ladder - what a player actually reaches for mid-piece)
// is also pulled to its own row, first, ahead of the profile switch and
// theme picker - see the CSS comment beside `@media (max-width: 900px)` in
// TabViewer.svelte for why those two, and not the transport row, are the
// ones asked to move.
//
// This file checks two different things per width, deliberately not
// conflated into one assertion:
//   - CLIPPING: nothing in the toolbar (or, in gig mode, the HUD) is cut off
//     by an ancestor's overflow, and the page itself never grows a
//     horizontal scrollbar.
//   - REACHABILITY, at 768 and 430 specifically: every control actually
//     responds to a real click, not merely present in the DOM. A control
//     sitting at a real x/y inside the viewport but clipped by an ancestor's
//     `overflow: hidden` would pass an `toBeAttached()`/`toBeVisible()`
//     check (Playwright's visibility check does not account for an
//     ancestor's clipping) while still being exactly as unreachable as one
//     hidden behind an unopenable disclosure - only a real `.click()`,
//     which fails when the target point does not actually hit the element,
//     tells the two apart.
import { expect, test } from "@playwright/test";

import { WIDTHS, clippingAudit, tap } from "./responsive-audit.js";

/** Loads the bundled demo score, which - unlike most real library scores -
 * supports all three profiles on one staff (alphaTeX's default staff shows
 * both standard notation and tablature), so the segmented switch renders at
 * its full three-button width and the toolbar is exercised at the same
 * intrinsic width the issue measured. No library or upload needed. */
async function openDemo(page) {
  await page.goto("/#/demo");
  await page.waitForSelector(".toolbar .seg button", { timeout: 15_000 });
}

async function enterGigMode(page) {
  // Demo mode's header has no "Gig mode" button (see Viewer.svelte: the
  // header's .controls block only renders for a real, non-demo score) - the
  // keyboard shortcut is the only way in, which Viewer.svelte wires up
  // regardless of demo status.
  await page.keyboard.press("f");
  await page.waitForSelector(".gig-hud", { timeout: 5_000 });
}

// clippingAudit and tap moved to ./responsive-audit.js (issue #8), so the
// library header and Viewer controls row checks in this file's own new
// describe block below, and in zzzzzz-score-metadata.spec.js, can share
// them rather than re-deriving the same two functions. See that file's own
// header comment for the reachability reasoning behind `tap` - unchanged
// from what stood here before the move.

for (const width of WIDTHS) {
  test.describe(`at ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });

    test(`the toolbar has zero clipping and the page never overflows horizontally`, async ({ page }) => {
      await openDemo(page);
      const audit = await clippingAudit(page, ".toolbar");
      expect(audit.rootMissing, ".toolbar was not found at all").toBeFalsy();
      expect(audit.clipped, `clipped controls: ${JSON.stringify(audit.clipped)}`).toEqual([]);
      expect(audit.pageOverflow, "page grew a horizontal scrollbar").toBe(0);
    });

    // Checked first, per the issue: gig mode is the layout most likely to be
    // running on a stand, since it is what a player actually performs from.
    test(`gig mode's HUD has zero clipping and the page never overflows horizontally`, async ({ page }) => {
      await openDemo(page);
      await enterGigMode(page);
      const audit = await clippingAudit(page, ".gig-hud");
      expect(audit.rootMissing, ".gig-hud was not found at all").toBeFalsy();
      expect(audit.clipped, `clipped controls: ${JSON.stringify(audit.clipped)}`).toEqual([]);
      expect(audit.pageOverflow, "page grew a horizontal scrollbar").toBe(0);
    });
  });
}

test.describe("every toolbar control is reachable by an actual click, not merely present", () => {
  for (const width of [768, 430]) {
    test(`at ${width}px`, async ({ page }) => {
      test.setTimeout(60_000);
      await page.setViewportSize({ width, height: 900 });
      await openDemo(page);

      // The profile switch. All three buttons must be individually
      // reachable and actually change what's highlighted - not just
      // present in `.seg`'s DOM, which is exactly what stayed true while
      // `.seg`'s own overflow:hidden clipped "Tab" and "Both" away.
      const notation = page.locator(".seg button", { hasText: "Notation" });
      const tab = page.locator(".seg button", { hasText: /^Tab$/ });
      const both = page.locator(".seg button", { hasText: "Both" });
      await tap(page, tab, "Tab");
      await expect(tab).toHaveClass(/on/);
      await tap(page, both, "Both");
      await expect(both).toHaveClass(/on/);
      await tap(page, notation, "Notation");
      await expect(notation).toHaveClass(/on/);
      // Back to "Both" - the rest of this test's transport checks want both
      // notation and tab staves available for the fullest toolbar width.
      await tap(page, both, "Both");
      await expect(both).toHaveClass(/on/);

      // The theme picker - present, but never claimed as one of the
      // controls that must sit in the top row; still has to be reachable.
      const themePicker = page.locator("select.theme-picker");
      await tap(page, themePicker, "theme picker");
      await themePicker.selectOption("noir");
      await expect(themePicker).toHaveValue("noir");

      // Play, Speed and Loop: what a player reaches for mid-piece, and the
      // three controls this fix keeps on the first, un-buried row below the
      // wrap breakpoint.
      const play = page.locator(".player button.primary");
      await expect(play).toBeEnabled({ timeout: 20_000 });
      await tap(page, play, "Play");
      await expect(play).toHaveText(/Pause/);
      const stop = page.locator(".player button", { hasText: "■" });
      await tap(page, stop, "Stop");
      await expect(play).toHaveText(/Play/);

      const speed = page.locator(".player > select");
      await tap(page, speed, "speed");
      await speed.selectOption("1.25");
      await expect(speed).toHaveValue("1.25");

      const loop = page.locator(".practice button", { hasText: "Loop" });
      await tap(page, loop, "Loop");
      await expect(loop).toHaveClass(/on/);

      // Metronome, Count-in, Ladder: not claimed as always-visible, but the
      // fix wraps rather than hides, so they still have to be reachable.
      const metronome = page.locator(".practice button", { hasText: "Metronome" });
      await tap(page, metronome, "Metronome");
      await expect(metronome).toHaveClass(/on/);

      const countIn = page.locator(".practice button", { hasText: "Count-in" });
      await tap(page, countIn, "Count-in");
      await expect(countIn).toHaveClass(/on/);

      const ladder = page.locator(".practice button", { hasText: "Ladder" });
      await tap(page, ladder, "Ladder");
      await expect(ladder).toHaveClass(/on/);
      // Ladder's own follow-on controls (Start/Step/Target) must be
      // reachable too, not just the button that reveals them.
      const ladderStart = page.locator(".ladder-controls input").first();
      await expect(ladderStart).toBeVisible();
      await tap(page, ladderStart, "ladder start");
      await ladderStart.fill("70");
      await expect(ladderStart).toHaveValue("70");

      // Restore the theme picker's own selection before finishing: staff_theme
      // is a server-side setting (server/fermata/api.py), shared by the whole
      // suite rather than scoped to this test or even this file - leaving it
      // on "noir" here leaked into whatever spec happened to run next and read
      // the rendered staff colour (measured: score-multi-part.spec.js's
      // staff-line count reading 0 instead of 10 when it ran after this one).
      await tap(page, themePicker, "theme picker");
      await themePicker.selectOption("parchment");
      await expect(themePicker).toHaveValue("parchment");
    });
  }
});

test("below the wrap breakpoint, the transport row (Play/Speed/Loop) renders above the profile switch and theme picker", async ({
  page,
}) => {
  // A placement check, not just a presence one - see the design note atop
  // this file for why Play/Speed/Loop are the row asked to move first, not
  // the profile switch or the theme picker.
  //
  // Comparing box TOPS alone is not enough to prove this: pre-fix, `.seg`
  // and `.player` sit in the very same unwrapped row, and `align-items:
  // center` alone was enough to put `.player`'s top a few pixels ABOVE
  // `.seg`'s - `.player` was simply the taller item that row (its own
  // "Count-in" button had wrapped onto two lines), not a different row at
  // all. Confirmed by hand against the pre-fix build - a bare `.y <
  // .y` comparison passed there too, for the wrong reason entirely, which
  // is exactly the kind of test this project does not want. What actually
  // distinguishes "two separate rows" from "one row, uneven heights" is
  // that the rows do not vertically OVERLAP at all - `.player`'s bottom
  // edge sits above `.seg`'s top edge, not just its center.
  await page.setViewportSize({ width: 768, height: 900 });
  await openDemo(page);
  const playerBox = await page.locator(".toolbar .player").boundingBox();
  const segBox = await page.locator(".toolbar .seg").boundingBox();
  expect(playerBox, ".player did not render").not.toBeNull();
  expect(segBox, ".seg did not render").not.toBeNull();
  expect(
    playerBox.y + playerBox.height,
    `.player (bottom ${playerBox.y + playerBox.height}) must not vertically overlap .seg (top ${segBox.y}) - they have to be on separate rows, not just centered unevenly within one`,
  ).toBeLessThanOrEqual(segBox.y);
});

// The library's own filter row (issue #8) - a second row that stopped
// fitting for the same reason .toolbar did above: two more selects
// (key-filter, difficulty-filter) added to a row that already held the
// search box, two selects, Organise and the result count, with nothing in
// it able to shrink to fit either. Fixed the same way - wrap, not shrink -
// and checked the same two ways: no clipping and no page overflow at every
// width, every control reachable by a real click at the two narrowest ones.
// No library or upload needed: `header.filter-row` renders unconditionally,
// with zero scores, so this needs no upload and therefore cannot disturb
// zz-library-missing.spec.js's own high-water-mark calibration the way
// leaving rows behind in the library would (see
// zzzzzz-score-metadata.spec.js's header comment for that story in full).
test.describe("the library filter row has zero clipping and the page never overflows horizontally", () => {
  for (const width of WIDTHS) {
    test(`at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/#/");
      await page.waitForSelector("header.filter-row");
      const audit = await clippingAudit(page, "header.filter-row");
      expect(audit.rootMissing, "header.filter-row was not found at all").toBeFalsy();
      expect(audit.clipped, `clipped controls: ${JSON.stringify(audit.clipped)}`).toEqual([]);
      expect(audit.pageOverflow, "page grew a horizontal scrollbar").toBe(0);
    });
  }
});

test.describe("every library filter control is reachable by an actual click, not merely present", () => {
  for (const width of [768, 430]) {
    test(`at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/#/");
      await page.waitForSelector("header.filter-row");

      const search = page.locator("header.filter-row .search");
      await tap(page, search, "search");
      await search.fill("a probe that matches nothing");
      await expect(search).toHaveValue("a probe that matches nothing");
      await search.fill("");

      const keyFilter = page.locator("select.key-filter");
      await tap(page, keyFilter, "key filter");
      await keyFilter.selectOption("2");
      await expect(keyFilter).toHaveValue("2");
      await keyFilter.selectOption("");

      const difficultyFilter = page.locator("select.difficulty-filter");
      await tap(page, difficultyFilter, "difficulty filter");
      await difficultyFilter.selectOption("3");
      await expect(difficultyFilter).toHaveValue("3");
      await difficultyFilter.selectOption("");

      const organise = page.locator("button.organise-toggle");
      await tap(page, organise, "Organise");
      await expect(organise).toHaveText(/Done organising/);
      // Left the way it was found - "organising" is client-only state, but a
      // test that leaves the page in it is still a worse citizen than one
      // that does not.
      await tap(page, organise, "Done organising");
      await expect(organise).toHaveText(/^Organise$/);
    });
  }
});
