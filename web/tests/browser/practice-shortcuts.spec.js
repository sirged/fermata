// Single-key shortcuts for the staff/practice view (issue #92).
//
// Two pages are used on purpose, for two different things neither can prove
// alone:
//
//   - "/#/demo" (TabViewer.svelte's bundled alphaTex sample) for every
//     shortcut that acts on the score itself - play/pause, loop, speed,
//     metronome, count-in, theme, the profile switch, cursor movement, the
//     loop boundary, and double-click-to-play. It touches no library and
//     needs no cleanup (see toolbar-responsive.spec.js for the same choice),
//     and - unlike most real library scores - the sample renders under all
//     three profiles at once, so the profile-switch keys have three buttons
//     to exercise.
//   - a stubbed real score page ("/#/score/1", metronome-score.js's own
//     fixture) for the one thing "/#/demo" cannot test at all: the focus
//     guard. Demo mode's header carries no text field of any kind (see
//     Viewer.svelte - the tag editor only renders for a real score), and the
//     guard this suite is here to prove correct needs one to type into.
//
// Every assertion about state after a keypress reads it back from something
// the real code path writes as a CONSEQUENCE of acting - a CSS class already
// bound to the same state a mouse click would set, an existing dataset
// attribute score-render.js already published before this issue, or the new
// cursor/loop-range ones this issue's own code adds by re-reading
// api.tickPosition/api.playbackRange live rather than tracking a separate
// counter (see score-render.js's own comment on publishCursor for why that
// distinction is the one metronome.spec.js's header warns matters) - never a
// value sampled immediately after a keypress with no wait. See
// tests/minimum-tests.js for how these were checked against a mutation of
// the behaviour they claim.
import { expect, test } from "@playwright/test";
import { stubMetronomeScore, stubMetronomeScoreRepeat } from "./fixtures/metronome-score.js";
import { CLEAN_CONFIDENCE, MIN_PDF, SCORE, stubScoreApi, transcriptionResponse } from "./fixtures/transcription-warnings.js";

/**
 * A minimal, valid multi-page PDF, built (not hand-copied like
 * transcription-warnings.js's own single-page MIN_PDF) so its byte offsets
 * are always correct for however many pages are asked for. Needed only
 * here, for the side-layout page-turn spec below - MIN_PDF has exactly one
 * page, which cannot demonstrate a page actually turning.
 */
function buildMultiPagePdf(pageCount) {
  const objects = ["1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"];
  const kids = Array.from({ length: pageCount }, (_, i) => `${3 + i} 0 R`).join(" ");
  objects.push(`2 0 obj<</Type/Pages/Kids[${kids}]/Count ${pageCount}>>endobj\n`);
  for (let i = 0; i < pageCount; i++) {
    objects.push(`${3 + i} 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n`);
  }
  let body = "%PDF-1.4\n";
  const offsets = [0];
  for (const obj of objects) {
    offsets.push(body.length);
    body += obj;
  }
  const xrefStart = body.length;
  const total = objects.length + 1;
  let xref = `xref\n0 ${total}\n0000000000 65535 f \n`;
  for (let i = 1; i < total; i++) xref += `${String(offsets[i]).padStart(10, "0")} 00000 n \n`;
  body += xref + `trailer<</Size ${total}/Root 1 0 R>>\nstartxref\n${xrefStart}\n%%EOF`;
  return Buffer.from(body, "utf-8");
}

const host = (page) => page.locator(".at-host");
const playButton = (page) => page.locator(".player button.primary");
const stopButton = (page) => page.locator(".player button[aria-label*='Backspace']");
const loopButton = (page) => page.locator('button:has-text("Loop")');
const metronomeButton = (page) => page.locator('button:has-text("Metronome")');
const countInButton = (page) => page.locator('button:has-text("Count-in")');
const speedSelect = (page) => page.locator('select[title="Playback speed"]');
const themeSelect = (page) => page.locator(".theme-picker");
const profileButtons = (page) => page.locator(".seg button");

