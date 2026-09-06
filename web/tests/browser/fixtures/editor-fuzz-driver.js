// The N-random-edits fuzz DRIVER (#189), lifted out of
// ../score-editor-fuzz-189.spec.js so a SECOND input can be run through the
// same one (#262: a native MusicXML score opened from the library file rather
// than from a transcription row).
//
// Why extracted rather than copied: the guard's value is that the sequence is
// seeded and reproducible, and two copies of a driver are two sequences that
// can silently stop being the same one. Everything below is the original code,
// unchanged in behaviour - the spec that owned it still makes every assertion
// it made before, against the same seed and the same fixture, and its numbers
// (applied/refused/per-op counts) are re-measured, not assumed, in its own
// comments.
//
// Nothing here asserts. It drives the harness and reports what happened; the
// specs decide what that has to be.

/**
 * Run `n` seeded random edits through window.__scoreEditorHarness and report
 * what happened - see score-editor-fuzz-189.spec.js's header for the design
 * (why mulberry32, why these ops, why duration/dots are deliberately absent).
 *
 * The whole sequence runs in ONE page.evaluate for speed and determinism: the
 * seeded RNG is pure, so this reproduces byte-for-byte. It awaits each real
 * edit's reload/re-render before the next, and stops at the first step (if any)
 * where the per-edit divergence flag went false.
 */
export function runSeededEdits(page, { seed, n }) {
  return page.evaluate(
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
      const OPS = ["fret", "string", "accidental", "enharmonic", "tie", "voice", "delete", "restToNote", "rangeString"];
      const opCounts = {};
      let applied = 0;
      let refused = 0;
      let firstBadStep = -1;
      let firstBad = null;
      let restSkipped = 0;
      // The widest range a "rangeString" gesture actually covered, so the
      // assertions can tell a real multi-note write from a run of gestures
      // that each happened to reach only their anchor (an extension stops at
      // a rest, at a voice change and at the document edge).
      let rangeWidest = 0;
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
          if (op === "rangeString") arg = { k: Math.floor(rng() * 4), string: 1 + Math.floor(rng() * stringCount) };
          else if (op === "fret") arg = Math.floor(rng() * 13);
          else if (op === "string") arg = 1 + Math.floor(rng() * stringCount);
          else if (op === "accidental") arg = pick([-2, -1, 0, 1, 2]);
          else if (op === "enharmonic") arg = pick([-1, 1]);
          else if (op === "voice") arg = 1 + Math.floor(rng() * 3);
        }
        const r = await h.apply(ordinal, op, arg);
        if (op === "rangeString" && r.range && r.range.length > rangeWidest) rangeWidest = r.range.length;
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
        rangeWidest,
        firstBadStep,
        firstBad,
        finalCount: h.count(),
        renderCount: window.__scoreEditor.noteCount(),
        audit: h.audit(),
      };
    },
    { seed, n },
  );
}

/**
 * Deliberately induce a stale-render divergence - change ordinal 0's fret in
 * the DOCUMENT only, skipping the re-render, which is exactly the positional-
 * map/stale-render shift the whole-model audit exists to catch. Reports the
 * note as it was, whether the planted change applied, and the audit taken
 * immediately afterwards.
 */
export function plantStaleDivergence(page) {
  return page.evaluate(() => {
    const h = window.__scoreEditorHarness;
    const before = h.noteAt(0);
    const changed = h.corrupt(
      0,
      "fret",
      (before.fret + 5) % 13 === before.fret ? before.fret + 1 : (before.fret + 5) % 13,
    );
    const audit = h.audit();
    return { before, changed: changed.changed, audit };
  });
}

/**
 * Heal a planted divergence: a real edit through the normal path RELOADS the
 * view, so the render matches the document again. Re-applying the note's
 * CURRENT fret would be a no-op that skips the reload, so a genuinely different
 * fret is used to force the re-render.
 */
export function healStaleDivergence(page) {
  return page.evaluate(async () => {
    const h = window.__scoreEditorHarness;
    const now = h.noteAt(0);
    const other = (now.fret + 1) % 13;
    await h.apply(0, "fret", other);
    return { audit: h.audit(), applied: h.noteAt(0).fret === other };
  });
}
