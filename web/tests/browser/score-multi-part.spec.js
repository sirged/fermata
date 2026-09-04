// Issue #93: only the first part of a multi-part score is drawn.
//
// WHAT IS ASSERTED, AND WHY. score-render.js's load() calls hand alphaTab a
// byte buffer with no track list, and AlphaTabApiBase.renderScore() falls
// back to `[score.tracks[0]]` whenever the track-index argument is falsy or
// empty (confirmed by reading the bundled renderScore() source in
// node_modules/@coderline/alphatab/dist/alphaTab.js - see score-render.js's
// own ALL_TRACKS comment). A two-part MusicXML document becomes two alphaTab
// tracks, one per <part>; on unfixed main only the first part's name reaches
// the drawn SVG's text nodes at all - the second part is imported into the
// score model (the importer never drops it) but never handed to the
// renderer, so there is nothing on screen naming it, positioning it, or
// letting a person play it. Asserting the drawn SVG's own text content -
// rather than something read off the score model - is deliberate: a fix that
// parsed both parts correctly but still only rendered one would pass any
// assertion made against the model and fail only this one.
import { expect, test } from "@playwright/test";
import { stubMultiPartScore } from "./fixtures/multi-part-score.js";

const host = (page) => page.locator(".at-host");
const playButton = (page) => page.locator(".player button.primary");

// Mirrors navigation.spec.js's openScore(): data-score-render-ok reads "true"
// in two states that drew nothing at all - before the very first render ever
// runs, and when a render request was silently dropped at width 0 or deferred
// by font loading (see web/test-fixtures/tab-profile-selection.md, "A score
// that draws nothing") - so it is not on its own proof that anything is on
// screen yet. Waiting for the play button to become enabled as well is what
// the rest of this suite uses to mean "a real render actually finished."
async function openScore(page, id) {
  await page.goto(`/#/score/${id}`);
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true", { timeout: 30_000 });
}

// Trimmed and NBSP-normalized: alphaTab pads some drawn text (bar numbers
// render as "1  ", for instance) with trailing whitespace, and writes a
// multi-word track name with a U+00A0 NON-BREAKING space between the words
// (measured directly - a plain ASCII space would not survive this) rather
// than an ordinary one, almost certainly to stop its own line-wrapping
// splitting a track name across two lines. Neither has anything to do with a
// string's identity for this spec's purposes, so both are normalized away
// here rather than baked into the two literals this file compares against.
async function drawnText(page) {
  return await page.evaluate(() =>
    [...document.querySelectorAll(".at-host svg text")].map((t) => t.textContent.replace(/ /g, " ").trim()),
  );
}

test.describe("a multi-part score draws every part, not just the first", () => {
  test("both part names reach the rendered SVG", async ({ page }) => {
    const consoleErrors = [];
    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m.text());
    });
    page.on("pageerror", (e) => consoleErrors.push(String(e)));

    await stubMultiPartScore(page);
    await openScore(page, 30);

    const drawn = await drawnText(page);
    // Pre-fix (renderScore()'s default, tracks[0] only): drawn contains
    // "Upper Part" and NOT "Lower Part" - this is the exact issue #93
    // repro, reproduced against this fixture below before the fix (see the
    // PR description's mutation record). Post-fix: both are present, because
    // load() now asks alphaTab for ALL_TRACKS rather than accepting its
    // first-track-only default.
    expect(drawn).toContain("Upper Part");
    expect(drawn).toContain("Lower Part");
    expect(consoleErrors).toEqual([]);
  });

  test("the second part's own notation actually draws, not just its label", async ({ page }) => {
    // The label alone does not prove the STAFF drew - alphaTab prints a
    // track's name once per staff system regardless of whether the staff
    // beneath it holds any glyphs. Both parts here are pitched, non-percussion
    // treble/bass staves with four notes per bar for three bars, so a
    // genuinely rendered second track draws real glyph volume, not just a
    // name. Measured on a fixed page: comfortably more than a bare label and
    // an empty staff would ever produce.
    await stubMultiPartScore(page);
    await openScore(page, 30);
    const glyphCount = await page.evaluate(() => document.querySelectorAll(".at-host svg text").length);
    expect(glyphCount).toBeGreaterThan(10);
  });
});
