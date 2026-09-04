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
// value sampled immediately after a keypress with no wait. See pull request
// #142 (issue #92) for how these were checked against a mutation of the
// behaviour they claim - that record used to be copied into
// tests/minimum-tests.js's own comment, which #126 replaced with per-file
// entries under tests/spec-floors/ that do not have room to carry it.
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

/**
 * One 4/4 bar, two voices - voice 1 two half notes (onsets at 0 and half
 * the bar), voice 2 four quarter notes (onsets at every quarter, including
 * two INTERIOR to voice 1's own half notes). <backup> is what MusicXML
 * itself uses to return the cursor to measure-start between voices - not
 * this file's invention. divisions=480 matches metronome-score.js's own
 * convention, for the same fixture-shape reason that file states.
 */
const MULTI_VOICE_MUSICXML = `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>480</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>960</duration><voice>1</voice><type>half</type></note>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>960</duration><voice>1</voice><type>half</type></note>
      <backup><duration>1920</duration></backup>
      <note><pitch><step>E</step><octave>4</octave></pitch><duration>480</duration><voice>2</voice><type>quarter</type></note>
      <note><pitch><step>F</step><octave>4</octave></pitch><duration>480</duration><voice>2</voice><type>quarter</type></note>
      <note><pitch><step>G</step><octave>4</octave></pitch><duration>480</duration><voice>2</voice><type>quarter</type></note>
      <note><pitch><step>A</step><octave>4</octave></pitch><duration>480</duration><voice>2</voice><type>quarter</type></note>
    </measure>
  </part>
</score-partwise>
`;

function multiVoiceScoreMeta() {
  // file_type not "pdf" is what routes Viewer.svelte to TabViewer directly
  // - see metronome-score.js's own scoreMeta() for the identical reasoning.
  return {
    id: 1,
    title: "multi-voice fixture",
    composer: "",
    source: "",
    file_type: "musicxml",
    has_transcription: false,
    favorite: false,
    content_kind: "notation",
    tags: [],
  };
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

/**
 * The PDF pane's live scroll geometry: where the scroller is, and how tall
 * each page has actually rendered. Everything the page-turn tests below
 * measure comes from here rather than from a screenshot or a fixed sleep.
 */
const pdfGeometry = (page) =>
  page.evaluate(() => {
    const scroller = document.querySelector(".pages");
    const first = document.querySelector('.pdf-page[data-page="1"]');
    const rect = scroller.getBoundingClientRect();
    const topOf = (el) => el.getBoundingClientRect().top - rect.top + scroller.scrollTop;
    return {
      scrollTop: scroller.scrollTop,
      pageHeight: first.getBoundingClientRect().height,
      pageOneTop: topOf(first),
      pageTwoTop: topOf(document.querySelector('.pdf-page[data-page="2"]')),
    };
  });

/**
 * What the PDF pane is showing, and what its indicator says about it, read
 * in ONE evaluate so the two can never be sampled either side of a change.
 *
 * "Showing" is measured here rather than asked of the component: the page
 * displaying the largest fraction of itself, which is what an intersection
 * ratio is and what PdfViewer's own observer ranks its entries by. Reading
 * it off the rects is what makes an assertion that the two agree an
 * assertion about the pane rather than the indicator being compared with
 * itself.
 */
const pdfIndicatorState = (page) =>
  page.evaluate(() => {
    const scroller = document.querySelector(".pages");
    const view = scroller.getBoundingClientRect();
    const ratios = [...document.querySelectorAll(".pdf-page")].map((canvas) => {
      const rect = canvas.getBoundingClientRect();
      const overlap = Math.min(rect.bottom, view.bottom) - Math.max(rect.top, view.top);
      return { page: Number(canvas.dataset.page), ratio: rect.height ? Math.max(0, overlap) / rect.height : 0 };
    });
    const best = ratios.reduce((a, b) => (b.ratio > a.ratio ? b : a));
    return {
      hud: document.querySelector(".hud span").textContent.trim(),
      shown: best.page,
      ratios: ratios.map((r) => `${r.page}:${r.ratio.toFixed(2)}`).join(" "),
    };
  });

/**
 * Waits for the PDF pane's indicator to agree with the page the pane is
 * actually showing, reporting both when it does not. Polled rather than read
 * once, because the correction is made a couple of frames after the
 * re-render's own scroll restore; a disagreement that is never corrected -
 * the bug this covers - simply never agrees.
 */
async function pdfIndicatorAgrees(page, pageCount) {
  await expect
    .poll(async () => {
      const at = await pdfIndicatorState(page);
      return at.hud === `${at.shown} / ${pageCount}`
        ? "agrees"
        : `HUD reads "${at.hud}" over a pane showing page ${at.shown} (ratios ${at.ratios})`;
    })
    .toBe("agrees");
}

/**
 * Waits for the PDF pane to have finished re-rendering its canvases at the
 * width it is going to keep - the readiness barrier the page-turn tests
 * below need and #168's did not.
 *
 * Any layout change that widens or narrows the pane (entering gig mode,
 * which drops the staff pane; a viewport resize) makes PdfViewer re-render
 * every canvas at a new width, 200ms after the resize stops. Until that
 * lands, the pages on screen are still the OLD ones, at the old height - and
 * a half-page turn is measured off the current rendered height, so the same
 * keypress moves the reader a different distance either side of it.
 *
 * The condition is PdfViewer's own: `Math.min(clientWidth - 32, 1100)` (see
 * its computeWidth), read back off the DOM rather than restated as a number,
 * so a change to how the pane sizes itself moves both together. Waiting on
 * the width the component computes for the container it is actually in is a
 * fact about the render having completed, not an interval hoped to be long
 * enough.
 */
async function pdfPagesRenderedAtSettledWidth(page, timeout) {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const scroller = document.querySelector(".pages");
        const pages = [...document.querySelectorAll(".pdf-page")];
        if (!scroller || !pages.length) return "no pages yet";
        const want = Math.min(scroller.clientWidth - 32, 1100);
        const widths = [...new Set(pages.map((p) => Math.round(p.getBoundingClientRect().width)))];
        return widths.length === 1 && widths[0] === want ? "settled" : `rendered ${widths} of ${want}`;
      }, timeout ? { timeout } : undefined),
    )
    .toBe("settled");
}

