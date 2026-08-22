// The practice metronome's arithmetic: what tempo to click at, and how a bar
// of a given time signature should be subdivided and accented. Pure and
// import-free on purpose, the same way pitch.js is - no runes, no alphaTab,
// nothing that needs a browser - so it can be tested directly rather than
// through a rendered score.
//
// metronome-engine.js is the only file that turns these numbers into sound.
// This file never touches audio, the renderer, or the DOM.

/** A click slower than this stops being a metronome and starts being a wait. */
export const MIN_METRONOME_BPM = 20;
// A click faster than this is not a tempo practice happens at - and, just as
// importantly, keeps the period between clicks (60_000 / this, in ms) safely
// above METRONOME_CLICK_SECONDS in metronome-engine.js: at 400 that is a 150ms
// period against a ~70ms envelope, comfortably clear of one click's tail
// overlapping the next one's attack. This bounds the CLICK RATE itself
// (clicks per minute - what is displayed and what is scheduled, always the
// same number - see effectiveClickRate), not a quarter-note tempo prior to
// any per-meter conversion: clamping before that conversion let an extreme
// meter (4/128, or even a plain 6/8) push the actual rate far past what
// either this constant or the envelope could survive.
export const MAX_METRONOME_BPM = 400;
/** Where a fixed-BPM click starts before a player has chosen otherwise. */
export const DEFAULT_METRONOME_BPM = 120;
// alphaTab's own fallback when nothing in the score declares a tempo at all
// (Score.tempo returns exactly this when the first bar carries no tempo
// automation). A proportion taken of "no tempo marked" has no better answer
// than the same fallback the renderer already uses for that score - so a
// score with nothing marked behaves exactly like one marked "quarter = 120",
// rather than being a special case this file invents its own rule for.
export const FALLBACK_SCORE_TEMPO = 120;

/**
 * Pulled into the countable range, whatever was handed in - including a
 * value that is not usably a number at all. Guarded here, at the definition,
 * rather than trusted to every caller: `clampBpm(NaN)` propagating NaN
 * through Math.min/Math.max is one call site away from an unterminating
 * scheduler loop (secondsPerClick(NaN) is NaN, and `while (next < ...)` never
 * becomes false against NaN).
 */
export function clampBpm(bpm) {
  const n = Number(bpm);
  if (!Number.isFinite(n)) return MIN_METRONOME_BPM;
  return Math.min(MAX_METRONOME_BPM, Math.max(MIN_METRONOME_BPM, n));
}

/**
 * The click RATE - clicks per minute, full stop - the practice metronome
 * should sound right now. This is deliberately the one number both scheduled
 * and displayed: a caller that reports something else (a quarter-note tempo
 * a listener would have to convert in their head to match what they are
 * actually hearing) is exactly the "displayed and sounded disagree" failure
 * this function exists to rule out by construction.
 *
 * `mode: "bpm"` ignores the score AND the meter entirely - the typed number
 * IS the rate, in every time signature, which is what a physical
 * metronome's dial means and what the issue asks for: a tempo set directly,
 * regardless of what the score says.
 *
 * `mode: "proportion"` takes `proportion` of `scoreTempo` - the score's OWN
 * quarter-note tempo at the playhead, never the playback speed a caller may
 * have set separately - and converts THAT onto `unit` (the meter's own click
 * unit, `unit/4`: an eighth-note meter clicks twice per quarter, a
 * half-note meter once every two). Call this again as `scoreTempo` or `unit`
 * move - a piece that changes tempo or time signature internally - and the
 * answer moves with it; nothing here resolves it once and remembers.
 *
 * The conversion happens BEFORE clamping, not after: clamping the
 * quarter-note value first and converting second would let a compound or
 * unusually-fast meter push the actual clicked rate past MAX_METRONOME_BPM
 * without ever tripping it.
 */
export function effectiveClickRate(request) {
  return clampBpm(rawClickRate(request));
}

/**
 * The same rate BEFORE clampBpm pulls it into the countable range - the exact
 * number that was asked for, whether or not a metronome can sound it.
 *
 * This exists so an interface can say WHY a value it is showing has stopped
 * matching the setting that produced it. 15% of a piece marked 120 is 18
 * clicks a minute, which MIN_METRONOME_BPM correctly refuses; the click then
 * runs at 20, and a control still reading "15%" beside a readout of "20" is a
 * number that no longer describes what is sounding. Showing the true rate is
 * most of the answer, but not all of it: without the reason, the disagreement
 * reads as a bug rather than as a floor. Comparing this against
 * effectiveClickRate is how the interface knows to say so.
 *
 * Deliberately the SAME arithmetic, expressed once: effectiveClickRate is
 * defined in terms of this rather than the two sharing a copied formula, so
 * there is no way for the number shown, the number scheduled and the number
 * checked against the limits to drift apart.
 */
export function rawClickRate({ mode, bpm, proportion, scoreTempo, unit }) {
  if (mode === "bpm") {
    return Number.isFinite(bpm) && bpm > 0 ? bpm : DEFAULT_METRONOME_BPM;
  }
  const base = Number.isFinite(scoreTempo) && scoreTempo > 0 ? scoreTempo : FALLBACK_SCORE_TEMPO;
  const ratio = Number.isFinite(proportion) && proportion > 0 ? proportion : 1;
  const u = Number.isInteger(unit) && unit > 0 ? unit : 4;
  return base * ratio * (u / 4);
}

