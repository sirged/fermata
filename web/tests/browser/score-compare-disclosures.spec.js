// Playwright coverage for the structural-disclosures panel Disclosures.svelte
// renders inside ScoreCompare.svelte (issue #155).
//
// Every one of these counters (repeats_unread, nav_marks_unresolved,
// coincident_unsplit_pairs, ...) was already computed, stored, reloaded
// through the API and mirrored onto TranscriptionOut before this file existed
// - see server/tests for that half. What none of it reached was a reader:
// only the warning PROSE those counters feed showed up in ScoreCompare's
// generic warnings list, and the count itself never did. This suite is about
// what the component does with a given payload shape, the same reason
// score-compare-warnings.spec.js stubs `/api/scores/1*` with page.route
// rather than transcribing a real PDF - server/tests already covers that the
// backend produces these numbers correctly.
import { test, expect } from "@playwright/test";
import {
  CLEAN_CONFIDENCE,
  transcriptionResponse,
  stubScoreApi,
  zeroDisclosures,
} from "./fixtures/transcription-warnings.js";

test.describe("ScoreCompare structural disclosures", () => {
  test("a non-zero disclosure renders its label and value, with its bar list beside it", async ({
    page,
  }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({
        warnings: [],
        confidence: CLEAN_CONFIDENCE,
        disclosures: {
          ...zeroDisclosures(),
          repeats_unread: 2,
          repeats_unread_bars: [12, 19],
        },
      }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");

    const row = page.locator('[data-disclosure="repeats_unread"]');
    await expect(row).toBeVisible();
    await expect(row).toContainText("Repeat marks not read");
    await expect(row.locator(".disclosure-value")).toHaveText("2");
    // the bar list is the counter's detail - the numbers it exists to send a
    // reader to, spelled out plainly (no in-viewer bar navigation exists yet)
    await expect(row.locator(".disclosure-bars")).toHaveText("bars 12, 19");
    // this fixture's every other counter is a real, measured zero - only
    // one row should exist
    await expect(page.locator(".disclosure-row")).toHaveCount(1);
  });

  test("multiple non-zero disclosures across different families each get their own row", async ({
    page,
  }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({
        warnings: [],
        confidence: CLEAN_CONFIDENCE,
        disclosures: {
          ...zeroDisclosures(),
          endings_truncated: 1,
          endings_truncated_bars: [30],
          nav_marks_unresolved: 3,
          nav_marks_unresolved_bars: [4, 5, 6],
          coincident_unsplit_pairs: 2,
          staves_coincident_unsplit: 1,
          unison_digits_shared: 5,
          notes_no_stem: 7,
          staves_no_stem: 2,
        },
      }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");

    await expect(page.locator('[data-disclosure="endings_truncated"] .disclosure-value')).toHaveText("1");
    await expect(page.locator('[data-disclosure="nav_marks_unresolved"] .disclosure-value')).toHaveText("3");
    await expect(page.locator('[data-disclosure="nav_marks_unresolved"] .disclosure-bars')).toHaveText(
      "bars 4, 5, 6",
    );
    await expect(page.locator('[data-disclosure="coincident_unsplit_pairs"] .disclosure-value')).toHaveText("2");
    await expect(page.locator('[data-disclosure="staves_coincident_unsplit"] .disclosure-value')).toHaveText("1");
    await expect(page.locator('[data-disclosure="unison_digits_shared"] .disclosure-value')).toHaveText("5");
    await expect(page.locator('[data-disclosure="notes_no_stem"] .disclosure-value')).toHaveText("7");
    await expect(page.locator('[data-disclosure="staves_no_stem"] .disclosure-value')).toHaveText("2");
    await expect(page.locator(".disclosure-row")).toHaveCount(7);
  });

  test("systems_unread's list reads as pages, not bars", async ({ page }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({
        warnings: [],
        confidence: CLEAN_CONFIDENCE,
        disclosures: { ...zeroDisclosures(), systems_unread: 1, systems_unread_pages: [9] },
      }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    await expect(page.locator('[data-disclosure="systems_unread"] .disclosure-bars')).toHaveText("page 9");
  });

  test("zero-valued counters are absent from the DOM entirely, not shown as a wall of zeros", async ({
    page,
  }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({
        warnings: [],
        confidence: CLEAN_CONFIDENCE,
        disclosures: { ...zeroDisclosures(), repeats_unread: 4, repeats_unread_bars: [1] },
      }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");

    await expect(page.locator('[data-disclosure="repeats_unread"]')).toBeVisible();
    await expect(page.locator(".disclosure-row")).toHaveCount(1);
    for (const key of [
      "endings_unread",
      "endings_truncated",
      "form_marks_unanchored",
      "nav_marks_unresolved",
      "nav_marks_unanchored",
      "dots_unassigned",
      "notes_no_stem",
    ]) {
      await expect(page.locator(`[data-disclosure="${key}"]`)).toHaveCount(0);
    }
  });

  test("a transcription where every disclosure is a real zero shows no disclosures panel at all", async ({
    page,
  }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({
        warnings: [],
        confidence: CLEAN_CONFIDENCE,
        disclosures: zeroDisclosures(),
      }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    await expect(page.locator(".disclosures")).toHaveCount(0);
  });

  test("a legacy counter that was never computed for this row renders 'not measured', never '0'", async ({
    page,
  }) => {
    // Models a transcription extracted before nav_marks (issue #134 phase 2)
    // shipped: repeats_unread is a real, honest zero on every OTHER counter
    // (phase 1 already existed and found nothing), but nav_marks_unresolved
    // was never computed for this row at all - an omitted/`null` field, not
    // a zero.
    const disclosures = { ...zeroDisclosures(), repeats_unread: 6, repeats_unread_bars: [2, 3] };
    delete disclosures.nav_marks_unresolved;
    delete disclosures.nav_marks_unresolved_bars;
    await stubScoreApi(
      page,
      transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE, disclosures }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");

    const navRow = page.locator('[data-disclosure="nav_marks_unresolved"]');
    await expect(navRow).toBeVisible();
    await expect(navRow.locator(".disclosure-value")).toHaveText("not measured");
    await expect(navRow.locator(".disclosure-value")).not.toHaveText("0");
    // no bar list for a counter that was never measured
    await expect(navRow.locator(".disclosure-bars")).toHaveCount(0);
    // and its sibling, which WAS measured, still shows its real number -
    // exactly these two rows exist, nothing else
    await expect(page.locator('[data-disclosure="repeats_unread"] .disclosure-value')).toHaveText("6");
    await expect(page.locator(".disclosure-row")).toHaveCount(2);
  });

  test("an edited row with no confidence at all (every counter null) shows no disclosures panel", async ({
    page,
  }) => {
    const extracted = transcriptionResponse({
      warnings: [],
      confidence: CLEAN_CONFIDENCE,
      disclosures: { ...zeroDisclosures(), repeats_unread: 3, repeats_unread_bars: [1] },
    });
    await stubScoreApi(page, extracted, { editRevert: true });
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    await expect(page.locator(".disclosures")).toBeVisible();

    await page.locator('button:has-text("Edit source")').click();
    await page.locator(".editor-input").fill("<score-partwise><!-- fixed it --></score-partwise>");
    await page.locator('button:has-text("Save & render")').click();
    await expect(page.locator(".editor-input")).toHaveCount(0);

    // saveEdit()'s response states every disclosure field null (the same
    // "not recorded" contract the bar counts already use) - a wall of
    // seventeen "not measured" rows would be exactly the noise the null-vs-
    // zero rule exists to avoid, so the whole panel goes to nothing instead,
    // the same way the bar headline and warnings box already do for this
    // state.
    await expect(page.locator(".disclosures")).toHaveCount(0);

    await page.locator('button:has-text("Revert to extracted")').click();
    await page.waitForSelector(".staff-render");
    await expect(page.locator('[data-disclosure="repeats_unread"] .disclosure-value')).toHaveText("3");
  });

  test("gig mode drops the disclosures panel along with the rest of the review chrome", async ({
    page,
  }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({
        warnings: [],
        confidence: CLEAN_CONFIDENCE,
        disclosures: { ...zeroDisclosures(), repeats_unread: 2, repeats_unread_bars: [1, 2] },
      }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    await page.locator('.seg button:has-text("Staff")').click();
    await page.locator('button:has-text("Gig mode")').click();
    await expect(page.locator(".disclosures")).toHaveCount(0);
  });
});
