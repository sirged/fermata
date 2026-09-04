// The N-random-edits divergence fuzz guard (#189), the stronger check the #10
// evaluation asked for once the editor grew operations that REORDER or
// ADD/REMOVE notes (voice moves #182, deletes #186): after N seeded random
// edits, RE-IMPORT the written MusicXML and assert the on-screen model equals
// it across EVERY note - not just the selected one the per-selection guard
// cross-checks. A positional-map SHIFT (the renderer's ordinal N naming a
// different note than the document's ordinal N) is invisible to the
// per-selection guard unless that exact note is selected; this catches it.
//
// Design, stated so it can be audited rather than inferred:
//   - Seeded, reproducible: the whole edit sequence is driven from a SEEDED
//     mulberry32 RNG (SEED below), so a failing run reproduces exactly. The seed
//     and the failing step are emitted in the assertion message. Never
//     Math.random().
//   - Fixed for CI, sweepable locally: SEED and N are constants in this spec so
//     CI is deterministic and cheap; FERMATA_FUZZ_SEED / FERMATA_FUZZ_N override
//     them for a larger local sweep.
//   - The score: the real polyphonic fixture (fixtures/editor-poly.js,
//     POLY_MUSICXML) - two voices with backups, chords and ties across eight
//     4/4 bars (~50 sounding notes). Polyphony is the point: it is where a voice
//     move or a delete can shift an ordinal.
//   - The edit menu, uniform over the shipped ops that touch note IDENTITY or
//     ORDERING: fret, string, accidental, enharmonic, tie, voice move, delete,
//     and (#238) restToNote - turning one of the rests "delete" itself
//     produces back into a note, addressed by its OWN ordinal space
//     (h.restCount(), not h.count() - see the driver below) since it starts
//     from a rest, not a sounding note.
//     Duration and dots are deliberately NOT in the menu: setDurationType/setDots
//     change one note's written value WITHOUT refilling the bar (see their
//     docstrings), so stacking them drives a polyphonic bar out of this profile's
//     Rule 8 - a validity question distinct from the positional-map integrity
//     this guard proves, and one #183 already covers per-op.
//   - Refusals are not failures: an op that legitimately refuses (a tie to a
//     different pitch, a voice already occupied at the onset) leaves the document
//     untouched and the sequence continues.
//   - The equality asserted: see auditAllNotes in TabViewer.svelte - count,
//     per-ordinal pitch/string/fret/voice between the render and the re-import,
//     plus a full written-document round-trip (id, spelling, string, fret, voice,
//     onset, duration via type+dots, ties).
//
// The crucial second test proves the guard is not green-by-construction: a
// deliberate stale-render divergence is induced and the audit is shown to go red
// and NAME the note - a fuzz guard that cannot fail is worthless.
import { test, expect } from "@playwright/test";

import { POLY_MUSICXML } from "./fixtures/editor-poly.js";
import { stubEditorApi } from "./fixtures/editor-score.js";

const wrap = (page) => page.locator(".staff-render .wrap");
const host = (page) => page.locator(".staff-render .at-host");

const SEED = Number(process.env.FERMATA_FUZZ_SEED ?? 0x1a2b3c);
const N = Number(process.env.FERMATA_FUZZ_N ?? 40);

async function renderedOk(page) {
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true");
}

// Opens the editor over `content` with the fuzz harness enabled, waiting until
// the whole score's `expected` sounding notes are laid out.
async function openEditor(page, content, expected) {
  await page.addInitScript(() => {
    window.__fermataEditorHarness = true;
  });
  const handle = await stubEditorApi(page, content);
  await page.goto("/#/score/1");
  await page.waitForSelector(".staff-render");
  await page.getByRole("button", { name: "Staff", exact: true }).click();
  await renderedOk(page);
  await page.getByRole("button", { name: "Edit notes" }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
  await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(expected);
  await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness?.count() ?? 0)).toBe(expected);
  return handle;
}