async function openDemo(page) {
  await page.goto("/#/demo");
  // All three profile buttons is the signal the sample actually finished
  // loading and rendering, not merely that the page navigated - see
  // toolbar-responsive.spec.js's identical wait for the same reason.
  await expect(profileButtons(page)).toHaveCount(3, { timeout: 15_000 });
  await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
}

async function cursorTick(page) {
  return Number(await host(page).getAttribute("data-cursor-tick"));
}

async function cursorBar(page) {
  return Number(await host(page).getAttribute("data-cursor-bar"));
}

async function loopStartTick(page) {
  const v = await host(page).getAttribute("data-loop-start-tick");
  return v == null ? null : Number(v);
}

async function loopEndTick(page) {
  const v = await host(page).getAttribute("data-loop-end-tick");
  return v == null ? null : Number(v);
}

test("Space toggles play/pause when the staff view has focus", async ({ page }) => {
  await openDemo(page);
  await expect(playButton(page)).toHaveText(/Play/);
  await page.keyboard.press(" ");
  await expect(playButton(page)).toHaveText(/Pause/);
  await page.keyboard.press(" ");
  await expect(playButton(page)).toHaveText(/Play/);
});

test("Backspace stops playback and returns the cursor to the start", async ({ page }) => {
  await openDemo(page);
  // Move the cursor on first, with the arrow keys under test elsewhere in
  // this file - Backspace has nothing to prove if the cursor never left 0.
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  // expect.poll, not a bare read: every assertion in this file about state
  // after a keypress retries rather than sampling once, immediately - see
  // this file's own header and issue #110, which this project has hit
  // before from exactly this shape of assertion.
  await expect.poll(() => cursorBar(page)).toBeGreaterThan(0);
  await page.keyboard.press(" ");
  await expect(playButton(page)).toHaveText(/Pause/);
  await page.keyboard.press("Backspace");
  await expect(playButton(page)).toHaveText(/Play/);
  await expect(host(page)).toHaveAttribute("data-cursor-tick", "0");
});

test("L toggles the loop, S cycles speed, N toggles the metronome, C toggles count-in", async ({
  page,
}) => {
  await openDemo(page);

  await expect(loopButton(page)).not.toHaveClass(/on/);
  await page.keyboard.press("l");
  await expect(loopButton(page)).toHaveClass(/on/);
  await page.keyboard.press("l");
  await expect(loopButton(page)).not.toHaveClass(/on/);

  // Starts at 1 (TabViewer's own default) and SPEEDS is
  // [0.5, 0.75, 1, 1.25] - one press wraps forward to the end of the list.
  await expect(speedSelect(page)).toHaveValue("1");
  await page.keyboard.press("s");
  await expect(speedSelect(page)).toHaveValue("1.25");
  await page.keyboard.press("s");
  await expect(speedSelect(page)).toHaveValue("0.5");

  await expect(metronomeButton(page)).not.toHaveClass(/on/);
  await page.keyboard.press("n");
  await expect(metronomeButton(page)).toHaveClass(/on/);
  await page.keyboard.press("n");
  await expect(metronomeButton(page)).not.toHaveClass(/on/);

  await expect(countInButton(page)).not.toHaveClass(/on/);
  await page.keyboard.press("c");
  await expect(countInButton(page)).toHaveClass(/on/);
  await page.keyboard.press("c");
  await expect(countInButton(page)).not.toHaveClass(/on/);
});

test("T cycles the staff theme", async ({ page }) => {
  await openDemo(page);
  // Read the starting theme rather than assuming "parchment": staff_theme is
  // a SERVER setting (settings.svelte.js), not per-browser state, so a
  // theme changed by an earlier run - or a future test - must not make this
  // one flaky. Whatever it starts at, three presses cycle the full ring and
  // land back where it began, which is what is actually asserted.
  const themes = ["parchment", "noir", "print"];
  const start = await host(page).getAttribute("data-score-theme");
  let at = themes.indexOf(start);
  expect(at).toBeGreaterThanOrEqual(0);
  for (let i = 0; i < 3; i++) {
    await page.keyboard.press("t");
    at = (at + 1) % themes.length;
    await expect(host(page)).toHaveAttribute("data-score-theme", themes[at]);
    await expect(themeSelect(page)).toHaveValue(themes[at]);
  }
  expect(themes[at]).toBe(start); // back where it started - nothing leaked
});

