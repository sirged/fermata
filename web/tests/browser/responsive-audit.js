// Shared responsive-layout audit machinery, factored out of
// toolbar-responsive.spec.js (issue #106) so a second spec checking a
// different row for the same two failure modes - no clipping, every control
// reachable by a real click - does not have to re-derive or duplicate it.
// toolbar-responsive.spec.js itself imports these rather than defining them
// twice; see that file for the design story behind WHY wrap-not-shrink is
// the fix these functions are verifying, at every width they check.
//
// Not a *.spec.js file on purpose: Playwright's default testMatch only picks
// up files with "test" or "spec" in the name, so this one is never collected
// or run on its own - it exists to be imported.
import { expect } from "@playwright/test";

// 834 and 768 are ordinary portrait-tablet widths - this project's own
// stated primary form factor, a tablet on a music stand. 1280/1024 are
// ordinary desktop widths, and 430 is an ordinary phone width - the whole
// range #106 and #8 both measured against.
export const WIDTHS = [1280, 1024, 834, 768, 430];

/** Audits every button/select/input under `selector` for two kinds of
 * overflow: the page growing a horizontal scrollbar, and a control being
 * clipped away by some ancestor's non-visible overflow (the `.seg` failure
 * mode #106 found) even though the page itself never overflowed. Returns the
 * clipped fraction (0 = fully visible, 1 = entirely clipped) for anything
 * more than 40% cut off, by name, so a failure says which control and by
 * how much rather than just "something overflowed". */
export async function clippingAudit(page, selector) {
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
 * A control clipped by an ancestor's `overflow: hidden` hard enough that
 * `document.elementFromPoint()` at its own geometric center resolves to a
 * DIFFERENT element sitting next to it is exactly what a real fingertip
 * would hit instead - confirmed by hand against #106's pre-fix build.
 * Playwright's `locator.click()` still succeeds and still fires the covered
 * control's handler in that exact state, which makes it the wrong tool
 * here: it is one of the "passes against both the working and broken
 * layout" tests this project has shipped before.
 *
 * So this asserts the honest thing directly - that the point a finger would
 * land on resolves, via the same DOM API a real tap's hit-test agrees with,
 * to the control itself or one of its descendants - and then drives the
 * interaction with `page.mouse.click(x, y)` at that exact point, which does
 * NOT fire the covered control's handler when the assertion above would
 * have failed. */
export async function tap(page, locator, label) {
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
