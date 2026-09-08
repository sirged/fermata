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

  test("a raster scan (page_count > 0, vector: false) shows only the reason - staff counts are never appended, because tabextract.py's raster branch never called _detect_staves", async ({
    page,
  }) => {
    // Every page failed the raster/vector test, so analyze() returns before
    // ever counting staves - standard_staff_count: 0 here is an unmeasured
    // placeholder, exactly like the page_count === 0 case, and must not be
    // shown as if it were a measured "0 standard staves" finding.
    await stubScoreApi(page, null, {
      analysis: {
        extractable: false,
        reason: "no fonts, no vector drawings, no text on any page - pdf is a raster scan",
        vector: false,
        tab_staff_count: 0,
        standard_staff_count: 0,
        page_count: 3,
      },
    });
    await page.goto("/#/score/1");

    // Wait for the analysis-based branch before reading the paragraph -
    // loadAnalysis() is async and reading too early races the "No staff
    // transcription yet" fallback the component starts in.
    await expect(page.locator(".empty-state h3")).toHaveText("No tab to extract");
    const text = await page.locator(".empty-state p").first().innerText();
    expect(text).toBe("no fonts, no vector drawings, no text on any page - pdf is a raster scan");
    expect(text).not.toContain("standard staff");
    expect(text).not.toContain("standard staves");
    expect(text).not.toContain("PDF");
  });

  test("a vector pdf with no tab staff shows the reason plus the measured standard-staff count", async ({
    page,
  }) => {
    // vector: true means _detect_staves actually ran on every page, so the
    // staff counts here are real measurements and the parenthetical is
    // trustworthy - unlike the raster case above.
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
    expect(text).toContain("2 standard staves");
    expect(text).toContain("vector PDF");
  });

  test("a corrupt pdf that was never analysed shows the reason alone, with no unmeasured staff/vector clause", async ({
    page,
  }) => {
    // page_count: 0 means analyze() never got past opening the file -
    // vector/tab_staff_count/standard_staff_count are hardcoded placeholders
    // here, not a measurement, so the sentence must be the reason and
    // nothing else.
    await stubScoreApi(page, null, {
      analysis: {
        extractable: false,
        reason: "could not open pdf: corrupt xref table",
        vector: false,
        tab_staff_count: 0,
        standard_staff_count: 0,
        page_count: 0,
      },
    });
    await page.goto("/#/score/1");

    // Wait for the analysis-based branch to actually be selected (loadAnalysis
    // is async) before reading the paragraph - innerText() alone does not
    // retry, and reading too early would race the "No staff transcription
    // yet" fallback the component starts in.
    await expect(page.locator(".empty-state h3")).toHaveText("No tab to extract");
    const text = await page.locator(".empty-state p").first().innerText();
    expect(text).toBe("could not open pdf: corrupt xref table");
    expect(text).not.toContain("standard staff");
    expect(text).not.toContain("standard staves");
    expect(text).not.toContain("PDF");
    expect(text).not.toContain("(");
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
