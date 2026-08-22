// The metronome itself: a click, on its own clock, that knows nothing about
// scores, the notation renderer, or the DOM.
//
// This is the whole tool. Every place in Fermata that wants a click calls
// createMetronomeEngine and pre-fills it from whatever context it has - a
// piece's tempo and time signature in the score viewer, the tempo you last
// practised at on the practice page, plain defaults on its own - and every
// pre-filled value stays adjustable afterwards. A pre-fill is a starting
// point, never a constraint.
//
// It used to live inside score-render.js, which docs/rendering.md establishes
// as the SOLE seam to the notation renderer - the one file allowed to know
// that library's vocabulary, so the library stays replaceable. The click had
// no business there, and the reason it had none is the same reason it works:
// it deliberately does NOT use the renderer's audio. alphaTab generates its
// own metronome as a track in the same MIDI file as the notes, ticking at the
// score's tempo and scaled by playbackSpeed exactly like every other event in
// that file; there is no setting anywhere in its public API that lets the
// click run at a different tempo from the notes beside it. Practice wants
// them to disagree constantly - a click at full tempo over a passage slowed
// to 70%, a fixed BPM the marking has nothing to do with - so this was always
// an independent Web Audio path that merely happened to be written inside the
// renderer seam. Nothing about it is renderer-specific, and moving it out is
// what lets everything else call it.
//
// The scheduling below was MOVED, not rewritten. It has been measured by two
// independent reviews - driftless over hundreds of clicks, no leaks, silent
// when it should be - and every property those reviews established is
// preserved verbatim: the audio-clock lookahead with its catch-up floor, the
// gain headroom, the clamp applied to the real click rate, and (where a
// caller supplies a playhead) a phase derived from that playhead rather than
// counted from when the click started.
//
// The scheduler is the standard "lookahead" pattern for Web Audio timing:
// every METRONOME_LOOKAHEAD_MS a queue is topped up with whatever clicks now
// fall within the next METRONOME_SCHEDULE_AHEAD_S of audio-clock time,
// scheduled by handing the audio node the exact time to start rather than by
// firing it now. A metronome built from one setTimeout per click drifts under
// any main-thread load - exactly the load a page with a soundfont and a
// renderer on it has - and a click that drifts is worse than none, since it
// stops being the thing a player can trust over their own sense of tempo.

import {
  DEFAULT_METRONOME_BPM,
  MAX_METRONOME_BPM,
  MIN_METRONOME_BPM,
  clampBpm,
  clickPhaseInBar,
  metronomePattern,
  rawClickRate,
  secondsPerClick,
} from "./metronome.js";

export const METRONOME_LOOKAHEAD_MS = 25;
export const METRONOME_SCHEDULE_AHEAD_S = 0.12;
export const METRONOME_CLICK_SECONDS = 0.05;
// Accent is both higher-pitched and louder than a plain subdivision tick -
// pitch alone survives a quiet room or a cheap speaker better than volume
// alone does, so the two are stacked rather than picking one. Gains are
// sized so the two can never clip even in the pathological case of one
// click's tail overlapping the next one's attack: 0.6 + 0.35 = 0.95, under
// unity with headroom to spare. MAX_METRONOME_BPM keeps that overlap from
// ever actually happening in practice - see its own comment in metronome.js.
export const METRONOME_ACCENT_HZ = 1500;
export const METRONOME_TICK_HZ = 950;
export const METRONOME_ACCENT_GAIN = 0.6;
export const METRONOME_TICK_GAIN = 0.35;

/** The most a subdivision setting will split one notated click into. Four is
 * as fine as a click stays countable; past that it is a buzz, and the clamp
 * on the resulting RATE (below) would be doing all the work anyway. */
export const MAX_SUBDIVISION = 4;

