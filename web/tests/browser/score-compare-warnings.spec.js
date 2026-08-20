// Playwright coverage for the transcription-warnings summary in
// ScoreCompare.svelte (issue #45 / PR #67, and the shape fix that followed
// once server/fermata/api.py started lifting bars_defective/bars_measured
// to the top level of the transcription object - PR #70).
//
// None of this needs the real extraction pipeline - it's about what the
// component does with a given payload shape, which server/tests already
// covers on the backend side - so every scenario stubs `/api/scores/1*`
// with `page.route` rather than transcribing a real PDF against the real
// backend this harness runs. That backend still serves the page shell and
// everything these tests don't stub (e.g. the library list on `#/`), the
// same way "a failed load is retryable" in instruments.spec.js layers a
// route stub over the real running app for one scenario rather than
// standing up a second server.
//
// The one thing here that WAS run against a real backend, by hand, before
// this suite existed: the "structured field" shape and the save/revert
// scenario below, against a real server built from fix/persist-bar-
// conformance (PR #70 / #71) transcribing the real "To Zanarkand" PDF,
// reloading, hand-editing, and reverting - see those PRs' discussion for
// that run's output. The two-voice fixture below does not reproduce on any
// of the 144 extractable scores in the real library; it exists because a
// fixture is the only way to exercise it.
import { test, expect } from "@playwright/test";
import {
  SCORE,
  NINE_WARNINGS,
  NINE_WARNINGS_EXPECTED_SUMMARY,
  CAPPED_CONFIDENCE,
  CLEAN_CONFIDENCE,
  TWO_VOICE_WARNINGS,
  TWO_VOICE_CONFIDENCE,
  STANDING_LIMITS_ONLY_WARNINGS,
  transcriptionResponse,
  stubScoreApi,
  editedTranscriptionResponse,
} from "./fixtures/transcription-warnings.js";

// This bug and its fix were verified for real against fix/persist-bar-
// conformance (commit 81532ec) and the real "To Zanarkand" PDF: transcribe
// (headline shows real figures) -> hand-edit and save through the UI
// (panel goes to nothing, not the pre-edit figures) -> revert to extracted
// (real figures return, identical to the first transcribe). The scenario
// below is the mocked version of that same three-state check.