/**
 * Waits for the PDF pane's scroll to come to rest at `target`, naming what
 * that target is so a failure says where the pane went instead of only that
 * two numbers differ. A tolerance of a pixel, because a smooth scroll lands
 * on device pixels and the targets here are computed in CSS ones.
 */
async function pdfScrollSettlesAt(page, target, what) {
  await expect
    .poll(async () => {
      const at = (await pdfGeometry(page)).scrollTop;
      return Math.abs(at - target) <= 1 ? what : `at ${Math.round(at)}, not ${what} (${Math.round(target)})`;
    })
    .toBe(what);
}

/**
 * The PDF pane's count of re-render scroll restores completed so far, or null
 * if none has ever completed. `PdfViewer` writes `data-render-settle-seq` on
 * the scroller in exactly one place - the post-restore double-rAF in its
 * flushResize - and never removes it.
 */
async function pdfRenderSettleStamp(page) {
  return page.evaluate(() => document.querySelector(".pages")?.dataset.renderSettleSeq ?? null);
}

/**
 * Waits for a re-render's scroll restore to COMPLETE, past a count taken
 * before whatever was going to cause it.
 *
 * `pdfScrollSettlesAt` above is the right barrier when the test knows the
 * target. The tests below deliberately do not: they press INTO a live
 * re-render, and where that leaves the reader depends on which page height
 * the press was measured against. Measured on this box at 1280 wide, 60 runs:
 * 0.4755 and 0.4864 of a page. A CI runner came to rest at 473px, which is
 * neither of the two offsets this box produces - that number is unexplained,
 * NOT a measured pane geometry, and no barrier can name it in advance.
 *
 * So the barrier waits for the viewer's own claim instead. The settled-width
 * barrier these tests already had cannot stand in for it: it is about canvas
 * WIDTHS, which reach their final value INSIDE PdfViewer's re-render loop -
 * each canvas is sized before its page is drawn - while the loop restores the
 * scroll only after the LAST page has finished drawing. After it sits a
 * window where the pages are already their settled size but the reader is
 * still at the offset the pre-render geometry produced, and a fraction read
 * there is a pre-render offset over a post-render page height. Measured at
 * the moment that barrier returns, with the restore delayed 40 frames past
 * the last page's draw, 5 of 5 runs: scrollTop 313 over an 1100px page,
 * 0.2627 - under the 0.35 the indicator test asserts.
 *
 * Two earlier versions of this barrier waited on something weaker, and both
 * are worth naming because both LOOKED like they worked:
 *
 * - two consecutive animation frames with an unchanged scrollTop. Decorative:
 *   inside the delayed-restore window the pane is perfectly still, so it
 *   returned "at rest" on its first evaluation.
 * - the viewer's rest counter, which was also stamped by a quiet-scroll timer
 *   200ms after the last scroll event. That timer has nothing to do with a
 *   re-render, and on the clean head it fired BEFORE one. Traced on the
 *   half-page-turn test, 8 of 8 runs, milliseconds from the same clock:
 *   `690 restore | 707 stamp | 883 turn starts | 1086 QUIET STAMP | 1132
 *   re-render starts | 1140 restore | 1173 stamp`. The barrier's baseline was
 *   absent in 8 of 8 (the turn had just removed the attribute), so any stamp
 *   satisfied it, and the one at 1086 is the turn going quiet - 46ms before
 *   the re-render started and 54ms before the restore this barrier exists to
 *   wait for. It returned on its first poll, at the pre-render offset.
 *
 * Stillness is not the property that matters, and neither is rest; having
 * restored is. So the viewer now writes that and nothing else, this waits for
 * the count to ADVANCE past a value captured before the press, and an absent
 * baseline counts as 0 - safe, because the only thing that ever writes this
 * attribute is a restore that has completed and painted. It throws on timeout
 * naming the attribute rather than falling through to the assertion.
 *
 * One obligation it puts on the caller, found by the same construction and
 * not by reading: the baseline only rules out re-renders that have already
 * STAMPED, so a re-render already in flight when the baseline is taken will
 * satisfy this barrier in place of the one the test is waiting for. Entering
 * gig mode starts exactly such a re-render. Traced with the restore delayed 40
 * frames: gig mode's flush restored at 1793 and stamped at 1827, the resize
 * under test started its flush at 2086, and the barrier returned at 2376 on
 * gig mode's stamp - after A restore, 112px away from where the resize's own
 * would leave the reader. So a test that enters gig mode first has to wait
 * for THAT re-render to have restored too, before taking its baseline, and
 * the width barrier is not that wait either. Both callers below do.
 *
 * One thing worth saying about the indicator test in particular, because it
 * was previously written down the wrong way round: its `pdfIndicatorAgrees`
 * poll does NOT agree trivially there. Measured at the pre-restore offset,
 * 3 of 3: the HUD reads "2 / 2" over a pane showing page one, because at the
 * narrow pre-render geometry a half turn brought page two past the 0.4
 * intersection threshold and the observer said so. The poll therefore waits
 * for the re-derivation in flushResize, which happens in the same frame this
 * stamp is written - so today it screens the early read by accident. That
 * accident is geometric: raise the threshold so the observer never fires
 * mid-turn and the HUD agrees at once, and with this barrier removed the test
 * reads 0.2627 in 5 of 5 runs and fails on the band. This barrier is what
 * makes the screening deliberate and independent of the threshold.
 */
async function pdfRestoreSettlesPast(page, stamp, timeout) {
  const was = stamp === null ? "none yet" : stamp;
  await expect
    .poll(
      async () => {
        const now = await pdfRenderSettleStamp(page);
        if (now === null) return `data-render-settle-seq absent, no re-render has restored (was ${was})`;
        return Number(now) > Number(stamp ?? 0)
          ? "restored"
          : `data-render-settle-seq still ${now} (was ${was})`;
      },
      timeout ? { timeout } : undefined,
    )
    .toBe("restored");
}

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
  // The UNTOUCHED starting tick was measured, occasionally, reporting 1
  // instead of 0 - an internal rounding artifact of the player becoming
  // ready (see the identical note on the F6 multi-voice test below), not
  // this test's to pin down - so it is only checked loosely here.
  expect(await cursorTick(page)).toBeLessThanOrEqual(1);

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

