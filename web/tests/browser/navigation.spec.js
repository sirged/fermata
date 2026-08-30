// Playback follows the D.C., D.S., To Coda and Fine the transcription carries
// (issue #151).
//
// WHAT IS ASSERTED, AND WHY IT IS THE HONEST THING TO ASSERT. The bar order
// read here is `data-playback-bars`, which score-render.js publishes from
// api.tickCache.masterBars - MidiFileGenerator's own tick lookup, built by the
// same generate() pass that produces the midi the synthesiser plays. There is
// no second mechanism: the notes that sound are the events of that midi, and
// the lookup is that midi's own account of which bar each stretch of it came
// from. It is also the timeline the practice layer already runs on - the
// metronome's bar counting, the cursor's stepping and the loop range all read
// the same lookup - so an assertion on it is an assertion about the piece the
// whole viewer is actually playing, not about a value computed beside it.
//
// That is not taken on trust alone. The last test in this file presses Play
// and watches `data-playing-bar`, which is published from the player's own
// position reports while audio runs, cross the jump live: the bar sounding
// after bar 4 is bar 1. If the injected directions were somehow not in the
// generated midi, that test would sit on bar 5 until it timed out.
//
// MUTATION RECORD (issue #151, and the reason these numbers are written out
// literally rather than derived from the fixture). Each was applied to
// score-render.js, rebuilt, run, and reverted:
//
//   - deleting the applyLoadedNavigation() call from the scoreLoaded handler
//     put the navigation fixture back to the straight `1 2 3 4 5 6 7 8` and
//     reddened ALL 16 - including the live one, which read `1 2 3 4 5 6 7` off
//     the audio timeline. The four drawn-text tests catch it only because each
//     also names the direction that was added: a count of drawn labels alone
//     cannot tell "cleared the right beat" from "added nothing to clear".
//   - mapping "al Coda" to the plain Direction.JumpDalSegno reddened 8, and
//     gave `1 2 3 4 1 2 3 4 5 6 7 8`. That is the plausible wrong answer, not
//     an arbitrary one: it is exactly the order this issue measured from
//     hoisting the `<sound>` elements to measure level and letting alphaTab's
//     own importer read them, so these tests are pinned against the losing
//     route as well as against no route at all.
//   - replacing jumpDirectionFor's ORDERING checks with the "is there one
//     anywhere" test they replaced reddened 2: the late-segno test by order
//     (`1 2 5 6`), and the backwards-coda test by TIMEOUT - data-score-render-ok
//     never appears, because the midi generator never returns. That is the
//     failure it is built to catch and the reason it carries its own 45s
//     ceiling; the passing path takes under half a second.
//   - dropping the `codaRouteIsSafe` term from the al-Coda flavour reddened 1,
//     also by timeout: the mixed-convention treatment, where a measure-level
//     To Coda the renderer imports unguarded meets a backwards coda. Its
//     CONTROL stayed green, which is the half that matters - the guard has to
//     disarm the wedge without declining the same document's playable form.
//   - making clearLateBeatText a no-op reddened all 4 drawn-text tests;
//     widening it back to every beat of two bars reddened 1, the annotation
//     test, at one drawn "Fine" where two belong; and narrowing it back to a
//     bar's FIRST beat only reddened the two mid-measure tests, at two drawn
//     "Fine"s where one belongs, with the annotation test still green. Those
//     last two bracket the rule from both sides.
import { expect, test } from "@playwright/test";
import {
  stubNavigationAnnotationScore,
  stubNavigationBackwardsCodaScore,
  stubNavigationContainerScore,
  stubNavigationLateSegnoScore,
  stubNavigationMidbarMarkScore,
  stubNavigationMixedBackwardScore,
  stubNavigationMixedForwardScore,
  stubNavigationRepeatScore,
  stubNavigationTwoVoiceMarkScore,
  stubNavigationScore,
  stubNavigationTimewiseScore,
  stubNavigationUnresolvedScore,
  stubNavigationUnstrungTabScore,
  stubNavigationTabOnlyInvalidStringScore,
} from "./fixtures/navigation-score.js";
import { tabWithheldMessage, UNRENDERABLE_MESSAGE } from "../../src/lib/score-render.js";