test.describe("note editor - N-random-edits divergence fuzz guard", () => {
  // Target 1: N seeded random edits on a real polyphonic score, the per-edit
  // divergence flag green throughout, and after the run the written MusicXML
  // re-imports to a model identical to the one on screen (auditAllNotes ok).
  test(`${N} seeded random edits keep the model and the render identical (seed ${SEED})`, async ({ page }) => {
    await openEditor(page, POLY_MUSICXML, 52);

    // The whole sequence runs in one page evaluate for speed and determinism -
    // the seeded RNG is pure, so this reproduces byte-for-byte. It awaits each
    // real edit's reload/re-render before the next, and records the first step
    // (if any) where the per-edit divergence flag went false.
    const result = await page.evaluate(
      async ({ seed, n }) => {
        // mulberry32: a small, fast, well-distributed seeded PRNG. Deterministic
        // from `seed`, so the same seed replays the same sequence.
        function mulberry32(a) {
          return function () {
            a |= 0;
            a = (a + 0x6d2b79f5) | 0;
            let t = Math.imul(a ^ (a >>> 15), 1 | a);
            t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
          };
        }
        const rng = mulberry32(seed);
        const pick = (arr) => arr[Math.floor(rng() * arr.length)];
        const h = window.__scoreEditorHarness;
        const stringCount = h.stringCount();
        // "restToNote" (#238) joins the menu alongside the other ops that
        // touch note IDENTITY or ORDERING: like voice/delete, it changes which
        // ordinal is which sounding note (a rest becomes one), so it belongs
        // in the SAME "structural" bucket those two are counted in below. It
        // is addressed differently from every other op here - by a REST
        // ordinal (h.restCount()), not a sounding one - since "delete" is
        // exactly what supplies the fresh rests this op then converts back.
        const OPS = ["fret", "string", "accidental", "enharmonic", "tie", "voice", "delete", "restToNote"];
        const opCounts = {};
        let applied = 0;
        let refused = 0;
        let firstBadStep = -1;
        let firstBad = null;
        let restSkipped = 0;
        const steps = [];
        for (let i = 0; i < n; i++) {
          const op = pick(OPS);
          let ordinal;
          let arg = null;
          if (op === "restToNote") {
            const restCount = h.restCount();
            if (restCount === 0) {
              // Nothing to convert yet (no rest exists this early in the
              // sequence) - skip the step rather than force a target that
              // does not exist; "delete" earlier in the run is what supplies
              // one.
              restSkipped += 1;
              continue;
            }
            ordinal = Math.floor(rng() * restCount);
            arg = { string: 1 + Math.floor(rng() * stringCount), fret: Math.floor(rng() * 13) };
          } else {
            const count = h.count();
            if (count === 0) break;
            ordinal = Math.floor(rng() * count);
            if (op === "fret") arg = Math.floor(rng() * 13);
            else if (op === "string") arg = 1 + Math.floor(rng() * stringCount);
            else if (op === "accidental") arg = pick([-2, -1, 0, 1, 2]);
            else if (op === "enharmonic") arg = pick([-1, 1]);
            else if (op === "voice") arg = 1 + Math.floor(rng() * 3);
          }
          const r = await h.apply(ordinal, op, arg);
          opCounts[op] = (opCounts[op] ?? 0) + 1;
          if (r.applied) applied += 1;
          if (r.refused) refused += 1;
          steps.push({ i, op, ordinal, arg, applied: r.applied, refused: r.refused, divergenceOk: r.divergenceOk });
          if (r.divergenceOk === false && firstBadStep < 0) {
            firstBadStep = i;
            firstBad = { step: steps[steps.length - 1], audit: h.audit() };
            break;
          }
        }
        return {
          seed,
          n,
          applied,
          refused,
          opCounts,
          restSkipped,
          firstBadStep,
          firstBad,
          finalCount: h.count(),
          renderCount: window.__scoreEditor.noteCount(),
          audit: h.audit(),
        };
      },
      { seed: SEED, n: N },
    );

    // The per-edit guard never went red across the whole sequence.
    expect(
      result.firstBadStep,
      `per-edit divergence flag went red. seed=${SEED} step=${result.firstBadStep} ` +
        `detail=${JSON.stringify(result.firstBad)}`,
    ).toBe(-1);

    // At least some edits actually applied AND at least one of the reorder/
    // add/remove ops landed - a run that refused everything would prove nothing.
    expect(result.applied, "no edit applied - the sequence exercised nothing").toBeGreaterThan(5);
    const structural = (result.opCounts.voice ?? 0) + (result.opCounts.delete ?? 0);
    expect(structural, "no voice-move or delete was attempted").toBeGreaterThan(0);
    // The new operation (#238) actually ran at least once, on a genuine rest -
    // POLY_MUSICXML starts with two, and "delete" earlier in the same run
    // supplies more, so restCount() is never 0 for the whole sequence.
    expect(
      result.opCounts.restToNote ?? 0,
      `restToNote was never attempted (restSkipped=${result.restSkipped})`,
    ).toBeGreaterThan(0);

    // The written MusicXML re-imports to a model identical to the one on screen,
    // across every note - the fuzz guard's assertion.
    expect(
      result.audit.divergences,
      `after ${N} edits the re-import diverged from the render. seed=${SEED} ` +
        `divergences=${JSON.stringify(result.audit.divergences)}`,
    ).toEqual([]);
    expect(result.audit.ok).toBe(true);
    expect(result.audit.docCount).toBe(result.audit.renderCount);
    expect(result.renderCount).toBe(result.finalCount);

    // The live DOM flag the per-selection guard drives is green too.
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  // Target 2 (the crucial one): the guard actually CATCHES a divergence. A
  // stale-render divergence is induced deterministically - the document is edited
  // but the view is NOT reloaded, exactly the positional-map/stale-render shift
  // the audit exists to catch - and the audit is shown to go red and NAME the
  // note that diverged. Then a real reload heals it, proving the red was the
  // divergence and not a broken audit.
  test("the audit reds and names a note when the render is left stale against the document", async ({ page }) => {
    await openEditor(page, POLY_MUSICXML, 52);

    // Baseline: the freshly-loaded model and render agree.
    const clean = await page.evaluate(() => window.__scoreEditorHarness.audit());
    expect(clean.ok, `baseline audit should be clean: ${JSON.stringify(clean.divergences)}`).toBe(true);

    // Plant a divergence: change ordinal 0's fret in the DOCUMENT only, skipping
    // the re-render. The written MusicXML now says one thing; the screen still
    // shows the old note.
    const planted = await page.evaluate(() => {
      const h = window.__scoreEditorHarness;
      const before = h.noteAt(0);
      const changed = h.corrupt(0, "fret", (before.fret + 5) % 13 === before.fret ? before.fret + 1 : (before.fret + 5) % 13);
      const audit = h.audit();
      return { before, changed: changed.changed, audit };
    });

    expect(planted.changed, "the planted document edit should have applied").toBe(true);
    // The audit goes red...
    expect(planted.audit.ok).toBe(false);
    // ...and NAMES the divergence: ordinal 0, a pitch/fret mismatch, with the
    // document value and the (stale) render value both reported.
    const named = planted.audit.divergences.filter((d) => d.ordinal === 0 && (d.field === "fret" || d.field === "midi"));
    expect(
      named.length,
      `the audit should name ordinal 0's fret/midi divergence, got ${JSON.stringify(planted.audit.divergences)}`,
    ).toBeGreaterThan(0);
    const fretDiv = named.find((d) => d.field === "fret");
    expect(fretDiv.render).toBe(planted.before.fret); // the screen still shows the old fret
    expect(fretDiv.doc).not.toBe(planted.before.fret); // the document holds the new one

    // Heal it: a real edit through the normal path RELOADS the view, so the
    // render matches the document again and the audit goes green - proving the
    // red above was the divergence, not a guard that is always red. (Re-applying
    // the note's CURRENT fret would be a no-op that skips the reload, so a
    // genuinely different fret is used to force the re-render.)
    const healed = await page.evaluate(async () => {
      const h = window.__scoreEditorHarness;
      const now = h.noteAt(0);
      const other = (now.fret + 1) % 13;
      await h.apply(0, "fret", other);
      return { audit: h.audit(), applied: h.noteAt(0).fret === other };
    });
    expect(healed.applied).toBe(true);
    expect(healed.audit.ok, `after a real reload the audit should be clean: ${JSON.stringify(healed.audit.divergences)}`).toBe(true);
  });

  // Target 4-adjacent: the fuzz's own harness aside, a plain fret edit on the
  // polyphonic fixture keeps the per-selection guard green - the earlier #10
  // guard still holds over a two-voice document driven note-by-note.
  test("a single fret edit on the polyphonic score keeps the per-selection guard green", async ({ page }) => {
    await openEditor(page, POLY_MUSICXML, 52);
    const r = await page.evaluate(async () => {
      const h = window.__scoreEditorHarness;
      const res = await h.apply(0, "fret", 7);
      return { res, audit: h.audit() };
    });
    expect(r.res.applied).toBe(true);
    expect(r.res.divergenceOk).toBe(true);
    expect(r.audit.ok, JSON.stringify(r.audit.divergences)).toBe(true);
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "7");
  });

  // The specific editor gap this fuzz FOUND (then fixed), pinned as its own
  // falsifiable test: a tie must join the same FRETBOARD POSITION, not merely the
  // same sounding pitch. alphaTab draws a tie-stop note at a fret derived from its
  // start, so a tie across two positions that only sound alike renders one pitch
  // while the document holds the other - a whole-model divergence the per-
  // selection guard never sees (the diverging note is the unselected partner).
  test("a tie between two same-pitch but different-position notes is refused", async ({ page }) => {
    await openEditor(page, POLY_MUSICXML, 52);
    const out = await page.evaluate(async () => {
      const h = window.__scoreEditorHarness;
      // Notes 0 (E4, string 1 fret 0) and 1 (F#4, string 1 fret 2) are contiguous
      // in voice 1. Re-fret note 1 to E4 on ANOTHER string+fret: string 2 fret 5
      // = E4 (64), the SAME sounding pitch as note 0 but a different position.
      await h.apply(1, "string", 2);
      await h.apply(1, "fret", 5);
      const n0 = h.noteAt(0);
      const n1 = h.noteAt(1);
      // Now try to tie 0 -> 1. Same MIDI, different position: it must refuse.
      const tie = await h.apply(0, "tie", null);
      return { n0, n1, tie, after0: h.noteAt(0), audit: h.audit() };
    });
    expect(out.n0.midi).toBe(64);
    expect(out.n1.midi).toBe(64); // same sounding pitch
    expect(out.n0.string).not.toBe(out.n1.string); // different position
    expect(out.tie.refused).toBe(true); // the tie is refused
    expect(out.tie.applied).toBe(false);
    expect(out.after0.tieStart).toBe(false); // nothing was written
    expect(out.audit.ok, JSON.stringify(out.audit.divergences)).toBe(true);
  });

  // The other half of the same fix: a fret edit that MOVES a tied note off its
  // partner's position breaks the now-invalid tie on BOTH ends, rather than
  // leaving a tie the renderer would draw wrong.
  test("moving a tied note to another position breaks the tie", async ({ page }) => {
    await openEditor(page, POLY_MUSICXML, 52);
    const out = await page.evaluate(async () => {
      const h = window.__scoreEditorHarness;
      // Make note 1 the SAME position as note 0 (string 1 fret 0), then tie 0->1.
      await h.apply(1, "fret", 0);
      const tie = await h.apply(0, "tie", null);
      const tiedStart = h.noteAt(0).tieStart;
      const tiedStop = h.noteAt(1).tieStop;
      // Move note 1 to a different fret: the tie must break on both ends.
      await h.apply(1, "fret", 5);
      return {
        tie,
        tiedStart,
        tiedStop,
        after0Start: h.noteAt(0).tieStart,
        after1Stop: h.noteAt(1).tieStop,
        audit: h.audit(),
      };
    });
    expect(out.tie.applied).toBe(true); // the tie was created (same position)
    expect(out.tiedStart).toBe(true);
    expect(out.tiedStop).toBe(true);
    // After moving note 1's fret, both halves are gone.
    expect(out.after0Start).toBe(false);
    expect(out.after1Stop).toBe(false);
    expect(out.audit.ok, JSON.stringify(out.audit.divergences)).toBe(true);
  });
});
