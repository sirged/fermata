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

const WIDTHS = [1280, 1024, 834, 768, 430];

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

/** Audits every button/select/input under `selector` for two kinds of
 * overflow: the page growing a horizontal scrollbar, and a control being
 * clipped away by some ancestor's non-visible overflow (the `.seg` failure
 * mode above) even though the page itself never overflowed. Returns the
 * clipped fraction (0 = fully visible, 1 = entirely clipped) for anything
 * more than 40% cut off, by name, so a failure says which control and by
 * how much rather than just "something overflowed". */
async function clippingAudit(page, selector) {
  return page.evaluate((sel) => {
    function clippedFraction(node) {
      const rect = node.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return 1;
      let visible = { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
      let ancestor = node.parentElement;
      while (ancestor) {
        const cs = getComputedStyle(ancestor);
        if (cs.overflow !== "visible" || cs.overflowX !== "visible" || cs.overflowY !== "visible") {
          const ar = ancestor.getBoundingClientRect();
          visible = {
            left: Math.max(visible.left, ar.left),
            right: Math.min(visible.right, ar.right),
            top: Math.max(visible.top, ar.top),
            bottom: Math.min(visible.bottom, ar.bottom),
          };
        }
        ancestor = ancestor.parentElement;
      }
      visible.left = Math.max(visible.left, 0);
      visible.top = Math.max(visible.top, 0);
      visible.right = Math.min(visible.right, window.innerWidth);
      visible.bottom = Math.min(visible.bottom, window.innerHeight);
      const visArea = Math.max(0, visible.right - visible.left) * Math.max(0, visible.bottom - visible.top);
      const fullArea = rect.width * rect.height;
      return fullArea === 0 ? 1 : 1 - visArea / fullArea;
    }

    const root = document.querySelector(sel);
    if (!root) return { pageOverflow: 0, clipped: [], rootMissing: true };
    const clipped = [];
    for (const el of root.querySelectorAll("button, select, input")) {
      const frac = clippedFraction(el);
      if (frac > 0.4) {
        clipped.push({ text: (el.textContent || el.tagName).trim().slice(0, 24), clippedFraction: frac });
      }
    }
    return {
      pageOverflow: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
      clipped,
    };
  }, selector);
}

/** The real reachability check, and the reason it does not simply call
 * Playwright's own `locator.click()`.
 *
 * `.seg`'s own `overflow: hidden` clips "Tab" and "Both" away below the wrap
 * breakpoint (pre-fix) hard enough that `document.elementFromPoint()` at
 * their own geometric center resolves to the theme-picker select sitting
 * next to them instead - confirmed by hand against the pre-fix build. A
 * real fingertip tapping that spot would hit the select, not the button
 * underneath. Playwright's `locator.click()`, though, still succeeded and
 * still fired the button's handler in that exact state - its own
 * actionability check is more forgiving of this particular clip than a real
 * tap is, which makes it the wrong tool here: it is one of the "passes
 * against both the working and broken layout" tests this project has
 * shipped before.
 *
 * So this asserts the honest thing directly - that the point a finger would
 * land on resolves, via the same DOM API a real tap's hit-test agrees with,
 * to the control itself or one of its descendants - and then drives the
 * interaction with `page.mouse.click(x, y)` at that exact point, which (see
 * the same hand-check) does NOT fire the covered button's handler when the
 * assertion above would have failed. */
async function tap(page, locator, label) {
  const box = await locator.boundingBox();
  expect(box, `${label}: no bounding box (detached or display:none)`).not.toBeNull();
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  const handle = await locator.elementHandle();
  const hit = await page.evaluate(
    ({ x, y, el }) => {
      const found = document.elementFromPoint(x, y);
      return !!found && (found === el || el.contains(found) || found.contains(el));
    },
    { x, y, el: handle },
  );
  expect(hit, `${label} is not the real hit-test target at its own center - something else covers it`).toBe(true);
  await page.mouse.click(x, y);
}

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