const host = (page) => page.locator(".at-host");
const playButton = (page) => page.locator(".player button.primary");

// The renderer publishes this only once a render has finished, so waiting for
// it to be present at all is waiting for a real render rather than for a
// navigation - the same reason the other suites here wait on the play button
// being enabled rather than on page.goto() resolving.
async function playbackBars(page) {
  return await host(page).getAttribute("data-playback-bars");
}

async function openScore(page, id) {
  await page.goto(`/#/score/${id}`);
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true", { timeout: 30_000 });
}

test.describe("navigation marks reach playback", () => {
  test("the committed navigation transcription plays the form it carries", async ({ page }) => {
    await stubNavigationScore(page);
    await openScore(page, 11);
    // segno@1, To Coda@2, D.S. al Coda@4, coda@6, Fine@7, D.C. al Fine@8:
    // 1-4, back to the segno, out at the To Coda on the pass that is looking
    // for one, through the coda to bar 8, then the D.C. back to the top and
    // on to the Fine at bar 7.
    await expect
      .poll(() => playbackBars(page), { timeout: 30_000 })
      .toBe("1 2 3 4 1 2 6 7 8 1 2 3 4 5 6 7");
  });

  test("the directions injected are the ones the document names", async ({ page }) => {
    await stubNavigationScore(page);
    await openScore(page, 11);
    // The two signs are already on the model - alphaTab's importer builds
    // TargetSegno/TargetCoda from <direction-type><segno/>/<coda/> - so they
    // are not listed here; what this file adds is the Fine (which nothing
    // else produces) and the three jumps, each in the compound reading its
    // own <words> spell out.
    await expect(host(page)).toHaveAttribute(
      "data-score-jumps",
      "7:TargetFine 2:JumpDaCoda 4:JumpDalSegnoAlCoda 8:JumpDaCapoAlFine",
    );
    await expect(host(page)).toHaveAttribute("data-score-jumps-skipped", "0");
  });

  test("a D.S. composes with the repeat structure it jumps across", async ({ page }) => {
    await stubNavigationRepeatScore(page);
    await openScore(page, 12);
    // Bars 1-3 repeat, To Coda closes bar 4, D.S. al Coda closes bar 6, the
    // coda opens bar 7. The repeat is taken on the first pass and NOT on the
    // pass the D.S. starts: alphaTab's MidiPlaybackController clears its
    // repeat stack when it takes a jump, which is also the usual performance
    // convention (repeats are not taken on the D.S. unless the page says so).
    // Written out literally rather than derived, so a change to that
    // behaviour shows up here as a failure to read rather than as two
    // formulas agreeing with each other.
    await expect
      .poll(() => playbackBars(page), { timeout: 30_000 })
      .toBe("1 2 3 1 2 3 4 5 6 1 2 3 4 7 8");
  });

  test("a jump naming a target the score does not draw is not injected", async ({ page }) => {
    const consoleErrors = [];
    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m.text());
    });
    page.on("pageerror", (e) => consoleErrors.push(String(e)));
    await stubNavigationUnresolvedScore(page);
    await openScore(page, 13);
    // Straight through, once. Bar 3's "D.S. al Coda" carries no <sound> at all
    // (nothing to act on); bar 5's "D.C. al Fine" carries a live dacapo on a
    // score with no Fine, and is declined rather than degraded to a plain D.C.
    // that would play the whole piece twice.
    await expect.poll(() => playbackBars(page), { timeout: 30_000 }).toBe("1 2 3 4 5 6");
    await expect(host(page)).toHaveAttribute("data-score-jumps", "");
    await expect(host(page)).toHaveAttribute("data-score-jumps-skipped", "1");
    expect(consoleErrors).toEqual([]);
  });

  test("an instruction the renderer now draws itself is not also printed a bar late", async ({ page }) => {
    await stubNavigationRepeatScore(page);
    await openScore(page, 12);
    // alphaTab attaches a <direction>'s <words> to the next beat it creates,
    // and Rule 16 writes an instruction after its measure's notes - so the
    // words used to land a bar downstream, which was the only trace of a jump
    // the player had. Now that the direction itself is on the right bar, the
    // renderer draws the instruction where it belongs and the stray copy is
    // cleared. Measured before that was written: each of these appeared twice.
    const drawn = await page.evaluate(() =>
      [...document.querySelectorAll(".at-host svg text")].map((t) => t.textContent),
    );
    expect(drawn.filter((t) => t === "To Coda")).toHaveLength(1);
    expect(drawn.filter((t) => t === "D.S. al Coda")).toHaveLength(1);
    // One of each is also what a score with NO direction injected draws - the
    // uncleared echoes, and no glyphs - so the count alone cannot tell
    // "cleared the right beat" from "did nothing at all". Naming the
    // directions that were added closes that.
    await expect(host(page)).toHaveAttribute("data-score-jumps", "4:JumpDaCoda 6:JumpDalSegnoAlCoda");
  });

  // The hang. Bounded deliberately: with the ordering check removed,
  // MidiFileGenerator.generate() never returns, and it runs on the main
  // thread - so the page stops answering, every locator poll below stalls,
  // and without a ceiling this test would hold a CI runner for the whole
  // 180s file timeout (or until the tab died of heap exhaustion) instead of
  // failing. 45s is far more than the ~1s the passing path takes and far less
  // than that.
  test("a coda before the To Coda that names it is declined, not hung on", async ({ page }) => {
    test.setTimeout(45_000);
    await stubNavigationBackwardsCodaScore(page);
    await page.goto("/#/score/14");
    // A render finishing at all is the assertion: the player cannot become
    // ready while the midi generator is still looping.
    await expect(host(page)).toHaveAttribute("data-score-render-ok", "true", { timeout: 20_000 });
    await expect(playButton(page)).toBeEnabled({ timeout: 20_000 });
    // Straight through, and the To Coda counted as declined. The D.S. al Coda
    // is still injected - its own target is behind it, where the renderer will
    // look - but with no coda jump to take, it never re-arms anything.
    await expect.poll(() => playbackBars(page), { timeout: 20_000 }).toBe("1 2 3 4 5 6 1 2 3 4 5 6");
    await expect(host(page)).toHaveAttribute("data-score-jumps-skipped", "1");
  });

  test("a segno after the D.S. that names it is declined, not jumped forward to", async ({ page }) => {
    await stubNavigationLateSegnoScore(page);
    await openScore(page, 15);
    // alphaTab's own target search falls forward when it finds nothing behind,
    // so injecting this would jump the wrong way and play `1 2 5 6` - four
    // bars of a six-bar score, with nothing saying two went missing.
    await expect.poll(() => playbackBars(page), { timeout: 30_000 }).toBe("1 2 3 4 5 6");
    await expect(host(page)).toHaveAttribute("data-score-jumps", "");
    await expect(host(page)).toHaveAttribute("data-score-jumps-skipped", "1");
  });

  // The control half of the pair below. Both documents mix the two <sound>
  // conventions - a nested D.S. al Coda this layer reads, and a MEASURE-LEVEL
  // To Coda only alphaTab's own importer reads, which it leaves unguarded.
  // With the coda after the To Coda that is an ordinary playable form, and
  // declining the D.S. here would be a regression rather than a fix.
  test("a measure-level To Coda with its coda ahead of it still plays the form", async ({ page }) => {
    await stubNavigationMixedForwardScore(page);
    await openScore(page, 19);
    await expect.poll(() => playbackBars(page), { timeout: 30_000 }).toBe("1 2 3 4 5 6 1 2 3 4 7 8");
    await expect(host(page)).toHaveAttribute("data-score-jumps", "6:JumpDalSegnoAlCoda");
  });

  // The residual wedge, and bounded for the same reason as the backwards-coda
  // test above: with the al-Coda flavour armed, the unguarded measure-level To
  // Coda jumps backwards, resets the state machine, re-arms the D.S., and
  // MidiFileGenerator never returns - measured at 89.9s of pegged main thread.
  test("an unguarded measure-level To Coda with a backwards coda disarms the al-Coda jump", async ({ page }) => {
    test.setTimeout(45_000);
    await stubNavigationMixedBackwardScore(page);
    await page.goto("/#/score/20");
    await expect(host(page)).toHaveAttribute("data-score-render-ok", "true", { timeout: 20_000 });
    await expect(playButton(page)).toBeEnabled({ timeout: 20_000 });
    // The D.S. is declined outright rather than downgraded: with no jump that
    // enters the coda-seeking state, the unguarded To Coda can never fire, and
    // the score plays straight through. This layer cannot guard a direction it
    // did not add, so it removes the only thing that can arm it.
    await expect.poll(() => playbackBars(page), { timeout: 20_000 }).toBe("1 2 3 4 5 6 7 8");
    await expect(host(page)).toHaveAttribute("data-score-jumps", "");
    await expect(host(page)).toHaveAttribute("data-score-jumps-skipped", "1");
  });

  test("an instruction written part-way through its bar is drawn once", async ({ page }) => {
    await stubNavigationMidbarMarkScore(page);
    await openScore(page, 21);
    // The echo lands on an INTERIOR beat of the mark's own bar, because the
    // importer hands its one-slot beat text to whatever beat it creates next -
    // and after a mid-measure direction that is the third note, not the next
    // bar's first. A clearing pass that only ever looks at a bar's first beat
    // leaves it, and "Fine" is drawn twice.
    const drawn = await page.evaluate(() =>
      [...document.querySelectorAll(".at-host svg text")].map((t) => t.textContent),
    );
    expect(drawn.filter((t) => t === "Fine")).toHaveLength(1);
    await expect(host(page)).toHaveAttribute("data-score-jumps", "2:TargetFine");
  });

  test("an instruction written before a backup is drawn once", async ({ page }) => {
    await stubNavigationTwoVoiceMarkScore(page);
    await openScore(page, 22);
    // Same consequence, different route: the next beat the importer creates
    // after this direction is the first beat of the bar's SECOND VOICE. The
    // echo is not "somewhere in the next bar" - it is wherever the importer's
    // own walk happens to be, which is why the slot is counted off the
    // document rather than assumed.
    const drawn = await page.evaluate(() =>
      [...document.querySelectorAll(".at-host svg text")].map((t) => t.textContent),
    );
    expect(drawn.filter((t) => t === "Fine")).toHaveLength(1);
    await expect(host(page)).toHaveAttribute("data-score-jumps", "2:TargetFine");
  });

  test("a words-only annotation sharing a mark's text survives the echo being cleared", async ({ page }) => {
    await stubNavigationAnnotationScore(page);
    await openScore(page, 16);
    // Two "Fine"s belong on this page: the one the renderer now draws for the
    // mark at the end of bar 2, and the separate words-only annotation printed
    // part-way through bar 3. The third - the mark's own echo, on bar 3's
    // first beat - is the duplicate, and is the only one that goes. Clearing
    // any wider takes the annotation with it, and nothing else on the page
    // records that it was ever there.
    const drawn = await page.evaluate(() =>
      [...document.querySelectorAll(".at-host svg text")].map((t) => t.textContent),
    );
    expect(drawn.filter((t) => t === "Fine")).toHaveLength(2);
    // A count of two is also what a score with NO direction injected draws -
    // the uncleared echo plus the annotation - so the count alone cannot tell
    // "cleared the right one" from "did nothing at all". Naming the direction
    // that was added closes that: two "Fine"s and a Fine on bar 2 can only be
    // the glyph and the annotation.
    await expect(host(page)).toHaveAttribute("data-score-jumps", "2:TargetFine");
  });

  test("a compressed .mxl container's jumps are read, not silently skipped", async ({ page }) => {
    await stubNavigationContainerScore(page);
    await openScore(page, 17);
    // The same document as the plain-XML fixture, inside a real ZIP with the
    // manifest that names it. `.mxl` is a file type the library accepts and a
    // person can upload; before the container was opened this played its D.S.
    // as though the score carried none, indistinguishably from one that does.
    await expect
      .poll(() => playbackBars(page), { timeout: 30_000 })
      .toBe("1 2 3 4 1 2 6 7 8 1 2 3 4 5 6 7");
    // And nothing claims the document was unreadable.
    expect(await host(page).getAttribute("data-score-jumps-unread")).toBeNull();
  });

  test("a document whose marks were not read says so rather than looking like one with none", async ({ page }) => {
    await stubNavigationTimewiseScore(page);
    await openScore(page, 18);
    // A score-timewise document: the renderer imports it, this layer refuses to
    // index it (a measure's position there is not a master bar index), and it
    // carries a live D.S. that therefore does not play. An empty
    // data-score-jumps alone would be indistinguishable from a score that
    // prints no jumps at all, which is the failure this attribute exists to
    // prevent - the same distinction that made a compressed container's marks
    // vanish without trace.
    await expect(host(page)).toHaveAttribute("data-score-jumps-unread", "not-musicxml");
    await expect(host(page)).toHaveAttribute("data-score-jumps", "");
    await expect.poll(() => playbackBars(page), { timeout: 30_000 }).toBe("1 2 3 4");
  });

  test("stepping the cursor by bar follows the played order across a jump", async ({ page }) => {
    await stubNavigationScore(page);
    await openScore(page, 11);
    await expect.poll(() => playbackBars(page), { timeout: 30_000 }).toBe("1 2 3 4 1 2 6 7 8 1 2 3 4 5 6 7");

    // Deliberately no click on the score first: alphaTab's own beat selection
    // moves the playhead on a plain mouse click, so clicking to "focus" the
    // host would start this walk from wherever the pointer happened to land
    // (measured: the centre of the page is bar 4, and the walk from there
    // read 4 -> 1 -> 2 -> 6, correct but not the run this test describes).
    // The key handler is on the window; nothing needs focusing.
    //
    // data-cursor-bar is the NOTATED index (0-based) of the bar the cursor is
    // in; data-cursor-tick is its PLAYBACK tick. The pair is what makes this
    // test say something: after four bar-steps the cursor is back on bar 1 by
    // name and much further along in time, which is exactly what "the second
    // pass of bar 1" means and is unreachable if stepping walked the notated
    // bar list. This is the property #142 pinned for repeats, on a jump.
    const bars = [];
    const ticks = [];
    for (let i = 0; i < 5; i++) {
      bars.push(await host(page).getAttribute("data-cursor-bar"));
      ticks.push(Number(await host(page).getAttribute("data-cursor-tick")));
      if (i < 4) await page.keyboard.press("ArrowDown");
    }
    expect(bars).toEqual(["0", "1", "2", "3", "0"]);
    // Strictly increasing, including over the step that lands back on bar 1 -
    // the cursor never goes backwards in time because the music does not.
    for (let i = 1; i < ticks.length; i++) {
      expect(ticks[i]).toBeGreaterThan(ticks[i - 1]);
    }
  });

  test("the player actually sounds the jump, not just the schedule", async ({ page }) => {
    // Every distinct value data-playing-bar takes, recorded in the page by a
    // MutationObserver rather than sampled by polling from the test. A bar of
    // this fixture lasts two seconds at the renderer's default tempo, which a
    // poll whose interval escalates into whole seconds can step straight over
    // - and a missed sample would read as the jump not happening. The
    // observer cannot miss one: it is called for the attribute write itself.
    await page.addInitScript(() => {
      window.__playingBars = [];
      new MutationObserver((records) => {
        for (const r of records) {
          const value = r.target.getAttribute("data-playing-bar");
          if (value != null && value !== window.__playingBars[window.__playingBars.length - 1]) {
            window.__playingBars.push(value);
          }
        }
        // `document`, not document.documentElement: an init script runs
        // before the page's own scripts, when the root element may not exist
        // yet - observing it then throws and takes the rest of this script
        // with it, leaving an array that stays empty forever and a test that
        // fails for the wrong reason (measured).
      }).observe(document, {
        subtree: true,
        attributes: true,
        attributeFilter: ["data-playing-bar"],
      });
    });
    await stubNavigationScore(page);
    await openScore(page, 11);
    await playButton(page).click();
    // Seven bars of real audio: 1-4, back to the segno, and out at the To
    // Coda into the coda bar. data-playing-bar is published from the player's
    // own position reports, so none of this can be satisfied by a lookup that
    // was built correctly and then never played.
    await expect
      .poll(async () => (await page.evaluate(() => window.__playingBars)).slice(0, 7).join(" "), {
        timeout: 90_000,
      })
      .toBe("1 2 3 4 1 2 6");
  });
});