test("1/2/3 switch the notation/tab/both profile", async ({ page }) => {
  await openDemo(page);
  // scoretab ("Both") is TabViewer's own initial profile.
  await expect(host(page)).toHaveAttribute("data-score-profile", "scoretab");
  await page.keyboard.press("1");
  await expect(host(page)).toHaveAttribute("data-score-profile", "score");
  await expect(profileButtons(page).nth(0)).toHaveClass(/on/);
  await page.keyboard.press("2");
  await expect(host(page)).toHaveAttribute("data-score-profile", "tab");
  await expect(profileButtons(page).nth(1)).toHaveClass(/on/);
  await page.keyboard.press("3");
  await expect(host(page)).toHaveAttribute("data-score-profile", "scoretab");
  await expect(profileButtons(page).nth(2)).toHaveClass(/on/);
});

test("the arrow keys move the cursor a beat and a bar, without starting playback", async ({
  page,
}) => {
  await openDemo(page);
  await expect(host(page)).toHaveAttribute("data-cursor-bar", "0");
  await expect(host(page)).toHaveAttribute("data-cursor-tick", "0");

  await page.keyboard.press("ArrowRight");
  await expect.poll(() => cursorTick(page)).toBeGreaterThan(0);
  const afterOneBeat = await cursorTick(page);
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => cursorTick(page)).toBeGreaterThan(afterOneBeat);
  await page.keyboard.press("ArrowLeft");
  await expect(host(page)).toHaveAttribute("data-cursor-tick", String(afterOneBeat));

  // Moving the cursor is not the same thing as playing it - the button must
  // still read "Play" throughout.
  await expect(playButton(page)).toHaveText(/Play/);

  await page.keyboard.press("ArrowDown");
  await expect(host(page)).toHaveAttribute("data-cursor-bar", "1");
  await page.keyboard.press("ArrowDown");
  await expect(host(page)).toHaveAttribute("data-cursor-bar", "2");
  await page.keyboard.press("ArrowUp");
  await expect(host(page)).toHaveAttribute("data-cursor-bar", "1");
  await expect(playButton(page)).toHaveText(/Play/);
});

test("many rapid ArrowRights never stall or step backwards (F5)", async ({ page }) => {
  // A real regression, not a hypothetical: ensureCursorPosition used to
  // compare tickPosition against the cached beat's start by EXACT equality,
  // and api.tickPosition's write and its own read-back were measured to not
  // always land in the same synchronous tick this file writes them in (see
  // stop()'s and nudgeLoopBoundary's own comments on the identical race for
  // api.playbackRange) - so a caller re-entering right after a write could
  // read a value one or two ticks off what was just written, look like the
  // position had moved some OTHER way, and reseed from the stale read. Under
  // load (40 rapid presses) this intermittently STALLED - re-deriving the
  // same beat repeatedly instead of stepping through it - measured directly
  // before the fix (a range-containment check instead of exact equality;
  // see ensureCursorPosition's own comment).
  //
  // Uses the metronome fixture (48 beats across 8 bars of 6/8), not the
  // demo sample (~34 beats): a stall shows up as two consecutive presses
  // reporting the same tick, and the demo sample's own beat count is close
  // enough to 40 that reaching its actual end partway through would look
  // exactly like the bug this test exists to catch.
  //
  // The reads below are deliberately bare, not expect.poll - this test's
  // whole point is what an IMMEDIATE read shows right after each press;
  // retrying would wait out the very race being checked for and could not
  // fail against the mutation that reintroduces it.
  await stubMetronomeScore(page);
  await page.goto("/#/score/1");
  await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
  let previous = await cursorTick(page);
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("ArrowRight");
    const tick = await cursorTick(page);
    expect(tick, `press ${i + 1}: stalled at the same tick as the previous press`).toBeGreaterThan(previous);
    previous = tick;
  }
});

