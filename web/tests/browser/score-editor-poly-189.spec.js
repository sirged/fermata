// Polyphonic selection, including the genuinely-overlapping note heads (#189).
//
// The first editor increment's fixture is monophonic, so overlapping-voice
// selection was untested. This covers it end to end against the real alphaTab
// render: a two-voice score (fixtures/editor-poly.js) where a note in each voice
// sounds the SAME pitch at the SAME onset (a cross-voice unison), so their heads
// draw one on top of the other - the ~1.5% genuinely-overlapping case the #10
// evaluation flagged as an open question.
//
// The disambiguation, stated and tested here: CLICK-CYCLING. A first click at a
// spot selects the nearest note stacked there; each further click at the SAME
// spot advances to the next note in that stack, wrapping around (see
// score-render.js hitTestNote). A single click still selects the nearest note
// exactly as before for the non-overlapping 98.5%. This test proves BOTH
// overlapping notes can be reached - two distinct ordinals, two different voices,
// the divergence guard green for each.
import { test, expect } from "@playwright/test";

import { OVERLAP_MUSICXML, POLY_MUSICXML } from "./fixtures/editor-poly.js";
import { stubEditorApi } from "./fixtures/editor-score.js";

const wrap = (page) => page.locator(".staff-render .wrap");
const host = (page) => page.locator(".staff-render .at-host");

async function renderedOk(page) {
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true");
}