test("nudging the loop boundary many times does not drift the cursor (N1)", async ({ page }) => {
  // nudgeLoopBoundary saves the cursor's tick, writes a new playbackRange
  // (whose own setter relocates tickPosition as a side effect), then
  // restores the saved value - see its own comment. The value restored used
  // to be a raw api.tickPosition READ-BACK, and reading it back after
  // writing it was measured returning a DETERMINISTIC +1 on this fixture
  // (write 5280, read back 5281) - not an occasional rounding artifact, a
  // consistent one, so saving and restoring that read-back accumulated one
  // tick of drift on every single nudge: -462 ticks over 600 nudges in the
  // original measurement, eventually landing cursor stepping a beat behind
  // wherever it should be. Fixed by restoring the beat's own CANONICAL tick
  // (computed from plain integers on the parsed model) instead of the
  // engine's lossy read-back - asserted here by checking the cursor is
  // back to the exact tick it started at after every nudge, not only the
  // first or the last.
  //
  // Paced with a short wait between presses, deliberately: nudging far
  // faster than any human keypress rate hits an unrelated, separate timing
  // interaction in alphaTab's own cursor-transition animation (see
  // nudgeLoopBoundary's own comment) - not reproducible at this, or any
  // human-realistic, pace.
  await openDemo(page);
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => cursorTick(page)).toBeGreaterThan(0);
  const anchor = await cursorTick(page);
  for (let i = 0; i < 10; i++) {
    await page.keyboard.press("Shift+ArrowRight");
    await page.waitForTimeout(50);
    await expect(host(page), `nudge ${i + 1} drifted the cursor`).toHaveAttribute(
      "data-cursor-tick",
      String(anchor),
    );
  }
});

test("a repeated section: forward arrow-key stepping stays monotonic across it (F1)", async ({ page }) => {
  // Beat.absolutePlaybackStart (and api.tickCache.getBeatStart) are built
  // from NOTATED bar order - one tick per bar however many times a repeat
  // plays it - while api.tickPosition/api.playbackRange/
  // api.tickCache.masterBars are the repeat-EXPANDED PLAYBACK order the
  // generated MIDI actually runs on. Confusing the two was #92's most
  // severe bug: measured directly on this exact fixture (two 4/4 bars
  // repeated once, then a 6/8 bar), stepping forward past the repeat and
  // one beat further moved the cursor BACKWARDS by 6720 ticks. See
  // beatPositionAtTick/positionTick's own block comment in score-render.js
  // for the fix.
  await stubMetronomeScoreRepeat(page);
  await page.goto("/#/score/4");
  await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
  // 4 beats/bar x 2 bars x 2 passes + 6 beats in the final 6/8 bar = 22
  // beats total - stepped past the end (24 presses) to also confirm it
  // clamps there instead of wrapping.
  // A short poll per press, not a bare read (unlike the F5 stress test
  // above, which deliberately stays bare because retrying would wait out
  // the exact stale-reseed race it exists to catch): under load, running
  // inside the full suite rather than in isolation, a single read was
  // measured landing before the keydown's own synchronous handler had
  // actually run - test-harness event-dispatch timing, not a reseed bug,
  // confirmed by its being unreproducible over a dozen isolated runs of
  // this same test alone. A genuine backward step or stall does not
  // self-correct within a few hundred milliseconds; a delayed read does.
  let previous = -1;
  for (let i = 0; i < 24; i++) {
    await page.keyboard.press("ArrowRight");
    const floor = previous;
    await expect
      .poll(() => cursorTick(page), { timeout: 500, message: `press ${i + 1}: stepped backwards or stalled` })
      .toBeGreaterThanOrEqual(floor);
    previous = await cursorTick(page);
  }
});

test("a repeated section: double-clicking a beat AFTER the repeat lands on its real playback tick (F1)", async ({
  page,
}) => {
  // The other half of the same bug: a bar placed after a repeated section
  // (never itself repeated) still needs the repeat's extra passes counted
  // to land on its OWN correct, later tick. Measured directly: double-
  // clicking the 6/8 bar used to seek to 10080 - inside the repeat's
  // second pass - when it actually plays at 15360.
  await stubMetronomeScoreRepeat(page);
  await page.goto("/#/score/4");
  await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
  // "b8" is the first beat of the third notated bar (two 4/4 bars of 4
  // beats each come first, b0-b7) - alphaTab's own per-beat class, found
  // by DOM inspection, the same one the double-click test below uses.
  const target = await page.locator(".at-host .b8").first().boundingBox();
  expect(target).not.toBeNull();
  await page.mouse.dblclick(target.x + target.width / 2, target.y + target.height / 2);
  await expect(playButton(page)).toHaveText(/Pause/, { timeout: 10_000 });
  await expect(host(page)).toHaveAttribute("data-cursor-tick", "15360");
});