test("Shift+arrows nudge the loop boundary", async ({ page }) => {
  await openDemo(page);
  await expect(host(page)).not.toHaveAttribute("data-loop-start-tick");
  await expect(host(page)).not.toHaveAttribute("data-loop-end-tick");

  await page.keyboard.press("Shift+ArrowRight");
  await expect.poll(() => loopEndTick(page)).not.toBeNull();
  const start = await loopStartTick(page);
  const firstEnd = await loopEndTick(page);
  expect(Number.isFinite(start)).toBe(true);
  expect(firstEnd).toBeGreaterThan(start);

  await page.keyboard.press("Shift+ArrowRight");
  await expect.poll(() => loopEndTick(page)).toBeGreaterThan(firstEnd);
  // The start boundary is untouched by nudging the end.
  await expect(host(page)).toHaveAttribute("data-loop-start-tick", String(start));

  await page.keyboard.press("Shift+ArrowLeft");
  await expect(host(page)).toHaveAttribute("data-loop-end-tick", String(firstEnd));
});

test("double-clicking a beat seeks to it and plays from there", async ({ page }) => {
  await openDemo(page);
  // alphaTab marks every rendered beat's own SVG group with a stable "bN"
  // class (N is its own internal beat id) - found by inspecting the DOM,
  // not documented, but a real per-beat element rather than the animated
  // playback cursor (.at-cursor-beat), which turned out NOT to be a usable
  // proxy for "where a beat is": it glides smoothly between notes while
  // playing, so its instantaneous on-screen position does not correspond to
  // any single beat's own hit-test area - confirmed by clicking it and
  // reading back which beat alphaTab's own beatMouseDown reported: always
  // the first one, wherever the cursor visually was. ".b10" is simply a beat
  // comfortably past the first bar in the bundled sample; nothing about the
  // number itself matters, only that it is not the very first beat.
  const beatTarget = page.locator(".at-host .b10").first();
  await expect(beatTarget).toBeVisible();
  const target = await beatTarget.boundingBox();
  expect(target).not.toBeNull();

  await expect(host(page)).toHaveAttribute("data-cursor-tick", "0");
  await expect(playButton(page)).toHaveText(/Play/);

  const x = target.x + target.width / 2;
  const y = target.y + target.height / 2;
  await page.mouse.dblclick(x, y);

  await expect(playButton(page)).toHaveText(/Pause/, { timeout: 10_000 });
  await expect.poll(() => cursorTick(page)).toBeGreaterThan(0);
});

test("each wired control's accessible name contains its key token", async ({ page }) => {
  await openDemo(page);
  async function nameOf(locator) {
    const el = locator.first();
    return (await el.getAttribute("aria-label")) ?? (await el.textContent());
  }
  await expect.poll(() => nameOf(playButton(page))).toMatch(/\(\(Space\)\)/);
  await expect.poll(() => nameOf(stopButton(page))).toMatch(/\(\(Backspace\)\)/);
  await expect.poll(() => nameOf(loopButton(page))).toMatch(/\(\(L\)\)/);
  await expect.poll(() => nameOf(metronomeButton(page))).toMatch(/\(\(N\)\)/);
  await expect.poll(() => nameOf(countInButton(page))).toMatch(/\(\(C\)\)/);
  await expect.poll(() => nameOf(speedSelect(page))).toMatch(/\(\(S\)\)/);
  await expect.poll(() => nameOf(themeSelect(page))).toMatch(/\(\(T\)\)/);
  await expect.poll(() => nameOf(profileButtons(page).nth(0))).toMatch(/\(\(1\)\)/);
  await expect.poll(() => nameOf(profileButtons(page).nth(1))).toMatch(/\(\(2\)\)/);
  await expect.poll(() => nameOf(profileButtons(page).nth(2))).toMatch(/\(\(3\)\)/);
});