test.describe("ScoreCompare warnings summary", () => {
  test("states the bar count and capped confidence without interaction, keeps the staff in view, and its detail toggles", async ({
    page,
  }) => {
    const consoleErrors = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await stubScoreApi(
      page,
      transcriptionResponse({
        warnings: NINE_WARNINGS,
        confidence: CAPPED_CONFIDENCE,
        bars: { defective: 3, measured: 50, overfull: 3, short: 0 },
      }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".warnings-summary");

    // the one-line summary is the thing that must be true without clicking
    // anything - both the bar count and the capped confidence live here
    await expect(page.locator(".warn-text")).toHaveText(NINE_WARNINGS_EXPECTED_SUMMARY);

    // expanded the first time this score's warnings are seen this session
    await expect(page.locator(".warnings-detail")).toBeVisible();

    // the justification for each item is readable without hovering - not
    // parked behind a title-only tooltip, unreachable on a touch device
    await expect(page.locator(".warnings-detail")).toContainText(
      "treat those specific notes as low confidence",
    );

    // the staff must render within the viewport rather than below the fold -
    // the entire point of summarising instead of listing prose
    const staffBox = await page.locator(".staff-render").boundingBox();
    expect(staffBox.y).toBeLessThan(page.viewportSize().height);

    // toggles closed and back open
    await page.locator(".warnings-summary").click();
    await expect(page.locator(".warnings-detail")).toBeHidden();
    await page.locator(".warnings-summary").click();
    await expect(page.locator(".warnings-detail")).toBeVisible();

    expect(consoleErrors).toEqual([]);
  });

  test("says nothing about bars when bars_defective/bars_measured are absent, even though the warning prose describes a bar wrong in both directions", async ({
    page,
  }) => {
    // No `bars` passed - models an edited row (no confidence at all is
    // normal) or a transcription stored before this field existed. Summing
    // the two sentences' counts (7 + 6) would claim 13 of 12 bars are
    // defective, which is impossible - the headline must say nothing about
    // bars rather than something confidently wrong.
    await stubScoreApi(
      page,
      transcriptionResponse({ warnings: TWO_VOICE_WARNINGS, confidence: TWO_VOICE_CONFIDENCE }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".warnings-summary");

    const summary = await page.locator(".warn-text").innerText();
    expect(summary).not.toMatch(/\d+ of \d+ bars? don't add up/);
    expect(summary).toBe("rhythm confidence low overall · 2 more");

    // nothing is lost - both full sentences are still in the detail list
    await page.locator(".warnings-summary").click();
    const detail = await page.locator(".warnings-detail").innerText();
    expect(detail).toContain("7 of 12");
    expect(detail).toContain("6 of 12");
  });

  test("prefers the structured bars_defective/bars_measured over the warning prose when both are present", async ({
    page,
  }) => {
    // Deliberately mismatched prose ("2 of 999") - a pass here can only mean
    // the structured top-level fields were read, not the regex fallback.
    await stubScoreApi(
      page,
      transcriptionResponse({
        warnings: ["2 of 999 bar(s) hold more than their time signature allows."],
        confidence: CAPPED_CONFIDENCE,
        bars: { defective: 7, measured: 40 },
      }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".warnings-summary");
    await expect(page.locator(".warn-text")).toContainText("7 of 40 bars don't add up");
  });

  test("saving a hand edit clears the stale bar figures and warnings instead of preserving them, and reverting restores the real ones", async ({
    page,
  }) => {
    const extracted = transcriptionResponse({
      warnings: NINE_WARNINGS,
      confidence: CAPPED_CONFIDENCE,
      bars: { defective: 3, measured: 50, overfull: 3, short: 0 },
    });
    await stubScoreApi(page, extracted, { editRevert: true });
    await page.goto("/#/score/1");
    await page.waitForSelector(".warnings-summary");
    await expect(page.locator(".warn-text")).toHaveText(NINE_WARNINGS_EXPECTED_SUMMARY);

    await page.locator('button:has-text("Edit source")').click();
    await page.locator(".editor-input").fill("<score-partwise><!-- fixed the bars --></score-partwise>");
    await page.locator('button:has-text("Save & render")').click();
    await expect(page.locator(".editor-input")).toHaveCount(0);

    // the panel must go to NOTHING - not the pre-edit "3 of 50", which an
    // endpoint that omits rather than states its empty keys would silently
    // preserve through the { ...transcription, ...res } merge in saveEdit()
    await expect(page.locator(".warnings")).toHaveCount(0);
    await expect(page.locator(".standing-footnote")).toHaveCount(0);
    await expect(page.locator(".source-badge.edited")).toBeVisible();

    await page.locator('button:has-text("Revert to extracted")').click();
    await page.waitForSelector(".warnings-summary");
    await expect(page.locator(".warn-text")).toHaveText(NINE_WARNINGS_EXPECTED_SUMMARY);
  });

  test("still surfaces warnings nested under confidence.warnings, the shape GET /transcription can return", async ({
    page,
  }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({
        warnings: NINE_WARNINGS,
        confidence: CAPPED_CONFIDENCE,
        bars: { defective: 3, measured: 50, overfull: 3, short: 0 },
        nestWarningsOnly: true, // no top-level `warnings` key
      }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".warnings-summary");
    await expect(page.locator(".warnings")).toBeVisible();
    await expect(page.locator(".warn-text")).toHaveText(NINE_WARNINGS_EXPECTED_SUMMARY);
  });

  test("renders nothing intrusive when there are no warnings", async ({ page }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    await expect(page.locator(".warnings")).toHaveCount(0);
    await expect(page.locator(".standing-footnote")).toHaveCount(0);
  });

  test("shows a quiet footnote, not the warnings box, when only standing limitations apply", async ({
    page,
  }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({ warnings: STANDING_LIMITS_ONLY_WARNINGS, confidence: CLEAN_CONFIDENCE }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    await expect(page.locator(".warnings")).toHaveCount(0);
    await expect(page.locator(".standing-footnote")).toHaveText(
      "Standing limits: tuplets aren't detected; tie detection is approximate.",
    );
  });

  test("expands on a score's first view this session and collapses on a later visit", async ({
    page,
  }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({
        warnings: NINE_WARNINGS,
        confidence: CAPPED_CONFIDENCE,
        bars: { defective: 3, measured: 50, overfull: 3, short: 0 },
      }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".warnings-summary");
    await expect(page.locator(".warnings-detail")).toBeVisible();

    // navigate away and back within the same session (sessionStorage persists)
    await page.goto("/#/");
    await page.goto("/#/score/1");
    await page.waitForSelector(".warnings-summary");
    await expect(page.locator(".warnings-detail")).toBeHidden();
  });

  test("gig mode drops the warnings block entirely and the score fills the view", async ({
    page,
  }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({
        warnings: NINE_WARNINGS,
        confidence: CAPPED_CONFIDENCE,
        bars: { defective: 3, measured: 50, overfull: 3, short: 0 },
      }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    // side-by-side is the default layout once a transcription exists, and
    // gig mode falls back from "side" to the PDF pane alone (a single HUD) -
    // switch to Staff first so gig mode is actually showing the tab.
    await page.locator('.seg button:has-text("Staff")').click();
    await page.locator('button:has-text("Gig mode")').click();

    await expect(page.locator(".warnings")).toHaveCount(0);
    await expect(page.locator(".standing-footnote")).toHaveCount(0);

    const viewport = page.viewportSize();
    const scoreBox = await page.locator(".staff-render").boundingBox();
    expect(scoreBox.height / viewport.height).toBeGreaterThan(0.95);
  });

  test("gig mode shows a small mark, with the specific fact in its title, only when something is actually unverified", async ({
    page,
  }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({
        warnings: NINE_WARNINGS,
        confidence: CAPPED_CONFIDENCE,
        bars: { defective: 3, measured: 50, overfull: 3, short: 0 },
      }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    await page.locator('.seg button:has-text("Staff")').click();
    await page.locator('button:has-text("Gig mode")').click();

    const mark = page.locator(".gig-mark");
    await expect(mark).toHaveCount(1);
    await expect(mark).toHaveAttribute(
      "title",
      "3 of 50 bars don't add up · rhythm confidence: medium",
    );
  });

  test("gig mode shows no mark on a clean score", async ({ page }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    await page.locator('.seg button:has-text("Staff")').click();
    await page.locator('button:has-text("Gig mode")').click();
    await expect(page.locator(".gig-mark")).toHaveCount(0);
  });

  test("gig mode shows no mark when only standing limitations apply", async ({ page }) => {
    await stubScoreApi(
      page,
      transcriptionResponse({ warnings: STANDING_LIMITS_ONLY_WARNINGS, confidence: CLEAN_CONFIDENCE }),
    );
    await page.goto("/#/score/1");
    await page.waitForSelector(".staff-render");
    await page.locator('.seg button:has-text("Staff")').click();
    await page.locator('button:has-text("Gig mode")').click();
    await expect(page.locator(".gig-mark")).toHaveCount(0);
  });

  test("the #/demo route still renders with no console errors", async ({ page }) => {
    const consoleErrors = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    page.on("pageerror", (err) => consoleErrors.push(String(err)));
    await page.goto("/#/demo");
    await page.waitForTimeout(2000);
    expect(consoleErrors).toEqual([]);
  });
});