/**
 * A metronome. Tempo, meter, subdivision, an accent, start and stop.
 *
 * `onTempo(rate, limit)` fires whenever either changes. `rate` is clicks per
 * minute, the same number that is actually scheduled, never a quarter-note
 * tempo a listener would have to convert in their head. `limit` is "slowest",
 * "fastest" or null, and says whether the countable-range clamp - rather than
 * the setting - is what decided that rate; an interface needs it to explain a
 * readout that has stopped matching the control above it. Both fire on a
 * setting change and, while running, whenever the live context under a
 * proportion moves (a tempo change written mid-piece, or the meter itself
 * changing - which changes the rate even when the tempo does not).
 *
 * `onClick(accent, numerator, denominator, phase)` fires once per click ONLY
 * from inside the real scheduling call that creates its oscillator - not
 * merely alongside it - so deleting that function's body silences this too;
 * nothing downstream of it can report a click that scheduleClick never
 * actually attempted. `phase` is the bar-relative slot the click landed in -
 * exposed separately from `accent` so a caller can tell the phase is really
 * being derived from a playhead each time, not merely incrementing.
 *
 * `ready` gates onTempo. It defaults to true, because a metronome standing on
 * its own always has a real number to report from the instant it exists. A
 * caller whose pre-fill arrives asynchronously (the score viewer, waiting for
 * a score to load) passes false and calls setReady(true) once the context is
 * genuinely known, so nothing reports a value that has nothing behind it yet.
 */
