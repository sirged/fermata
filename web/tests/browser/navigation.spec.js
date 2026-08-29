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
// literally rather than derived from the fixture):
//   - deleting the applyLoadedNavigation() call from score-render.js's
//     scoreLoaded handler put the navigation fixture back to the straight
//     `1 2 3 4 5 6 7 8` and reddened ALL SIX tests here - including the live
//     one, which read `1 2 3 4 5 6 7` off the audio timeline;
//   - mapping "al Coda" to the plain Direction.JumpDalSegno instead reddened
//     five (the unresolved-target test is the one it cannot touch) and gave
//     `1 2 3 4 1 2 3 4 5 6 7 8`. That is the plausible wrong answer, not an
//     arbitrary one: it is exactly the order this issue measured from
//     hoisting the `<sound>` elements to measure level and letting alphaTab's
//     own importer read them, so these tests are pinned against the losing
//     route as well as against no route at all.
import { expect, test } from "@playwright/test";
import {
  stubNavigationRepeatScore,
  stubNavigationScore,
  stubNavigationUnresolvedScore,
} from "./fixtures/navigation-score.js";

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
