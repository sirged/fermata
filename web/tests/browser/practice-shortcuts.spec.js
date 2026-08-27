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
import { stubMetronomeScore } from "./fixtures/metronome-score.js";

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
  expect(await cursorBar(page)).toBeGreaterThan(0);
  await page.keyboard.press(" ");
  await expect(playButton(page)).toHaveText(/Pause/);
  await page.keyboard.press("Backspace");
  await expect(playButton(page)).toHaveText(/Play/);
  expect(await cursorTick(page)).toBe(0);
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
  expect(await cursorBar(page)).toBe(0);
  expect(await cursorTick(page)).toBe(0);

  await page.keyboard.press("ArrowRight");
  const afterOneBeat = await cursorTick(page);
  expect(afterOneBeat).toBeGreaterThan(0);
  await page.keyboard.press("ArrowRight");
  expect(await cursorTick(page)).toBeGreaterThan(afterOneBeat);
  await page.keyboard.press("ArrowLeft");
  expect(await cursorTick(page)).toBe(afterOneBeat);

  // Moving the cursor is not the same thing as playing it - the button must
  // still read "Play" throughout.
  await expect(playButton(page)).toHaveText(/Play/);

  await page.keyboard.press("ArrowDown");
  expect(await cursorBar(page)).toBe(1);
  await page.keyboard.press("ArrowDown");
  expect(await cursorBar(page)).toBe(2);
  await page.keyboard.press("ArrowUp");
  expect(await cursorBar(page)).toBe(1);
  await expect(playButton(page)).toHaveText(/Play/);
});

test("Shift+arrows nudge the loop boundary", async ({ page }) => {
  await openDemo(page);
  expect(await host(page).getAttribute("data-loop-start-tick")).toBeNull();
  expect(await host(page).getAttribute("data-loop-end-tick")).toBeNull();

  await page.keyboard.press("Shift+ArrowRight");
  const start = Number(await host(page).getAttribute("data-loop-start-tick"));
  const firstEnd = Number(await host(page).getAttribute("data-loop-end-tick"));
  expect(Number.isFinite(start)).toBe(true);
  expect(firstEnd).toBeGreaterThan(start);

  await page.keyboard.press("Shift+ArrowRight");
  const secondEnd = Number(await host(page).getAttribute("data-loop-end-tick"));
  expect(secondEnd).toBeGreaterThan(firstEnd);
  // The start boundary is untouched by nudging the end.
  expect(Number(await host(page).getAttribute("data-loop-start-tick"))).toBe(start);

  await page.keyboard.press("Shift+ArrowLeft");
  const backDown = Number(await host(page).getAttribute("data-loop-end-tick"));
  expect(backDown).toBe(firstEnd);
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

  expect(await cursorTick(page)).toBe(0);
  await expect(playButton(page)).toHaveText(/Play/);

  const x = target.x + target.width / 2;
  const y = target.y + target.height / 2;
  await page.mouse.dblclick(x, y);

  await expect(playButton(page)).toHaveText(/Pause/, { timeout: 10_000 });
  expect(await cursorTick(page)).toBeGreaterThan(0);
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