export function createMetronomeEngine({ onTempo = () => {}, onClick = () => {}, ready = true } = {}) {
  let enabled = false;
  // The transport gate, separate from `enabled`: "the click is switched on"
  // and "the thing it is counting is under way" are different questions, and
  // only a caller with a transport has the second one. It defaults to true so
  // a metronome with nothing to follow - standalone, or on the practice page
  // - starts the moment it is enabled and needs no second call.
  let running = true;
  let mode = "proportion";
  let proportion = 1;
  let bpm = DEFAULT_METRONOME_BPM;
  let subdivision = 1;
  let accentEnabled = true;
  // The tempo a proportion is a proportion OF, in quarter-notes per minute.
  // null until a caller supplies one; effectiveClickRate falls back to
  // FALLBACK_SCORE_TEMPO, which is the same fallback the renderer itself uses
  // for a score that declares no tempo at all.
  let baseTempo = null;
  // The meter to click when no live pulse source supplies one. This is the
  // pre-fill: a caller sets it from its own context and a player can change
  // it afterwards.
  let meterNumerator = 4;
  let meterDenominator = 4;
  let hasContext = !!ready;

  // The seam this file exists to keep clean. A caller that HAS a playhead -
  // the score viewer, which reads one from the renderer - installs a function
  // here returning `{tick, bar}` on the playhead's own timeline, where `bar`
  // is `{startTick, endTick, numerator, denominator}` or null. That is a plain
  // shape metronome.js already defines; nothing about it is renderer
  // vocabulary, and this file never reaches for a renderer to get it. A caller
  // with no playhead installs nothing and the meter/phase come from the
  // settings above instead.
  let pulseSource = null;

  let audioCtx = null;
  let timer = null;
  let nextClickTime = 0;
  let lastReportedRate = null;
  let lastReportedLimit = null;
  // Oscillators already scheduled but not yet finished. Tracked so stop()
  // can cut them off - otherwise up to METRONOME_SCHEDULE_AHEAD_S of clicks
  // already queued keep sounding after pausing or switching the click off.
  let pendingOscillators = [];
  // The fallback phase, used ONLY when no pulse source is installed - i.e.
  // when there is no playhead in existence to derive a phase from, so there
  // is nothing better to count against than the clicks themselves. Every
  // failure mode a counter has (switched on mid-bar, a seek, a loop wrapping,
  // a mid-score meter change) is a statement about a playhead moving
  // independently of the click, and none of them can arise when the click IS
  // the only timeline. Reset by start() so a fresh run begins on a downbeat.
  let freeRunPhase = 0;

  /** The live music context, or null when nothing supplies one. */
  function context() {
    if (!pulseSource) return null;
    const live = pulseSource();
    if (!live) return null;
    return live;
  }

  /**
   * Everything one click needs, resolved together from one reading of the
   * context - the meter, the rate, the slot in the bar, and whether that slot
   * is accented. Resolved together rather than by four separate lookups on
   * purpose: the rate depends on the meter's unit and the accent depends on
   * the same pattern the phase is taken modulo, so two readings of a context
   * that moves under them can disagree.
   */
  function resolve(live) {
    const bar = live?.bar ?? null;
    const numerator = bar?.numerator ?? meterNumerator;
    const denominator = bar?.denominator ?? meterDenominator;
    const pattern = metronomePattern(numerator, denominator);
    const clicksPerBar = pattern.clicksPerBar * subdivision;
    const accentEvery = pattern.accentEvery * subdivision;
    // Subdivision is folded into effectiveClickRate's INPUTS rather than
    // multiplied onto its output, so MAX_METRONOME_BPM still lands on the
    // rate actually scheduled. Multiplying afterwards would let a
    // subdivision walk the real click rate straight past the clamp - the
    // same mistake the pre-conversion clamp made for compound meters, one
    // setting further along. In proportion mode the meter's own unit is what
    // scales the rate (unit/4), so a finer subdivision is exactly a finer
    // unit; in bpm mode the typed number IS the rate per notated click, so
    // splitting each one in two doubles it.
    const raw =
      mode === "bpm"
        ? rawClickRate({ mode: "bpm", bpm: bpm * subdivision })
        : rawClickRate({
            mode: "proportion",
            proportion,
            scoreTempo: baseTempo,
            unit: denominator * subdivision,
          });
    const rate = clampBpm(raw);
    // Which end of the countable range the clamp is holding this at, or null
    // when it is not holding it anywhere. Reported so an interface can say
    // WHY the rate it is showing has stopped matching the setting that
    // produced it - see rawClickRate in metronome.js. Flagged whenever the
    // clamp is actually load-bearing, not only when the two numbers round
    // differently: at the floor, pressing "slower" does nothing, and that is
    // worth saying either way.
    const limit = raw < MIN_METRONOME_BPM ? "slowest" : raw > MAX_METRONOME_BPM ? "fastest" : null;
    // Keyed on whether a pulse source EXISTS, not on whether it happened to
    // answer with a bar this time. The distinction matters: a caller with a
    // playhead whose bar lookup is not ready yet (the score viewer's tick
    // cache is built during rendering, not necessarily by the instant a score
    // loads) should get clickPhaseInBar's own answer for a null bar - the
    // downbeat, 0 - rather than a counter quietly taking over and reporting a
    // phase against a bar nothing has established. The counter is for the case
    // where there is no playhead in existence at all, and only that case.
    const phase = pulseSource
      ? clickPhaseInBar(live?.tick ?? 0, bar, clicksPerBar)
      : ((freeRunPhase % clicksPerBar) + clicksPerBar) % clicksPerBar;
    return {
      numerator,
      denominator,
      clicksPerBar,
      phase,
      limit,
      accent: accentEnabled && phase % accentEvery === 0,
      // Rounded ONCE, here, and used unrounded nowhere else: both the
      // scheduler and the readout consume exactly this number. A display that
      // rounds separately from what the scheduler consumes is exactly the
      // "shows one number, sounds another" mismatch this function exists to
      // rule out - 120.6 must never display 121 while clicking 120.6 a minute.
      rate: Math.round(rate),
    };
  }

  function report(live) {
    if (!hasContext) return;
    const { rate, limit } = resolve(live ?? context());
    // De-duplicated on BOTH, because the limit can change while the rate does
    // not: a raw rate of 19.9 and one of 15 both clamp to 20, but only the
    // second is a setting the click has stopped honouring. Keying on the rate
    // alone would announce the first and swallow the second.
    if (rate === lastReportedRate && limit === lastReportedLimit) return;
    lastReportedRate = rate;
    lastReportedLimit = limit;
    onTempo(rate, limit);
  }

  function shouldRun() {
    return enabled && running;
  }

  // Fires onClick from INSIDE the call that actually creates and starts the
  // oscillator - not as a separate, sibling call a caller could satisfy by
  // deleting this function's body and leaving the notification behind. A
  // click that gets reported without a real audio node behind it is exactly
  // the failure this project has shipped before: a test - or a player - that
  // trusts a value the interface merely intended to produce.
  function scheduleClick(time, accent, numerator, denominator, phase) {
    const ctx = audioCtx;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = accent ? METRONOME_ACCENT_HZ : METRONOME_TICK_HZ;
    osc.connect(gain);
    gain.connect(ctx.destination);
    const peak = accent ? METRONOME_ACCENT_GAIN : METRONOME_TICK_GAIN;
    // A short percussive envelope, not a sustained tone: near-instant attack
    // so the click reads as a transient a note can be placed against, then an
    // exponential decay - linear ramps to silence read as a cut-off, not a
    // click's natural decay.
    gain.gain.setValueAtTime(0, time);
    gain.gain.linearRampToValueAtTime(peak, time + 0.001);
    gain.gain.exponentialRampToValueAtTime(0.0001, time + METRONOME_CLICK_SECONDS);
    // Reported before start() is actually called, not after: a caller
    // hooking start() itself to observe the real scheduling (as this
    // project's own test suite does) needs the click already announced by
    // the time that hook runs, or a snapshot taken there reads the
    // PREVIOUS click's state instead of this one's. Still only reachable by
    // way of this function actually running - createOscillator, the gain
    // envelope and this call all have to happen first - so deleting
    // scheduleClick's body still silences onClick exactly as intended.
    onClick(accent, numerator, denominator, phase);
    osc.start(time);
    osc.stop(time + METRONOME_CLICK_SECONDS + 0.02);
    pendingOscillators.push(osc);
    osc.onended = () => {
      const i = pendingOscillators.indexOf(osc);
      if (i !== -1) pendingOscillators.splice(i, 1);
    };
  }

  function tick() {
    if (!audioCtx) return;
    // A stall longer than the lookahead window - a garbage collection pause,
    // a profile switch re-rendering mid-playback, a backgrounded tab whose
    // timers get throttled to once a second while its AudioContext keeps
    // running - leaves nextClickTime sitting in the past. The while loop
    // below would otherwise schedule every missed click at whatever instant
    // it is now already past due, and Web Audio fires a whole burst of them
    // at once: audible, alarming, and not a metronome recovering, just
    // catching up. The right answer is to drop what was missed and resume
    // from now - there is no "correct" time left to play a click that was
    // due half a second ago.
    if (nextClickTime < audioCtx.currentTime) {
      nextClickTime = audioCtx.currentTime + METRONOME_LOOKAHEAD_MS / 1000;
    }
    while (nextClickTime < audioCtx.currentTime + METRONOME_SCHEDULE_AHEAD_S) {
      // A live pulse source answers "where is the playhead RIGHT NOW", not
      // "where will it be when this click, scheduled up to
      // METRONOME_SCHEDULE_AHEAD_S from now, actually sounds" - a real but
      // bounded and self-correcting imprecision, worth stating plainly
      // rather than quietly living with: when the click's own rate does not
      // match the music's (any proportion other than 100%, or a fixed BPM),
      // a single click landing close to a bar or beat boundary can read one
      // slot early. It never accumulates - the very next click reads the
      // playhead fresh again - so the cost is an occasional single click's
      // accent placed a slot off near a boundary, not a drift that persists.
      const click = resolve(context());
      scheduleClick(nextClickTime, click.accent, click.numerator, click.denominator, click.phase);
      freeRunPhase += 1;
      nextClickTime += secondsPerClick(click.rate);
    }
    // The meter (and therefore, in proportion mode, the rate) can change
    // between one tick() and the next without any of report()'s other
    // triggers firing - a bar boundary crossed mid-playback is not a setting
    // change, a position update carrying a new base tempo, or a fresh load.
    // report()'s own de-duplication makes this a no-op the other ~39 times a
    // second nothing has actually changed.
    report();
  }

  function ensureAudioCtx() {
    if (audioCtx) return audioCtx;
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    } catch (e) {
      // Safari and iOS still enforce a hard cap on live AudioContexts and
      // throw once it is reached. Swallowed here rather than left to
      // propagate: prime() is called from inside a user gesture's own call
      // stack - synchronously before whatever else that gesture does - and an
      // uncaught throw there would stop the rest of it running. In the score
      // viewer that gesture is the Play button, and silencing the metronome
      // is the right failure there; silencing playback is not.
      console.warn("metronome: could not create an AudioContext - the click will stay silent.", e);
      audioCtx = null;
    }
    return audioCtx;
  }

  function start() {
    if (timer) return;
    const ctx = ensureAudioCtx();
    if (!ctx) return;
    nextClickTime = ctx.currentTime + 0.05;
    freeRunPhase = 0;
    timer = setInterval(tick, METRONOME_LOOKAHEAD_MS);
  }

  function stop() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    const ctx = audioCtx;
    if (ctx) {
      const now = ctx.currentTime;
      for (const osc of pendingOscillators.splice(0)) {
        try {
          osc.stop(now);
        } catch {
          // already stopped/ended between the splice and this call
        }
      }
    }
  }

  function sync() {
    if (shouldRun()) start();
    else stop();
  }

  return {
    /**
     * Create and resume the AudioContext now, from inside a user gesture's own
     * call stack. Browser autoplay policy grants audio to that call stack, not
     * to whatever async chain a caller goes through to actually get going -
     * so priming here means the context already exists and is already resumed
     * by the time the scheduler starts for real, and there is nothing left for
     * that later, async start to be denied. Skipped entirely when the click is
     * switched off, so a page whose transport gets used without the metronome
     * ever touched does not hold an AudioContext open for its whole lifetime
     * for nothing.
     */
    prime() {
      if (!enabled) return;
      ensureAudioCtx()?.resume().catch(() => {});
    },
    /**
     * Whether the click is switched on at all.
     *
     * This deliberately does NOT prime the audio context by itself, even
     * though for a metronome with no transport (standalone, or the practice
     * page) switching it on is the very gesture that ought to. A caller with
     * a transport - the score viewer - has a click on Play, not on the
     * metronome toggle, as the gesture audio should be granted to, and
     * creating a context here would leave one open for the whole life of a
     * page whose player never gets used. So a caller with no transport calls
     * prime() itself, right after this, from inside its own handler: sync()
     * below has already created the context by then (start() runs
     * synchronously, inside that same gesture's call stack), and prime() only
     * has to resume it.
     */
    setEnabled(v) {
      enabled = !!v;
      sync();
    },
    /**
     * The transport gate: true only once the thing being counted is genuinely
     * under way. Callers with no transport never touch it (it defaults to
     * true); the score viewer drives it from playback, and holds it false
     * through a count-in so this click stays out of one.
     */
    setRunning(v) {
      running = !!v;
      sync();
    },
    /** "proportion" takes setProportion() of setBaseTempo(), converted onto
     * the live meter's own click unit. "bpm" clicks the typed number itself,
     * in every meter, which is what a physical metronome's dial means. */
    setMode(next) {
      mode = next === "bpm" ? "bpm" : "proportion";
      report();
    },
    setProportion(next) {
      const v = Number(next);
      if (!Number.isFinite(v) || v <= 0) return;
      proportion = v;
      report();
    },
    setBpm(next) {
      const v = Number(next);
      if (!Number.isFinite(v) || v <= 0) return;
      bpm = v;
      report();
    },
    /** How many clicks each notated click is split into - 1 for the plain
     * beat, 2 for eighths against a quarter-note beat, and so on. */
    setSubdivision(next) {
      const v = Math.round(Number(next));
      if (!Number.isInteger(v) || v < 1 || v > MAX_SUBDIVISION) return;
      subdivision = v;
      report();
    },
    /** Whether the first click of each bar is accented at all. Off makes every
     * click identical, which is what a player counting a passage with no
     * settled downbeat asks for. */
    setAccent(v) {
      accentEnabled = !!v;
    },
    /** The meter to click when no pulse source supplies one. */
    setMeter(numerator, denominator) {
      const n = Math.round(Number(numerator));
      const d = Math.round(Number(denominator));
      if (Number.isInteger(n) && n > 0) meterNumerator = n;
      if (Number.isInteger(d) && d > 0) meterDenominator = d;
      report();
    },
    /** The quarter-note tempo a proportion is taken of. */
    setBaseTempo(next) {
      const v = Number(next);
      if (!Number.isFinite(v) || v <= 0) return;
      baseTempo = v;
      if (mode === "proportion") report();
    },
    /** Whether there is yet a real context to report a rate from - see
     * `ready`. Forces the next report through even if the new context's rate
     * happens to match a stale one already announced. */
    setReady(v) {
      hasContext = !!v;
      lastReportedRate = null;
      lastReportedLimit = null;
      report();
    },
    /** Install (or clear, with null) the live playhead this click derives its
     * meter and phase from - see pulseSource above. */
    setPulseSource(fn) {
      pulseSource = typeof fn === "function" ? fn : null;
    },
    /** The click rate right now, clicks per minute - the same number onTempo
     * reports and the scheduler consumes. Null before there is a context to
     * report one from. */
    currentRate() {
      if (!hasContext) return null;
      return resolve(context()).rate;
    },
    /** "slowest" or "fastest" when the countable-range clamp is what is
     * deciding the rate rather than the setting, otherwise null. */
    currentLimit() {
      if (!hasContext) return null;
      return resolve(context()).limit;
    },
    destroy() {
      stop();
      pulseSource = null;
      if (audioCtx) {
        audioCtx.close().catch(() => {});
        audioCtx = null;
      }
    },
  };
}