async function openEditor(page, content, expected) {
  await stubEditorApi(page, content);
  await page.goto("/#/score/1");
  await page.waitForSelector(".staff-render");
  await page.getByRole("button", { name: "Staff", exact: true }).click();
  await renderedOk(page);
  await page.getByRole("button", { name: "Edit notes" }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
  await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(expected);
  await settleEditorLayout(page);
}

// Entering edit mode turns the notation staff on (an alphaTab re-render) AND
// mounts the edit panel (a DOM reflow); the coordinates headPoint()/hitTest()
// work in are live screen geometry (an alphaTab bound plus the surface's
// getBoundingClientRect), so a click fired before that geometry settles
// hit-tests against a layout still in motion and lands on empty space - the
// select registers nothing and data-editor-selected reads null. This is #189's
// intermittent flake: noteCount() does NOT gate on the settle (it is already
// 52/4 from the tab-only render before edit mode, so its poll passes at once),
// and neither does a render-completion signal - the note's screen y was
// measured still shifting (363 -> 295 px) after alphaTab's postRenderFinished
// had fired and the note bounds had doubled, because the panel-mount reflow and
// growPaperToDrawing move the surface, not the alphaTab bounds. So gate on the
// hit-test geometry itself holding still: the same head point across two
// consecutive reads. Once the layout has settled it stays put (measured stable
// across 29 further reads), so this waits out the transient without a fixed
// sleep, exactly where the click's coordinates come from.
async function settleEditorLayout(page) {
  let prev = null;
  await expect
    .poll(async () => {
      const p = await page.evaluate(() => window.__scoreEditor?.headPoint(0) ?? null);
      const key = p ? `${Math.round(p.x)},${Math.round(p.y)}` : null;
      const stable = key != null && key === prev;
      prev = key;
      return stable;
    })
    .toBe(true);
}

// The currently-selected ordinal, as an integer, read off the DOM.
async function selectedOrdinal(page) {
  const v = await wrap(page).getAttribute("data-editor-selected");
  return v == null ? null : Number(v);
}

test.describe("note editor - polyphonic selection", () => {
  // A plain (non-overlapping) note in each voice selects with a single click and
  // the guard is green - the polyphonic baseline the overlap case builds on.
  test("a note in each voice selects with the divergence guard green", async ({ page }) => {
    await openEditor(page, POLY_MUSICXML, 52);

    // Ordinal 0 is voice 1's first note (E4). Select it by its own head point.
    const p0 = await page.evaluate(() => window.__scoreEditor.headPoint(0));
    expect(p0).toBeTruthy();
    await page.mouse.click(p0.x, p0.y);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "0");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-voice", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");

    // A note the fixture places in voice 2 (a bass note) selects too, its voice
    // read back as 2 from BOTH the document and the renderer, guard green.
    const v2 = await page.evaluate(() => {
      const h = window.__scoreEditor;
      for (let i = 0; i < h.noteCount(); i++) {
        if (h.viewInfo(i)?.voice === 2) return i;
      }
      return -1;
    });
    expect(v2).toBeGreaterThan(-1);
    const pv2 = await page.evaluate((i) => window.__scoreEditor.headPoint(i), v2);
    await page.mouse.click(pv2.x, pv2.y);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", String(v2));
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-voice", "2");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  // Target 3: two genuinely-overlapping notes (a cross-voice unison, ordinals 0
  // and 2, both E4 at onset 0) can EACH be selected by click-cycling at the one
  // spot, and the guard is green for each. Asserts BOTH distinct notes reached.
  test("overlapping-voice note heads are each reachable by click-cycling", async ({ page }) => {
    await openEditor(page, OVERLAP_MUSICXML, 4);

    // Ordinals 0 (voice 1) and 2 (voice 2) are the unison. Confirm the fixture
    // really overlaps: same pitch, and their head points essentially coincide.
    const geom = await page.evaluate(() => {
      const h = window.__scoreEditor;
      return {
        p0: h.headPoint(0),
        p2: h.headPoint(2),
        v0: h.viewInfo(0),
        v2: h.viewInfo(2),
        hit: h.hitTest(h.headPoint(0).x, h.headPoint(0).y),
      };
    });
    expect(geom.v0.midi).toBe(64); // E4
    expect(geom.v2.midi).toBe(64); // E4 - the unison
    expect(geom.v0.voice).toBe(1);
    expect(geom.v2.voice).toBe(2);
    // The two heads draw within a couple of pixels of each other - a real
    // overlap, not two separated heads.
    expect(Math.abs(geom.p0.x - geom.p2.x)).toBeLessThanOrEqual(3);
    expect(Math.abs(geom.p0.y - geom.p2.y)).toBeLessThanOrEqual(3);

    // Click the one spot. The first click selects the nearest of the stacked
    // notes; capture which.
    const spot = geom.p0;
    await page.mouse.click(spot.x, spot.y);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", /^(0|2)$/);
    const first = await selectedOrdinal(page);
    expect([0, 2]).toContain(first);
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    const firstVoice = await wrap(page).getAttribute("data-editor-selected-voice");

    // A second click at the SAME spot cycles to the OTHER note in the stack.
    await page.mouse.click(spot.x, spot.y);
    await expect
      .poll(() => selectedOrdinal(page), "a second click at the same spot should cycle to the other overlapping note")
      .not.toBe(first);
    const second = await selectedOrdinal(page);
    expect([0, 2]).toContain(second);
    expect(second).not.toBe(first);
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    const secondVoice = await wrap(page).getAttribute("data-editor-selected-voice");

    // BOTH distinct notes were selected, and they are the two different voices of
    // the unison - the overlap was fully disambiguated.
    expect(new Set([first, second])).toEqual(new Set([0, 2]));
    expect(new Set([firstVoice, secondVoice])).toEqual(new Set(["1", "2"]));

    // A third click at the same spot wraps back to the first note.
    await page.mouse.click(spot.x, spot.y);
    await expect.poll(() => selectedOrdinal(page)).toBe(first);
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  // Cycling is anchored to the SPOT: moving to a different note and back resets
  // the stack, so a single click there again selects the nearest (not the next
  // in a stale cycle).
  test("moving the click elsewhere resets the overlap cycle", async ({ page }) => {
    await openEditor(page, OVERLAP_MUSICXML, 4);
    const spot = await page.evaluate(() => window.__scoreEditor.headPoint(0));
    // A clearly non-overlapping note: ordinal 1 (G4, voice 1, second half of the
    // bar) sits elsewhere on the staff.
    const other = await page.evaluate(() => window.__scoreEditor.headPoint(1));

    await page.mouse.click(spot.x, spot.y);
    const first = await selectedOrdinal(page);
    await page.mouse.click(other.x, other.y);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "1");
    // Back to the overlap spot: a fresh stack, so the nearest (the same `first`)
    // is selected again rather than the cycle continuing from where it left off.
    await page.mouse.click(spot.x, spot.y);
    await expect.poll(() => selectedOrdinal(page)).toBe(first);
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });
});