// ---------------------------------------------------------------------------
// Issue #165: a TAB-staff note with no <string> must not crash the renderer
// ---------------------------------------------------------------------------

/** Every console message Playwright reports as an error, plus any uncaught
 * page exception - the check "the committed navigation transcription plays
 * the form it carries" above never made, which is exactly how issue #165's
 * crash went unnoticed: alphaTab catches TabBarRenderer's paint exceptions
 * internally and logs them rather than throwing, so a page that draws almost
 * nothing can still set data-score-render-ok, enable the play button and
 * pass every assertion that does not itself read the console. */
function watchForErrors(page) {
  const consoleErrors = [];
  const pageErrors = [];
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });
  page.on("pageerror", (e) => pageErrors.push(String(e)));
  return { consoleErrors, pageErrors };
}

async function drawnGlyphCount(page) {
  return await page.evaluate(() => document.querySelectorAll(".at-host svg text").length);
}

/** `data-score-profiles`, split on its comma delimiter (publish() in
 * score-render.js writes `scoreProfiles.join(",")`) - [] once a score has
 * loaded and found nothing drawable, since "".split(",") is [""] not []. */
async function dataScoreProfiles(page) {
  const raw = await host(page).getAttribute("data-score-profiles");
  return raw ? raw.split(",") : [];
}