/**
 * How a bar of `numerator`/`denominator` should be clicked: `clicksPerBar`
 * ticks, one per `unit` (a denominator-valued note - an eighth for .../8),
 * with a tick accented every `accentEvery` ticks starting from the first.
 *
 * Compound meters (6/8, 9/8, 12/8, ..., and the same grouping written in
 * sixteenths - 9/16, 12/16, ...) click the subdivision rather than the
 * notated beat, because a bare click on the dotted pulse leaves the two or
 * three notes inside it to guesswork - exactly the "hard to place in
 * compound meters" a bare click fails at. The grouping comes back as the
 * accent instead: every third click is a main pulse (1, 4, 7, ...), the two
 * between it are subdivision only.
 *
 * x/4 meters are deliberately left simple, even a numerator divisible by
 * three (6/4): unlike 6/8, which is unambiguously two dotted-quarter pulses
 * of three eighths, 6/4 is genuinely ambiguous between two dotted-half
 * pulses and six plain quarter-note ones, with no single notational
 * convention to default to - so it clicks as six plain quarters rather than
 * guessing which reading a given piece meant.
 *
 * 3/8 (and 3/16) ask the same "numerator a multiple of 3" question the
 * compound branch below answers, and land there too rather than needing a
 * carve-out: a single group of three IS the whole bar, so accenting "every
 * third click starting from the first" and "only the first click" are the
 * same instruction when there are only three clicks to begin with. Nothing
 * downstream can tell the two branches apart for 3/8, which is exactly why
 * there is nothing here to tell them apart with.
 */
export function metronomePattern(numerator, denominator) {
  const n = Number.isInteger(numerator) && numerator > 0 ? numerator : 4;
  const d = Number.isInteger(denominator) && denominator > 0 ? denominator : 4;
  const isCompound = (d === 8 || d === 16) && n % 3 === 0;
  return {
    clicksPerBar: n,
    accentEvery: isCompound ? 3 : n,
    unit: d,
  };
}

/** Seconds between one click and the next, at a click RATE already in clicks
 * per minute (see effectiveClickRate) - there is no meter or mode left to
 * convert here, which is deliberate: the rate handed in is already the
 * exact number of clicks a minute that both sounds and gets displayed. */
export function secondsPerClick(clickRate) {
  return 60 / clickRate;
}

/**
 * Which bar (of a flat, tick-ordered list) is active at `tick`. `bars` is
 * `{startTick, endTick, numerator, denominator}[]`, in tick order - not the
 * renderer's own model, so this has no reason to import it.
 *
 * createScoreMetronome in score-render.js builds this list from alphaTab's
 * OWN generated-midi-timeline lookup (`api.tickCache.masterBars`), never by
 * summing notated bar durations itself: `tick` lives on the generated MIDI's timeline, which
 * expands repeats and skips unplayed alternate endings, so the notated bar
 * order and the played tick order are different timelines the moment a
 * score has so much as one repeat sign in it. Only the renderer's own lookup
 * knows which bar is actually sounding at a given tick; a hand-summed index
 * would silently answer with the wrong bar - and therefore the wrong meter -
 * for every bar after the first repeat.
 *
 * Binary search: a long score can run into the thousands of bars and this
 * runs on every scheduled click.
 *
 * Returns null for an empty list - there is no bar to be wrong about before
 * anything has loaded.
 */
export function barAtTick(bars, tick) {
  if (!bars || bars.length === 0) return null;
  let lo = 0;
  let hi = bars.length - 1;
  let found = bars[0];
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].startTick <= tick) {
      found = bars[mid];
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/**
 * Which of a bar's `clicksPerBar` slots `tick` falls in - 0 for the first,
 * up to `clicksPerBar - 1` for the last.
 *
 * Derived fresh from the bar's own tick span every time this is called, not
 * carried forward as running state. A persistent counter that only resets
 * when the scheduler starts drifts out of alignment the moment any of these
 * happen: the metronome is switched on mid-bar; the transport seeks; a loop
 * whose length is not a whole number of click periods wraps back to its
 * start; or the meter changes mid-score, carrying the old count into a new
 * modulus. All four are just "the playhead is somewhere this counter didn't
 * expect" - recomputing from the playhead instead means every one of them is
 * the same ordinary case, not a special one to detect and handle.
 *
 * One consequence worth knowing: when the click's own rate does not evenly
 * divide the bar's real duration (a fixed BPM, or a proportion other than
 * 100%), the accented click will not recur at an even spacing measured in
 * clicks - it recurs at an even spacing measured in the music's OWN bars,
 * landing on or just after each real downbeat regardless of how the click's
 * own tempo relates to it. That is the point: the accent marks where the
 * bar actually starts, not a beat this function invented independently of
 * it.
 *
 * `tick` is clamped to the bar's own span, so a click scheduled slightly
 * ahead of the audio clock (see METRONOME_SCHEDULE_AHEAD_S in
 * metronome-engine.js) landing a few ticks past where the playhead has reached
 * so far still answers with the bar's last slot rather than spilling into
 * one that doesn't belong to it.
 *
 * Returns 0 - the downbeat - when `bar` is null (nothing loaded yet) or
 * degenerate, rather than propagating NaN into a modulus.
 */
export function clickPhaseInBar(tick, bar, clicksPerBar) {
  if (!bar || !(clicksPerBar > 0)) return 0;
  const barTicks = bar.endTick - bar.startTick;
  if (!(barTicks > 0)) return 0;
  const ticksPerClick = barTicks / clicksPerBar;
  const ticksIntoBar = Math.min(Math.max(tick - bar.startTick, 0), barTicks - 1e-6);
  return Math.floor(ticksIntoBar / ticksPerClick) % clicksPerBar;
}