test("cursor stepping visits a second voice's interior onsets, not just the first voice's (F6)", async ({ page }) => {
  // firstBeatOfBar used to read only bar.voices[0] (structurally, the first
  // voice) and follow its own Beat.nextBeat chain, so a second voice's
  // notes that fall BETWEEN the first voice's own beats were never visited
  // at all. Fixed as a side effect of rebuilding cursor stepping on
  // BeatTickLookup for F1 (see beatPositionAtTick's own comment): its chain
  // already merges every voice's onsets into one timeline, which this
  // fixture (one voice of two half notes, a second of four INTERIOR
  // quarter notes) is built to prove directly rather than infer.
  await page.route("**/api/scores/1", (route) =>
    route.fulfill({ json: multiVoiceScoreMeta() }),
  );
  await page.route("**/api/scores/1/file", (route) =>
    route.fulfill({ body: MULTI_VOICE_MUSICXML, contentType: "application/vnd.recordare.musicxml+xml" }),
  );
  await page.route("**/api/scores/1/practice", (route) =>
    route.fulfill({ json: { total_seconds: 0, sessions: [] } }),
  );
  await page.goto("/#/score/1");
  await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
  // The UNTOUCHED starting tick was measured, occasionally, reporting 1
  // instead of 0 - an internal rounding artifact of the player becoming
  // ready, unrelated to voices or repeats and not this test's to pin down -
  // so it is only checked loosely. Every tick AFTER that comes from this
  // file's own moveCursorBeat, which writes an exact integer
  // (masterBar.start + beatLookup.start, both plain integers - see
  // positionTick), and was measured to never carry the same drift: what
  // matters here, the STEP-TO-STEP progression, is asserted on exact values.
  // Voice 1 alone (two half notes) would only ever produce [1920] as a
  // single further step - the interior 960 and 2880 onsets only exist in
  // voice 2's four quarter notes.
  const start = await cursorTick(page);
  expect(start).toBeLessThanOrEqual(1);
  // A short poll per press, not a bare read - see the identical reasoning
  // on the F1 monotonicity test above (a stale read under full-suite load,
  // not a reseed bug: unreproducible over a dozen isolated runs, and a
  // wrong onset would not self-correct within a few hundred milliseconds
  // the way a delayed read does).
  const expected = [960, 1920, 2880];
  const ticks = [];
  for (let i = 0; i < 3; i++) {
    await page.keyboard.press("ArrowRight");
    await expect
      .poll(() => cursorTick(page), { timeout: 500, message: `press ${i + 1}` })
      .toBe(expected[i]);
    ticks.push(await cursorTick(page));
  }
  expect(ticks).toEqual(expected);
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

  // The UNTOUCHED starting tick was measured, occasionally, reporting 1
  // instead of 0 (see the identical note on the F6 multi-voice test below)
  // - checked loosely, since what this test is actually about is the
  // double-click landing well PAST it, not the exact starting value.
  const start = await cursorTick(page);
  expect(start).toBeLessThanOrEqual(1);
  await expect(playButton(page)).toHaveText(/Play/);

  const x = target.x + target.width / 2;
  const y = target.y + target.height / 2;
  await page.mouse.dblclick(x, y);

  await expect(playButton(page)).toHaveText(/Pause/, { timeout: 10_000 });
  await expect.poll(() => cursorTick(page)).toBeGreaterThan(start);
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

  test("Esc closes the open tag editor, even typed from inside it, and discards the draft", async ({ page }) => {
    await openTagEditor(page);
    const input = page.locator(".tags-input");
    await input.click();
    await page.keyboard.type("draft");
    await page.keyboard.press("Escape");
    await expect(page.locator(".tags-input")).toHaveCount(0);
    // Esc is the FIRST cancel-without-saving path this editor has ever had
    // - there was no "close" button before #92 wired one to a key, only
    // Save - so this is genuinely new behaviour, asserted explicitly rather
    // than left to be inferred from the editor merely having closed: what
    // was typed is gone, matching an ordinary Cancel anywhere else on the
    // web. Reopening re-seeds from the score's own (empty) tags, not from
    // the abandoned "draft" - see Viewer.svelte's own comment on this.
    await openTagEditor(page);
    await expect(page.locator(".tags-input")).toHaveValue("");
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

  test("Space on a focused button activates the button, not play/pause (F3)", async ({ page }) => {
    // onKey used to preventDefault() Space unconditionally, which suppresses
    // a focused BUTTON's own native Space-activates-click default action
    // regardless of who called preventDefault first - so tabbing to (or
    // clicking) the Loop button and pressing Space started playback instead
    // of toggling Loop. Clicking Loop both toggles it AND leaves it focused
    // (an ordinary browser behaviour, not this file's doing), so Space here
    // exercises the fix directly: it should re-activate the button Loop
    // already IS - toggling it back off - rather than reach TabViewer's
    // transport at all.
    await loopButton(page).click();
    await expect(loopButton(page)).toHaveClass(/on/);
    await page.keyboard.press(" ");
    await expect(loopButton(page)).not.toHaveClass(/on/);
    await expect(playButton(page)).toHaveText(/Play/);
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
    // BUTTON, so it now (rightly - see "the focus guard" describe block's
    // own "Space on a focused button activates the button, not play/pause
    // (F3)" test) owns Space itself while focused, and pressing it here
    // would re-click "Staff" rather than reach TabViewer's transport at all.
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
    // The canvases, NOT the HUD, are what proves this pane can turn a page.
    // PdfViewer publishes the page COUNT the moment the document's metadata
    // parses - so ".hud span" reads "1 / 2" a few hundred milliseconds
    // before renderAllPages has appended anything to scroll TO (measured on
    // an idle machine: HUD at 293ms with zero canvases, page 2's canvas at
    // 327ms; CI load widens that gap). Waiting only on the HUD is what made
    // this test fail on CI twice - issue #168 - and it is not a slow-product
    // flake to be waited out: the press inside that window was CONSUMED and
    // dropped, so no retry of the assertion could ever have recovered it.
    // PdfViewer no longer drops it (it remembers the turn), and this waits
    // for the real barrier so the test is not resting on that recovery.
    await expect(page.locator(".pdf-page")).toHaveCount(2);
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

// The other half of #168: the test above now waits for the pages, but the
// PRODUCT still has to survive a reader who does not. A pedal sends nothing
// but arrow keys and there is no mouse to fall back on (issue #106's own
// reasoning), so a tap on a score that is still loading has to land - and it
// used to be swallowed whole, because PdfViewer accepted the key
// (preventDefault) and then scrolled to a `[data-page]` element that did not
// exist yet, via an `?.` that made the miss invisible.
//
// Held open deliberately here rather than raced for: the PDF body is not
// released until AFTER the key has been pressed, so the press provably lands
// in the window this is about instead of landing there only when a loaded CI
// runner happens to be slow. That makes the "1 / …" below - PdfViewer's own
// wording for a page count it does not have yet - a fact of the test, not a
// hope.
test.describe("a page turn pressed before the PDF has rendered", () => {
  test("is honoured once the pages arrive, not dropped", async ({ page }) => {
    await stubScoreApi(page, transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE }));
    let release;
    const held = new Promise((resolve) => (release = resolve));
    await page.route("**/api/scores/1/file", async (route) => {
      await held;
      await route.fulfill({ body: buildMultiPagePdf(2), contentType: "application/pdf" });
    });
    await page.goto("/#/score/1");
    await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: "Side by side", exact: true })).toHaveClass(/on/);
    // Nothing of the document has been read yet - no page count, no canvases.
    await expect(page.locator(".hud span")).toHaveText("1 / …");
    await expect(page.locator(".pdf-page")).toHaveCount(0);

    await page.keyboard.press("ArrowRight");
    release();

    await expect(page.locator(".pdf-page")).toHaveCount(2);
    await expect(page.locator(".hud span")).toHaveText("2 / 2");
  });

  // The write that turn owes the library, which is not the same claim as the
  // page it lands on (#229 review).
  //
  // A turn pressed before any canvas exists is remembered and honoured, and
  // honouring it also REVOKES the stored-page restore (turnedBeforeRestore):
  // the reader asked to be somewhere, so they are not to be yanked off it.
  // On a score stored at page eight, ArrowLeft resolves to page one - which
  // is the page the indicator already named - so the turn moves the reader
  // without moving the indicator, and only the debounced save records that
  // the reader is no longer where the database thinks. Skip that save and
  // the two disagree silently until the score is reopened, at which point
  // the reader is thrown to page eight by a restore that is now wrong.
  //
  // Held open, not raced: the PDF body is released only after the press.
  test("a turn honoured before the pages existed is saved, even when it names the page already shown", async ({
    page,
  }) => {
    await stubScoreApi(page, transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE }));
    // Overrides stubScoreApi's own /api/scores/1 route (most recent wins) to
    // give the score a stored page far from page one, and to record what the
    // viewer writes back to it.
    const written = [];
    await page.route("**/api/scores/1", (route) => {
      if (route.request().method() === "PATCH") written.push(route.request().postDataJSON());
      return route.fulfill({ json: { ...SCORE, last_page: 8 } });
    });
    let release;
    const held = new Promise((resolve) => (release = resolve));
    await page.route("**/api/scores/1/file", async (route) => {
      await held;
      await route.fulfill({ body: buildMultiPagePdf(20), contentType: "application/pdf" });
    });
    await page.goto("/#/score/1");
    await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
    await expect(page.locator(".pdf-page")).toHaveCount(0);

    // Backwards from page one: goto() clamps it to page one, so this is the
    // turn that asks for the page the indicator is already showing.
    await page.keyboard.press("ArrowLeft");
    release();

    await expect(page.locator(".pdf-page")).toHaveCount(20, { timeout: 15_000 });
    await expect(page.locator(".hud span")).toHaveText("1 / 20");
    // The turn was honoured: page one, not the stored page eight.
    const at = await pdfGeometry(page);
    expect(Math.abs(at.scrollTop - at.pageOneTop)).toBeLessThanOrEqual(2);
    // And the library was told, which is the half a page-turn that does not
    // move the indicator can still lose. Polled: the save is debounced.
    await expect
      .poll(() => written.map((body) => body.last_page).join(","))
      .toBe("1");
  });

  // The same press, in gig mode, which is where it actually comes from: a
  // pedal tap on a score that has just been opened, with no mouse to fall
  // back on (issue #106's reasoning, and #92's).
  //
  // Gig mode is not the same code path. turn() takes its half-page branch
  // there, and only falls through to goto() - the one that can remember a
  // turn it cannot yet perform - while no canvas exists to measure a half
  // page against. So "a press is honoured once the pane settles" has to be
  // held open in gig mode too rather than inferred from the side-by-side
  // test above. #219's rewrite of the gig page-turn test deliberately waits
  // for the pane before pressing, which is right for what that test is
  // about and leaves nothing standing here; this is that cover, and unlike
  // the press it replaces it is held open rather than raced for.
  test("is honoured in gig mode too, where a pedal tap comes from", async ({ page }) => {
    await stubScoreApi(page, transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE }));
    let release;
    const held = new Promise((resolve) => (release = resolve));
    await page.route("**/api/scores/1/file", async (route) => {
      await held;
      await route.fulfill({ body: buildMultiPagePdf(2), contentType: "application/pdf" });
    });
    await page.goto("/#/score/1");
    await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });

    await page.keyboard.press("f");
    // The layout picker only renders outside gig mode, so its disappearance
    // is the evidence gig mode is genuinely on - same check, same reason, as
    // the gig describe below.
    await expect(page.getByRole("button", { name: "Side by side", exact: true })).toHaveCount(0);
    // Nothing of the document has been read yet: no page count, no canvases,
    // and so nothing for a half page to be measured against.
    await expect(page.locator(".hud span")).toHaveText("1 / …");
    await expect(page.locator(".pdf-page")).toHaveCount(0);

    await page.keyboard.press("ArrowRight");
    release();

    await expect(page.locator(".pdf-page")).toHaveCount(2);
    await expect(page.locator(".hud span")).toHaveText("2 / 2");
  });

  // The residual the test above does not reach, and the one that took CI on
  // #173's branch three times running. That test proves a turn pressed
  // before any canvas EXISTS is remembered. This one is about a turn pressed
  // while the canvases are being RE-RENDERED underneath it, at a new width -
  // which is what the side-by-side layout does on every load, because the
  // staff pane finishing its own layout resizes this one.
  //
  // For the duration of that re-render PdfViewer used to blank its
  // IntersectionObserver (suppressTracking), and an IntersectionObserver
  // only ever fires on a CHANGE: a page crossing delivered inside the
  // blanked window is not re-delivered afterwards, so the pane ended up
  // showing the new page while the indicator still read the old one, for as
  // long as the score stayed open. The re-render's own scroll restore, which
  // aimed at the last page OBSERVED rather than the one asked for, then
  // scrolled the reader back off it as well.
  //
  // Twenty pages, and the resize is issued 230ms before the key: the 200ms
  // debounce has expired and the re-render is provably still in flight when
  // the press lands, rather than that being true only on a slow runner.
  test("survives the pages being re-rendered underneath it", async ({ page }) => {
    await stubScoreApi(page, transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE }));
    await page.route("**/api/scores/1/file", (route) =>
      route.fulfill({ body: buildMultiPagePdf(20), contentType: "application/pdf" }),
    );
    await page.goto("/#/score/1");
    await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
    await expect(page.locator(".pdf-page")).toHaveCount(20, { timeout: 15_000 });
    await expect(page.locator(".hud span")).toHaveText("1 / 20");
    // let the load's own resize settle, so the one below is the only one in
    // flight and the timing under test is the timing being set up
    await page.waitForTimeout(600);

    await page.setViewportSize({ width: 900, height: 720 });
    await page.waitForTimeout(230);
    await page.keyboard.press("ArrowRight");

    await expect(page.locator(".hud span")).toHaveText("2 / 20");
  });

  // The same press, but from a reader who was PART WAY DOWN a page when it
  // landed - which the test above never is, and which is what tells a
  // restore that puts the reader back where they were from one that puts
  // them where they asked to go.
  //
  // A re-render measures where to put the reader back before it starts and
  // acts on it several hundred milliseconds later, after every canvas has
  // been redrawn. A turn pressed inside that gap moves the reader between
  // the two, so the two halves have to be talking about the same place: a
  // fraction measured down page one and then applied to page two's top puts
  // them a third of a page past where they asked to be, and the error grows
  // with how far down the previous page they had been. A whole-page turn
  // asks for the TOP of its page and has to arrive there exactly.
  test("a turn pressed mid-re-render lands on the top of the page it asked for", async ({ page }) => {
    await stubScoreApi(page, transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE }));
    await page.route("**/api/scores/1/file", (route) =>
      route.fulfill({ body: buildMultiPagePdf(20), contentType: "application/pdf" }),
    );
    await page.goto("/#/score/1");
    await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
    await expect(page.locator(".pdf-page")).toHaveCount(20, { timeout: 15_000 });
    await expect(page.locator(".hud span")).toHaveText("1 / 20");
    await page.waitForTimeout(600);

    // A third of the way down page one, the way a reader who has been
    // reading gets there. Nothing about the turn below depends on the exact
    // fraction; it only has to be a position the old restore could smear
    // onto the next page's top, which any non-zero one is.
    const start = await pdfGeometry(page);
    await page.evaluate((to) => (document.querySelector(".pages").scrollTop = to), start.pageOneTop + start.pageHeight / 3);
    await expect(page.locator(".hud span")).toHaveText("1 / 20");

    await page.setViewportSize({ width: 900, height: 720 });
    await page.waitForTimeout(230);
    await page.keyboard.press("ArrowRight");

    await pdfPagesRenderedAtSettledWidth(page);
    await expect(page.locator(".hud span")).toHaveText("2 / 20");
    const after = await pdfGeometry(page);
    expect(after.pageHeight).toBeLessThan(start.pageHeight);
    await pdfScrollSettlesAt(page, after.pageTwoTop, "the top of page two");
  });

  // The gig-mode counterpart, and the order a pedal actually produces. The
  // two tests above press a WHOLE-page turn, which asks for a page's top; a
  // gig-mode tap asks for an offset half way down one, and that is a
  // different thing for a restore to preserve. It is also the sequence
  // entering gig mode creates all by itself: widening the pane starts a
  // re-render, so the first tap of the set lands inside it.
  //
  // Preserving where the reader WAS is not enough here. That position is
  // half a page behind where the tap just asked to be, so putting them back
  // there is the press being thrown away by a second route, after the one
  // #219's first commit closed. Measured against that commit: the tap left
  // the reader at -0.022 of a page down, the same place a restore aiming at
  // the page's top leaves them.
  //
  // Twenty pages, and the resize is issued 230ms before the key, for the
  // same reason as the test above it: so the re-render is provably still in
  // flight when the press lands rather than only on a slow runner. A
  // two-page score re-renders faster than the press arrives and proves
  // nothing - checked, and it passes against the very commit it is meant to
  // fail.
  test("a half-page gig turn pressed mid-re-render is honoured, not rolled back", async ({ page }) => {
    await stubScoreApi(page, transcriptionResponse({ warnings: [], confidence: CLEAN_CONFIDENCE }));
    await page.route("**/api/scores/1/file", (route) =>
      route.fulfill({ body: buildMultiPagePdf(20), contentType: "application/pdf" }),
    );
    await page.goto("/#/score/1");
    await expect(playButton(page)).toBeEnabled({ timeout: 15_000 });
    await expect(page.locator(".pdf-page")).toHaveCount(20, { timeout: 15_000 });
    await expect(page.locator(".hud span")).toHaveText("1 / 20");
    await page.waitForTimeout(600);

    // Windowed gig mode - the path Viewer.svelte already carries a catch for
    // ("fullscreen denied or unavailable; gig mode still works windowed"),
    // and the only one a test can resize: a fullscreen window refuses
    // setViewportSize outright. Half-page turning, the pane widening and the
    // re-render this is about are all the same either way.
    await page.evaluate(() => {
      Element.prototype.requestFullscreen = () => Promise.reject(new Error("denied"));
    });
    await page.keyboard.press("f");
    await expect(page.getByRole("button", { name: "Side by side", exact: true })).toHaveCount(0);
    // Gig mode's own re-render, out of the way before the one under test -
    // and "out of the way" has to mean RESTORED, not merely re-rendered. The
    // width barrier alone returns before the restore, so gig mode's flush
    // could still be in flight here; its stamp would then land after the
    // baseline taken below and satisfy that barrier in the resize's place.
    // Traced with the restore delayed 40 frames: gig mode's flush restored at
    // 1793 and stamped 1827, the resize's flush started at 2086, and the
    // barrier returned at 2376 on gig mode's stamp - before the restore it
    // was waiting for.
    await pdfPagesRenderedAtSettledWidth(page, 15_000);
    await pdfRestoreSettlesPast(page, null, 15_000);
    const before = await pdfGeometry(page);
    // Still at the start of page one, so the offset measured at the end is
    // the tap's own doing and not somewhere the reader already was.
    expect((before.scrollTop - before.pageOneTop) / before.pageHeight).toBeLessThan(0.05);

    await page.setViewportSize({ width: 900, height: 720 });
    await page.waitForTimeout(230);
    // Captured before the press, so the barrier below cannot be satisfied by
    // a restore that had already completed.
    const restores = await pdfRenderSettleStamp(page);
    await page.keyboard.press("ArrowRight");

    await pdfPagesRenderedAtSettledWidth(page, 15_000);
    // The same exposure as the indicator test below, and the same barrier
    // (#234): the line above waits for canvas widths, which are final before
    // the re-render restores the scroll, so the fraction can otherwise be
    // read off a reader still at the pre-render offset. The read below
    // follows a re-render, so this is the barrier it needs.
    await pdfRestoreSettlesPast(page, restores, 15_000);
    const after = await pdfGeometry(page);
    expect(after.pageHeight).toBeLessThan(before.pageHeight);
    // Half a page on from where the tap was taken, give or take which
    // canvases had already been redrawn when it measured its step. Bounded
    // on BOTH sides: below 0.35 the tap was rolled back, above 0.65 it would
    // have been counted twice.
    const fraction = (after.scrollTop - after.pageOneTop) / after.pageHeight;
    expect(fraction).toBeGreaterThan(0.35);
    expect(fraction).toBeLessThan(0.65);
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
    // Same barrier as the side-by-side test above, for the same reason
    // (#168) - this test shares the hole and only wins the race more often.
    await expect(page.locator(".pdf-page")).toHaveCount(2);
    await expect(page.locator(".hud span")).toHaveText("1 / 2");
    // And one barrier that test does not need. Gig mode drops the staff
    // pane, so the PDF pane roughly doubles in width and every canvas is
    // re-rendered; both presses below have to land on the SAME rendered
    // geometry or they are two different distances (see the helper).
    await pdfPagesRenderedAtSettledWidth(page);

    // Two presses, not one - and this is the whole of issue #219.
    //
    // A gig-mode arrow key turns a HALF page: PdfViewer's `halfPage` is
    // switched on the moment gig mode is entered (that is the point of gig
    // mode - a performer reads down a page, not from page top to page top),
    // and a half turn is half the current page's rendered height plus its
    // share of the gap. Whether ONE of those moves the page INDICATOR is
    // therefore pure geometry: it moves only when half a page is taller than
    // whatever is left of the current page on screen. At the narrow
    // side-by-side render this test starts from, that is true; at the wide
    // gig render it is false, and the reader is legitimately still on the
    // bottom half of page one with the indicator correctly saying so.
    //
    // So the old single-press assertion on "2 / 2" was resting on the press
    // beating the re-render - an accident of runner speed, not a property of
    // the product. It is what failed on 3 of 36 main CI runs while every
    // pull-request run of the same trees was green. The barrier above
    // deliberately removes that accident, which makes a single press read
    // "1 / 2" every time; two presses are what actually turn the page.
    //
    // Two is not an arbitrary retry. A step is exactly half of a page plus
    // half of the gap between pages - half the page PITCH - so two of them
    // advance the pane by exactly one page whatever the pages measure, and
    // page two ends up sitting where page one was. That is what keeps this
    // an assertion about the keys reaching the PDF pane in gig mode rather
    // than about the height the pages happen to have rendered at.
    const before = await pdfGeometry(page);
    const step = (before.pageTwoTop - before.pageOneTop) / 2;

    await page.keyboard.press("ArrowRight");
    // The key reached the PDF pane, and moved it by exactly a half turn.
    await pdfScrollSettlesAt(page, before.scrollTop + step, "half a page on");
    // Deliberately still page one: half a page in, with half of it left.
    // This one line does depend on the geometry it is asserting about - half
    // a page leaves page two under PdfViewer's 0.4 intersection threshold at
    // this viewport, so a change to that threshold, to the 18px inter-page
    // gap, or to the suite's viewport reds it for a reason that has nothing
    // to do with the keys. The scroll assertions either side of it do not.
    await expect(page.locator(".hud span")).toHaveText("1 / 2");

    await page.keyboard.press("ArrowRight");
    await pdfScrollSettlesAt(page, before.scrollTop + 2 * step, "a whole page on");
    await expect(page.locator(".hud span")).toHaveText("2 / 2");
  });

  // The product half of #219, and the one that matters at the stand. The
  // test above waits for gig mode's re-render; a performer does not, and a
  // pedal sends nothing but arrow keys with no mouse to recover with.
  //
  // A half-page turn leaves the reader mid-page by design. PdfViewer's
  // re-render then had to put them back, and it aimed at the top of the page
  // they were on - which threw the half turn away entirely. Entering gig
  // mode is exactly when the two collide: it widens the pane, so a re-render
  // is already queued when the first tap arrives.
  //
  // Not raced for here. The resize is issued while the reader is provably
  // half a page down and the assertion is on where the re-render leaves
  // them, so this fails on every run against the old restore rather than on
  // a slow one - the race only decided how often a real reader met it.
  test("a half-page turn survives the pane being re-rendered at a new width", async ({ page }) => {
    // Windowed gig mode - the path Viewer.svelte already carries a catch for
    // ("fullscreen denied or unavailable; gig mode still works windowed"),
    // and the only one a test can resize: a fullscreen window refuses
    // setViewportSize outright. Half-page turning, the pane widening and the
    // re-render this is about are all the same either way; only the window
    // chrome differs.
    await page.evaluate(() => {
      Element.prototype.requestFullscreen = () => Promise.reject(new Error("denied"));
    });
    await page.keyboard.press("f");
    await expect(page.getByRole("button", { name: "Side by side", exact: true })).toHaveCount(0);
    await expect(page.locator(".pdf-page")).toHaveCount(2);
    // Gig mode's own re-render out of the way before the one under test, and
    // "out of the way" has to mean RESTORED, not merely re-rendered. The
    // width barrier alone returns before the restore, so gig mode's flush can
    // still be in flight here - and its stamp would then land after the
    // baseline taken below and satisfy that barrier in the resize's place.
    // Traced with the restore delayed 40 frames: gig mode's flush restored at
    // 1793 and stamped 1827, the resize's flush started at 2086, and the
    // barrier returned at 2376 on gig mode's stamp - 112px from where the
    // resize's own restore would have left the reader.
    await pdfPagesRenderedAtSettledWidth(page);
    await pdfRestoreSettlesPast(page, null);

    const before = await pdfGeometry(page);
    const step = (before.pageTwoTop - before.pageOneTop) / 2;
    await page.keyboard.press("ArrowRight");
    await pdfScrollSettlesAt(page, before.scrollTop + step, "half a page on");
    // How far down page one the reader now is - the thing that has to
    // survive the re-render, expressed as a fraction because the pixel
    // count will not: the pages are about to change height.
    const mid = await pdfGeometry(page);
    const fraction = (mid.scrollTop - mid.pageOneTop) / mid.pageHeight;
    expect(fraction).toBeGreaterThan(0.3);

    // Narrower pane -> shorter pages -> a re-render, the same one entering
    // gig mode causes, with the reader already mid-page.
    const restores = await pdfRenderSettleStamp(page);
    await page.setViewportSize({ width: 900, height: 720 });
    await pdfPagesRenderedAtSettledWidth(page);
    // Same early-read exposure as the two tests above - canvas widths are
    // final before the loop restores the scroll - and it was wrong to leave
    // this one out (#234). The barrier does not weaken what follows: it waits
    // for the viewer to say it HAS restored, and the assertions below are
    // about WHERE it restored to. The old restore, which aimed at the page's
    // top, stamps exactly the same and is caught by the same three lines.
    await pdfRestoreSettlesPast(page, restores);
    const after = await pdfGeometry(page);
    expect(after.pageHeight).toBeLessThan(before.pageHeight);

    const want = after.pageOneTop + fraction * after.pageHeight;
    expect(Math.abs(after.scrollTop - want)).toBeLessThanOrEqual(2);
    // Stated the blunt way too: not scrolled back to the top of page one,
    // which is where the old restore put it on every run.
    expect(after.scrollTop).toBeGreaterThan(after.pageOneTop + 1);
  });

  // Issue #229, and the half of a page turn the test above does not look at:
  // it proves where the reader ENDS UP, and says nothing about what the
  // indicator claims about that place. The two came apart here.
  //
  // A tap taken before gig mode's own re-render is measured against the
  // narrow pre-render geometry, where half a page is short enough to bring
  // page two past the 0.4 intersection threshold - so the observer sets the
  // indicator to "2 / 2", correctly, for the pane as it stands. The
  // re-render then blanks the observer (suppressTracking) and doubles every
  // page's height underneath it: the reader is left half way down page ONE
  // with page two barely on screen. That change was delivered as a crossing
  // while the observer was blanked and dropped, and an IntersectionObserver
  // only ever fires on a change, so nothing re-delivered it. Measured on
  // main before this fix, 3 of 3: the pane at scrollTop 547 - 47% down page
  // one, page one at ratio 0.52 against page two's 0.11 - and the HUD
  // reading "2 / 2" for as long as the score stayed open.
  //
  // Not a race, despite pressing without a barrier. The assertion is that
  // the indicator matches the pane's OWN geometry, which is true of a
  // correct viewer whichever side of the re-render the press lands on - all
  // three orderings (before the 200ms debounce fires, inside the re-render,
  // after it) leave the reader half a page down page one. Winning the race
  // is what makes this test reach the dropped crossing; losing it can only
  // make it prove less, never fail.
  test("the page indicator matches the pane after a turn taken inside gig mode's re-render", async ({ page }) => {
    // Windowed gig mode, for the reason the test above states.
    await page.evaluate(() => {
      Element.prototype.requestFullscreen = () => Promise.reject(new Error("denied"));
    });
    await expect(page.locator(".pdf-page")).toHaveCount(2);
    await pdfPagesRenderedAtSettledWidth(page);

    await page.keyboard.press("f");
    await expect(page.getByRole("button", { name: "Side by side", exact: true })).toHaveCount(0);
    // Deliberately no barrier before the press: this is the tap a performer
    // actually makes, straight into the pane that is still about to
    // re-render. The count is only read, not waited on.
    const restores = await pdfRenderSettleStamp(page);
    await page.keyboard.press("ArrowRight");

    await pdfPagesRenderedAtSettledWidth(page);
    // The turn's own barrier, and the only one here that is about where the
    // re-render put the reader rather than about the canvases (#234). The
    // indicator poll below happens to screen the same early read at this
    // threshold and this geometry; see pdfRestoreSettlesPast for the
    // measurement, and for why that is an accident worth not depending on.
    await pdfRestoreSettlesPast(page, restores);
    await pdfIndicatorAgrees(page, 2);

    // And the geometry that makes the line above worth asserting: the reader
    // really is mid-page-one, not parked somewhere the two would agree
    // trivially. Half a page on, whichever geometry the step was measured
    // against, with page two left well under the 0.4 threshold.
    //
    // The band stays 0.35 to 0.65. Measured at rest, 60 runs across three CPU
    // throttling rates, the fraction takes exactly two values - 0.4755 and
    // 0.4864, the two orderings of the race this test presses into - and the
    // nearer edge is 0.125 away. Widening it would only hide the next early
    // read; the band was never the loose part.
    const at = await pdfGeometry(page);
    const fraction = (at.scrollTop - at.pageOneTop) / at.pageHeight;
    expect(fraction).toBeGreaterThan(0.35);
    expect(fraction).toBeLessThan(0.65);
    const state = await pdfIndicatorState(page);
    expect(state.shown).toBe(1);
    await expect(page.locator(".hud span")).toHaveText("1 / 2");
  });

  // The same defect reached without pressing anything into a live re-render,
  // so it fails on every run rather than on a run that wins a race: the turn
  // is fully settled and correct BEFORE the pane changes shape, and it is
  // the resize alone that moves which page is on screen.
  //
  // A window resized mid-set is the ordinary way this happens off the stand.
  test("a resize that changes which page is on screen moves the indicator with it", async ({ page }) => {
    await page.evaluate(() => {
      Element.prototype.requestFullscreen = () => Promise.reject(new Error("denied"));
    });
    await page.keyboard.press("f");
    await expect(page.getByRole("button", { name: "Side by side", exact: true })).toHaveCount(0);
    await expect(page.locator(".pdf-page")).toHaveCount(2);
    await pdfPagesRenderedAtSettledWidth(page);

    // A narrow pane renders short pages, and half of a short page is enough
    // to bring page two past the threshold - which is what gives the resize
    // below something to change its mind about.
    await page.setViewportSize({ width: 700, height: 720 });
    await pdfPagesRenderedAtSettledWidth(page);
    const before = await pdfGeometry(page);
    await page.keyboard.press("ArrowRight");
    await pdfScrollSettlesAt(page, before.scrollTop + (before.pageTwoTop - before.pageOneTop) / 2, "half a page on");
    // Right, and asserted as such: page two IS the page most of the pane is
    // showing here. Without this the test could not tell the indicator being
    // re-derived from it never having moved at all.
    await pdfIndicatorAgrees(page, 2);
    await expect(page.locator(".hud span")).toHaveText("2 / 2");

    // Widen: the pages grow back, the reader keeps their place half way down
    // page one (#224), and page two drops off the bottom of the pane.
    await page.setViewportSize({ width: 1280, height: 720 });
    await pdfPagesRenderedAtSettledWidth(page);
    await pdfIndicatorAgrees(page, 2);
    const after = await pdfGeometry(page);
    expect(after.pageHeight).toBeGreaterThan(before.pageHeight);
    const state = await pdfIndicatorState(page);
    expect(state.shown).toBe(1);
    await expect(page.locator(".hud span")).toHaveText("1 / 2");
  });

  test("from the staff layout, gig mode keeps the staff pane and Space still plays/pauses it", async ({ page }) => {
    await page.getByRole("button", { name: "Staff", exact: true }).click();
    // Clicking "Staff" leaves it focused (an ordinary browser behaviour),
    // and Space on a focused button now activates the button - see "the
    // focus guard" describe block's own F3 test - so this has to move
    // focus off it before Space can reach the transport at all.
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press("f");
    await expect(page.getByRole("button", { name: "Staff", exact: true })).toHaveCount(0);
    await page.keyboard.press(" ");
    await expect(page.locator("button.primary")).toHaveText(/Pause/);
    await page.keyboard.press(" "); // left as found
  });
});
