// Playwright coverage for the transcription-analysis empty-state sentence
// ScoreCompare.svelte renders when a score has no tab to extract (issue
// #294). TranscriptionAnalysisOut's `vector`, `tab_staff_count`,
// `standard_staff_count` and `reason` had zero consumers in web/src before
// this file existed - the panel showed `analysis.reason` alone (or a fixed
// generic sentence when `reason` was empty), with no staff-count context at
// all. See score-compare-disclosures.spec.js for the sibling structural-
// disclosures panel this sits beside.
import { test, expect } from "@playwright/test";
import { stubScoreApi } from "./fixtures/transcription-warnings.js";

test.describe("ScoreCompare transcription-analysis sentence", () => {
  test("a non-extractable pdf shows one sentence built from the reason and the staff counts", async ({
    page,
  }) => {
    await stubScoreApi(page, null, {
      analysis: {
        extractable: false,
        reason:
          "no 6-line tab staff groups found - pages are vector but appear to be standard-notation only (fingering numbers are not fret numbers)",
        vector: true,
        tab_staff_count: 0,
        standard_staff_count: 2,
        page_count: 3,
      },
    });
    await page.goto("/#/score/1");

    await expect(page.locator(".empty-state h3")).toHaveText("No tab to extract");
    const text = await page.locator(".empty-state p").first().innerText();
    expect(text).toContain(
      "no 6-line tab staff groups found - pages are vector but appear to be standard-notation only (fingering numbers are not fret numbers)",
    );
    // the two fields the reason text alone never carries
    expect(text).toContain("2 standard staves");
    expect(text).toContain("vector PDF");
    // no transcribe offering for a score that cannot be transcribed
    await expect(page.locator('button:has-text("Transcribe this PDF")')).toHaveCount(0);
  });

  test("a raster scan is named as raster, not vector, and a single staff is singular", async ({
    page,
  }) => {
    await stubScoreApi(page, null, {
      analysis: {
        extractable: false,
        reason: "no fonts, no vector drawings, no text on any page - pdf is a raster scan",
        vector: false,
        tab_staff_count: 0,
        standard_staff_count: 1,
        page_count: 1,
      },
    });
    await page.goto("/#/score/1");

    const text = await page.locator(".empty-state p").first().innerText();
    expect(text).toContain("1 standard staff,");
    expect(text).toContain("raster PDF");
    expect(text).not.toContain("standard staves");
  });

  test("a score that is extractable, or one whose analysis has not loaded, still offers the transcribe button instead", async ({
    page,
  }) => {
    // extractable: true (a real transcription just hasn't been run yet) -
    // the sentence above must not eclipse the ordinary "no transcription
    // yet" offering for a score that actually can be transcribed.
    await stubScoreApi(page, null, {
      analysis: { extractable: true, reason: null, vector: true, tab_staff_count: 2, standard_staff_count: 0, page_count: 2 },
    });
    await page.goto("/#/score/1");

    await expect(page.locator(".empty-state h3")).toHaveText("No staff transcription yet");
    await expect(page.locator('button:has-text("Transcribe this PDF")')).toBeVisible();
  });
});