/**
 * The number to put in a fixed-BPM box so that the click keeps sounding at
 * `rate`, given the subdivision currently in force.
 *
 * `rate` is the click rate - what is heard and what is displayed - while the
 * box holds the rate per NOTATED click, which the engine then multiplies by
 * the subdivision. Seeding the box with the rate itself therefore multiplies
 * the tempo by the subdivision the instant the mode changes: 100% of a piece
 * marked 120 in 4/4 with eighths reports 240, seeding 240 makes the engine
 * compute 480, the clamp holds it at 400 - and the box reads 240 beside a
 * readout of 400, with 400 sounding. Two numbers on the same strip
 * disagreeing is the failure this project keeps having to re-learn not to
 * ship, and it is why this division lives in one named, tested place rather
 * than inline at the call site.
 *
 * Clamped, so the seeded value is one the box can legitimately hold.
 */
export function seedBpmForRate(rate, subdivision = 1) {
  const r = Number(rate);
  const d = Number(subdivision);
  if (!Number.isFinite(r) || r <= 0) return DEFAULT_METRONOME_BPM;
  const divisor = Number.isFinite(d) && d >= 1 ? d : 1;
  return clampBpm(Math.round(r / divisor));
}

/** The percentage ladder for a click taken as a proportion of a piece's own
 * tempo. Deliberately much wider at the bottom than a half-speed floor: for
 * a passage that is genuinely beyond you, half speed is not slow enough, and
 * being able to go much slower is the difference between practising a bar and
 * avoiding it. Above 100% is a real technique too - running a passage faster
 * so that the tempo it is meant to be played at feels easy. */
export const PROPORTION_PRESETS = [15, 25, 35, 50, 60, 70, 80, 90, 100, 110, 125, 150, 175];

/** The same idea for a click with no piece behind it to be a proportion of:
 * absolute rates spanning the range a metronome actually gets set to, from a
 * slow-practice crawl to a fast one. */
export const BPM_PRESETS = [40, 50, 60, 72, 84, 96, 108, 120, 132, 144, 160, 176, 200];

/** Subdivisions offered in the interface, by name. */
export const SUBDIVISION_LABELS = [
  [1, "Beat"],
  [2, "Eighths"],
  [3, "Triplets"],
  [4, "Sixteenths"],
];

/** Time signatures offered as a pre-fill when there is no piece to read one
 * from. Not a limit on what is possible - metronomePattern handles any meter
 * - just the handful worth putting in front of someone rather than making
 * them type. */
export const METER_PRESETS = ["4/4", "3/4", "2/4", "6/8", "9/8", "12/8", "5/4", "7/8"];
