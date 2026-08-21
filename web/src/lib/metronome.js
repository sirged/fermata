// The practice metronome's arithmetic: what tempo to click at, and how a bar
// of a given time signature should be subdivided and accented. Pure and
// import-free on purpose, the same way pitch.js is - no runes, no alphaTab,
// nothing that needs a browser - so it can be tested directly rather than
// through a rendered score.
//
// score-render.js is the only file that turns these numbers into sound (see
// docs/rendering.md on why the renderer itself stays behind that one seam).
// This file never touches audio, the renderer, or the DOM.

/** A click slower than this stops being a metronome and starts being a wait. */
export const MIN_METRONOME_BPM = 20;
/** A click faster than this is not a tempo practice happens at. */
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

export function clampBpm(bpm) {
  return Math.min(MAX_METRONOME_BPM, Math.max(MIN_METRONOME_BPM, bpm));
}

/**
 * The tempo the practice metronome should click at right now.
 *
 * `mode: "bpm"` ignores the score entirely - a number set directly, for when
 * the marking is wrong, aspirational, or simply not the speed this passage
 * is being worked at.
 *
 * `mode: "proportion"` takes `proportion` of `scoreTempo` - the score's OWN
 * tempo at the playhead, never the playback speed a caller may have set
 * separately. Call this again as `scoreTempo` moves (a piece that changes
 * tempo internally) and the answer moves with it; nothing here resolves it
 * once and remembers.
 *
 * Always clamped to a countable range - see MIN/MAX_METRONOME_BPM.
 */
export function effectiveMetronomeBpm({ mode, bpm, proportion, scoreTempo }) {
  if (mode === "bpm") {
    return clampBpm(Number.isFinite(bpm) && bpm > 0 ? bpm : DEFAULT_METRONOME_BPM);
  }
  const base = Number.isFinite(scoreTempo) && scoreTempo > 0 ? scoreTempo : FALLBACK_SCORE_TEMPO;
  const ratio = Number.isFinite(proportion) && proportion > 0 ? proportion : 1;
  return clampBpm(base * ratio);
}

/**
 * How a bar of `numerator`/`denominator` should be clicked: `clicksPerBar`
 * ticks, one per `unit` (a denominator-valued note - an eighth for .../8),
 * with a tick accented every `accentEvery` ticks starting from the first.
 *
 * Compound meters (6/8, 9/8, 12/8, ...) click the subdivision rather than the
 * notated beat, because a bare click on the dotted-quarter pulse leaves the
 * two or three eighth notes inside it to guesswork - exactly the "hard to
 * place in compound meters" a bare click fails at. The grouping comes back
 * as the accent instead: every third click is a main pulse (1, 4, 7, ...),
 * the two between it are subdivision only.
 *
 * 3/8 asks the same "numerator a multiple of 3, denominator 8" question the
 * compound branch below answers, and lands there too rather than needing a
 * carve-out: a single group of three IS the whole bar, so accenting "every
 * third click starting from the first" and "only the first click" are the
 * same instruction when there are only three clicks to begin with. Nothing
 * downstream can tell the two branches apart for 3/8, which is exactly why
 * there is nothing here to tell them apart with.
 */
export function metronomePattern(numerator, denominator) {
  const n = Number.isInteger(numerator) && numerator > 0 ? numerator : 4;
  const d = Number.isInteger(denominator) && denominator > 0 ? denominator : 4;
  const isCompound = d === 8 && n % 3 === 0;
  return {
    clicksPerBar: n,
    accentEvery: isCompound ? 3 : n,
    unit: d,
  };
}

/**
 * Seconds between one click and the next, at `bpm` - always a quarter-note
 * tempo, the same convention MIDI and MusicXML tempo markings already use
 * regardless of the notated meter - for a click on a `unit`-valued note.
 */
export function secondsPerClick(bpm, unit) {
  return (60 / bpm) * (4 / unit);
}

/**
 * Which time signature is active at `tick`, from a flat, tick-ordered list of
 * `{startTick, numerator, denominator}` - not the renderer's own bar model,
 * so this has no reason to import it (score-render.js builds the list from
 * that model once, when a score loads). Binary search: a long score can run
 * into the thousands of bars and this runs on every scheduled click.
 *
 * Defaults to 4/4 for an empty list - there is no time signature to be wrong
 * about before any score has loaded.
 */
export function timeSignatureAtTick(bars, tick) {
  if (!bars || bars.length === 0) return { numerator: 4, denominator: 4 };
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
  return { numerator: found.numerator, denominator: found.denominator };
}