// ------------------------------------------------------- the focus guard

test.describe("the focus guard", () => {
  test.beforeEach(async ({ page }) => {
    await stubMetronomeScore(page);
    await page.goto("/#/score/1");
    await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
  });

  async function openTagEditor(page) {
    await page.getByRole("button", { name: "+ tags" }).click();
    await expect(page.locator(".tags-input")).toBeVisible();
  }

  test("typing L into a focused text field does not toggle the loop", async ({ page }) => {
    await openTagEditor(page);
    await expect(loopButton(page)).not.toHaveClass(/on/);
    const input = page.locator(".tags-input");
    await input.click();
    await page.keyboard.type("lop");
    // The guard must not have swallowed the keystrokes either - a focus
    // guard that preventDefault()s everything would pass the "loop stayed
    // off" half of this test for the wrong reason (nothing reached the
    // input at all, which is worse than what it is meant to fix).
    await expect(input).toHaveValue("lop");
    await expect(loopButton(page)).not.toHaveClass(/on/);
  });

  test("Esc closes the open tag editor, even typed from inside it", async ({ page }) => {
    await openTagEditor(page);
    const input = page.locator(".tags-input");
    await input.click();
    await page.keyboard.type("draft");
    await page.keyboard.press("Escape");
    await expect(page.locator(".tags-input")).toHaveCount(0);
  });

  test("Space and L still work once focus has left the text field", async ({ page }) => {
    // The guard is about focus, not about "a text field exists somewhere on
    // the page" - closing back out of it must restore the ordinary keyboard.
    await openTagEditor(page);
    await page.keyboard.press("Escape");
    await expect(page.locator(".tags-input")).toHaveCount(0);
    await expect(playButton(page)).toHaveText(/Play/);
    await page.keyboard.press(" ");
    await expect(playButton(page)).toHaveText(/Pause/);
    await page.keyboard.press(" ");
    await page.keyboard.press("l");
    await expect(loopButton(page)).toHaveClass(/on/);
    // left as found, for whichever test in this file runs next
    await page.keyboard.press("l");
  });
});

// ------------------------------ single-pane desktop layouts: ScoreCompare

// ScoreCompare mounts a PdfViewer and a TabViewer AT THE SAME TIME and only
// hides whichever pane is not on screen with CSS (see its own snippets) - it
// never unmounts either - so without the `active` prop both added to
// PdfViewer.svelte and TabViewer.svelte, a single Space press on the PDF
// pane would ALSO have toggled the hidden staff pane's playback, and vice
// versa. NEITHER of these two tests puts the app in gig mode - see the
// "gig mode itself" suite below for that; these are the two single-pane
// DESKTOP layouts, which is what `active` actually keys off.
test.describe("keyboard shortcuts stay scoped to the visible pane in ScoreCompare", () => {
  test.beforeEach(async ({ page }) => {
    await stubScoreApi(page, transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE }));
    await page.goto("/#/score/1");
    await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
  });

  test("Space does nothing to the staff pane while only the PDF pane is shown", async ({ page }) => {
    await page.getByRole("button", { name: "PDF", exact: true }).click();
    await page.keyboard.press(" ");
    // A moment for a wrongly-active handler to have acted, then confirm
    // nothing did - TabViewer's own Play button (still in the DOM, just
    // hidden behind the PDF pane) never left "Play".
    await page.waitForTimeout(300);
    await expect(playButton(page)).toHaveText(/Play/);
  });

  test("Space toggles playback once the staff pane is the one shown", async ({ page }) => {
    await page.getByRole("button", { name: "Staff", exact: true }).click();
    // Move focus off the layout button before testing Space - it is a
    // BUTTON, so it now (rightly, see the focus-guard suite's own F3
    // coverage) owns Space itself while focused, and pressing it here would
    // re-click "Staff" rather than reach TabViewer's transport at all.
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press(" ");
    await expect(playButton(page)).toHaveText(/Pause/);
    await page.keyboard.press(" "); // left as found
  });
});

