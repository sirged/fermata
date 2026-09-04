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
async function pdfPagesRenderedAtSettledWidth(page) {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const scroller = document.querySelector(".pages");
        const pages = [...document.querySelectorAll(".pdf-page")];
        if (!scroller || !pages.length) return "no pages yet";
        const want = Math.min(scroller.clientWidth - 32, 1100);
        const widths = [...new Set(pages.map((p) => Math.round(p.getBoundingClientRect().width)))];
        return widths.length === 1 && widths[0] === want ? "settled" : `rendered ${widths} of ${want}`;
      }),
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
    await pdfPagesRenderedAtSettledWidth(page);

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
    await page.setViewportSize({ width: 900, height: 720 });
    await pdfPagesRenderedAtSettledWidth(page);
    const after = await pdfGeometry(page);
    expect(after.pageHeight).toBeLessThan(before.pageHeight);

    const want = after.pageOneTop + fraction * after.pageHeight;
    expect(Math.abs(after.scrollTop - want)).toBeLessThanOrEqual(2);
    // Stated the blunt way too: not scrolled back to the top of page one,
    // which is where the old restore put it on every run.
    expect(after.scrollTop).toBeGreaterThan(after.pageOneTop + 1);
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