/** `data-score-tab-withheld` - the count disqualifyUnstrungTabStaves()
 * disqualified, as a string, or null when the attribute is absent (nothing
 * withheld - see its own delete call in score-render.js's scoreLoaded
 * handler). */
async function dataScoreTabWithheld(page) {
  return await host(page).getAttribute("data-score-tab-withheld");
}

/** The empty-state notice's text, or null when that element is not in the
 * DOM at all (a score that DOES have something drawable never renders
 * `.notice`). */
async function noticeText(page) {
  const notice = page.locator(".notice");
  return (await notice.count()) ? await notice.textContent() : null;
}

test.describe("a TAB staff without a fretted position does not crash the renderer", () => {
  test("the real navigation.pdf transcription renders clean, with tab glyphs drawn", async ({
    page,
  }) => {
    // The ground truth for issue #165's own "Verifying" checklist: every
    // TAB-staff note in the committed transcription carries <string> and
    // <fret> (see server/tests/test_engraved_fixtures.py's and
    // test_tabextract.py's Rule 9 sweeps for the structural proof), so this
    // is the render-side half - the score page actually draws it, with
    // nothing logged to the console.
    const errors = watchForErrors(page);
    await stubNavigationScore(page);
    await openScore(page, 11);
    await page.waitForTimeout(1000);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.pageErrors).toEqual([]);
    // Measured on the unfixed page (reading navigation.musicxml, the
    // engraving source, straight into alphaTab): 12 glyphs - almost nothing
    // of the fixture actually drew. The real transcription draws its full
    // eight bars of tab digits and navigation text; > 40 is comfortably past
    // "a handful of leftover notation glyphs" and nowhere near a tight pin
    // on the exact digit count. (A healthy page also draws 0 SVG <path>
    // elements on this fixture - alphaTab has nothing here it renders as
    // one - so glyph COUNT is what discriminates a broken render, not glyph
    // type; see fixtures/navigation-score.js's header comment.)
    expect(await drawnGlyphCount(page)).toBeGreaterThan(40);
    // Nothing was withheld: every note in the real transcription is fretted.
    expect(await dataScoreTabWithheld(page)).toBeNull();
  });

  test("a staff whose tab notes are not all fretted degrades instead of crashing", async ({
    page,
  }) => {
    // Issue #165's actual reproduction: navigation.pdf's own ENGRAVING
    // SOURCE (two staves, notation over tab, the tab staff's notes carrying
    // no <string>/<fret> because MuseScore frets them itself while
    // engraving) fed to the real renderer as if it were a score - the shape
    // a directly uploaded MusicXML/.mxl file can legally have even though
    // Fermata's own transcriptions never produce it. Before
    // disqualifyUnstrungTabStaves existed, this threw straight out of
    // TabBarRenderer.paintStaffLines (caught and logged by alphaTab, not
    // re-thrown - see watchForErrors above), drawing 12 glyphs total.
    const errors = watchForErrors(page);
    await stubNavigationUnstrungTabScore(page);
    await openScore(page, 23);
    await page.waitForTimeout(1000);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.pageErrors).toEqual([]);
    // The notation staff (the same 32 notes, standard clef) still draws in
    // full - only the tab staff's rendering is turned off - so this is well
    // past the 12-glyph broken count without pinning an exact number.
    expect(await drawnGlyphCount(page)).toBeGreaterThan(40);
    // The strong assertion, not just "still draws something": the tab
    // profile was actually dropped from what this score offers, while the
    // notation half - a real staff, unaffected by the other one's defect -
    // stayed offered. Asserting only the glyph count above could pass for
    // the wrong reason (e.g. a bug that left "tab" offered but drew nothing
    // under it); this reads the renderer's own account of what it decided.
    const profiles = await dataScoreProfiles(page);
    expect(profiles).not.toContain("tab");
    expect(profiles).toContain("score");
    // Disclosed, not silently dropped (issue #165 review): one staff was
    // withheld, and a person with devtools open (or a future test) can see
    // it and why - see disqualifyUnstrungTabStaves's own dataset write.
    expect(await dataScoreTabWithheld(page)).toBe("1");
    // This score still has a notation staff to fall back to, so it is not
    // the "nothing left to draw" case - no notice element at all.
    expect(await noticeText(page)).toBeNull();
  });

  test("a TAB-only score with one out-of-range string degrades and discloses why, not the generic empty-score notice", async ({
    page,
  }) => {
    // The adversarial-review shape: isStringed() alone (string >= 0) is NOT
    // the crash condition - alphaTab maps a <string> element's value S to
    // note.string = tuning.length - S + 1 with no range check, so
    // <string>7</string> on a 6-line staff round-trips to note.string = 0,
    // which still satisfies isStringed and still runs
    // collectSpaces's `spaces[tuning.length - note.string]` off the end of
    // its own array. Guarding on isStringed instead of the 1..tuning.length
    // range reproduces the exact original crash here - see the mutation
    // record for this file.
    //
    // This is also the ONE staff the score has - no notation staff to fall
    // back to - so withholding it empties supportedProfiles() entirely, and
    // the generic UNRENDERABLE_MESSAGE ("no notation or tablature") would be
    // false: this score has a TAB clef, a real six-line tuning and seven of
    // its eight notes correctly fretted.
    // Not openScore(): this score is genuinely unrenderable (every staff
    // disqualified, supportedProfiles() empty), so data-score-render-ok
    // never becomes "true" and the play button's own readiness is a
    // different question from the one this test asks. publish() still
    // writes data-score-profiles as soon as scoreLoaded runs, though - ""
    // once profiles is known to be empty, present (not absent) precisely
    // because a score HAS loaded - so waiting on that value is the correct
    // sync point for "this score's degraded/disclosed state has settled".
    const errors = watchForErrors(page);
    await stubNavigationTabOnlyInvalidStringScore(page);
    await page.goto("/#/score/24");
    await expect(host(page)).toHaveAttribute("data-score-profiles", "", { timeout: 30_000 });
    await page.waitForTimeout(500);
    expect(errors.consoleErrors).toEqual([]);
    expect(errors.pageErrors).toEqual([]);
    expect(await dataScoreTabWithheld(page)).toBe("1");
    expect(await dataScoreProfiles(page)).toEqual([]);
    const text = await noticeText(page);
    expect(text).toBe(tabWithheldMessage(1));
    expect(text).not.toBe(UNRENDERABLE_MESSAGE);
  });
});