// ------------------------------------------------------ side-by-side layout

// "side" is the DEFAULT layout the instant a score has a transcription (see
// ScoreCompare's own `layout` initial value) and it is where main already
// turns PDF pages on Space/arrow keys - regression-tested here because
// nothing in the original suite exercised this layout's keyboard at all,
// which is exactly how the regression shipped green: `active` on the PDF
// pane was written as `activeLayout === "pdf"`, silently OFF the moment
// "side" - the common case - was showing.
test.describe("side-by-side layout: PDF page-turning keeps its keys", () => {
  test.beforeEach(async ({ page }) => {
    await stubScoreApi(page, transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE }));
    // Overrides the /file route stubScoreApi just registered with a real
    // 2-page PDF - Playwright tries the most-recently-registered matching
    // route first, so this one wins. MIN_PDF (what stubScoreApi uses) has
    // exactly one page and cannot demonstrate a page actually turning.
    await page.route("**/api/scores/1/file", (route) =>
      route.fulfill({ body: buildMultiPagePdf(2), contentType: "application/pdf" }),
    );
    await page.goto("/#/score/1");
    await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
    // "side" is the default already (score.has_transcription is true in
    // SCORE), asserted rather than assumed so a future default change fails
    // here loudly instead of this spec silently testing the wrong layout.
    await expect(page.getByRole("button", { name: "Side by side", exact: true })).toHaveClass(/on/);
  });

  test("ArrowRight turns the PDF page", async ({ page }) => {
    await expect(page.locator(".hud span")).toHaveText("1 / 2");
    await page.keyboard.press("ArrowRight");
    await expect(page.locator(".hud span")).toHaveText("2 / 2");
  });

  test("Space does not also toggle the staff pane's playback", async ({ page }) => {
    await page.keyboard.press(" ");
    await page.waitForTimeout(300);
    await expect(playButton(page)).toHaveText(/Play/);
  });
});

// ------------------------------------------------------------- gig mode

// Gig mode is the pedal-driven mode #92 itself points to as the reason this
// all has to stay unambiguous - a pedal sends nothing but arrow keys, and
// there is no mouse to fall back on if the wrong pane answers. Entered here
// for real (Viewer.svelte's own "f" shortcut, unmodified by this issue)
// rather than only inferred from the single-pane tests above.
test.describe("gig mode itself", () => {
  test.beforeEach(async ({ page }) => {
    await stubScoreApi(page, transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE }));
    await page.route("**/api/scores/1/file", (route) =>
      route.fulfill({ body: buildMultiPagePdf(2), contentType: "application/pdf" }),
    );
    await page.goto("/#/score/1");
    await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
  });

  test("from the default (side-by-side) layout, gig mode forces the PDF pane and its arrow keys turn pages", async ({
    page,
  }) => {
    await expect(page.getByRole("button", { name: "Side by side", exact: true })).toHaveClass(/on/);
    await page.keyboard.press("f");
    // ScoreCompare's own toolbar - the layout picker included - only renders
    // outside gig mode (see its `{#if !gigMode}`), so its disappearance is
    // the evidence gig mode is genuinely active, not merely that "f" was
    // pressed.
    await expect(page.getByRole("button", { name: "Side by side", exact: true })).toHaveCount(0);
    await expect(page.locator(".hud span")).toHaveText("1 / 2");
    await page.keyboard.press("ArrowRight");
    await expect(page.locator(".hud span")).toHaveText("2 / 2");
  });

  test("from the staff layout, gig mode keeps the staff pane and Space still plays/pauses it", async ({ page }) => {
    await page.getByRole("button", { name: "Staff", exact: true }).click();
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press("f");
    await expect(page.getByRole("button", { name: "Staff", exact: true })).toHaveCount(0);
    await page.keyboard.press(" ");
    await expect(page.locator("button.primary")).toHaveText(/Pause/);
    await page.keyboard.press(" "); // left as found
  });
});
