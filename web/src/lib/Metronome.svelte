<script>
  // The metronome's interface, used everywhere the metronome is: the score
  // viewer's transport, the practice page, and on its own. One component, so
  // the click reads and behaves identically wherever it turns up - and so
  // the exercises still to be built (#27, #28, #61) get it by dropping this
  // in and pre-filling three props, with nothing to reshape.
  //
  // The pre-fill is the whole point of the design. Over a piece this arrives
  // already set to that piece's tempo and time signature; on the practice
  // page, to the tempo you last practised at; on its own, to what it was left
  // at. And every one of those is a starting point, never a constraint - a
  // marking on a score is sometimes wrong, sometimes aspirational, and
  // frequently not the speed the passage should be practised at today.
  //
  // Two things this component deliberately does NOT do:
  //
  //   - It does not compute the tempo it displays. The number shown comes
  //     back FROM the audio layer (`tempo`, or the engine's own onTempo),
  //     never from a second calculation here. A readout derived locally would
  //     stay green through a bug in what the audio actually does with it,
  //     which is the failure the whole metronome suite exists to catch.
  //   - It does not know what a score is. `control` is anything
  //     metronome-shaped; the score viewer's is the renderer-facing adapter in
  //     score-render.js, and this file cannot tell the difference.
  import { untrack } from "svelte";
  import { DEFAULT_METRONOME_BPM, MAX_METRONOME_BPM, MIN_METRONOME_BPM } from "./metronome.js";
  import {
    BPM_PRESETS,
    METER_PRESETS,
    PROPORTION_PRESETS,
    SUBDIVISION_LABELS,
    createMetronomeEngine,
    seedBpmForRate,
  } from "./metronome-engine.js";

  let {
    // Whether this component owns the click, or drives one somebody else owns.
    //
    // DECLARED, not inferred from `control` being null. Inferring is the
    // obvious thing and it is wrong: a caller that builds its metronome
    // asynchronously - the score viewer constructs its renderer in an effect -
    // passes null on the first render, so "control is null, therefore make my
    // own" constructs a second engine nobody wanted. That was inert only
    // because the viewer's toggle happens to drive the score engine, so the
    // stray one was never enabled; a caller mounting with `enabled` already
    // true beside a late `control` would have got two engines and two clicks.
    // The Svelte compiler flagged exactly this as state_referenced_locally,
    // and the build now fails on that warning - see vite.config.js.
    ownsClick = true,
    // A metronome-shaped object to drive - `{setEnabled, setMode,
    // setProportion, setBpm, ...}`. Only meaningful when ownsClick is false.
    // It may arrive late (see above) or be REPLACED (a new view per score),
    // both of which the effect below handles by re-pushing every setting at
    // whatever object is current.
    control = null,
    // The live click rate, clicks per minute, as reported by `control`. Only
    // read when a `control` was supplied - an engine made here reports its own.
    tempo = null,
    // "slowest"/"fastest" when the countable-range clamp, rather than the
    // setting above it, is what decided that rate. Same rule: only read when a
    // `control` was supplied.
    limit = null,
    // Bound, so a parent that needs to know (the viewer's gig HUD shows the
    // readout with the controls left behind) can read it without this
    // component having to tell it twice.
    enabled = $bindable(false),
    // True when there is a piece tempo to take a percentage OF. Without one,
    // a percentage means nothing, so the mode choice is not offered at all
    // and the click is a plain BPM - which is all a metronome standing on its
    // own ever was.
    proportionBase = false,
    // Where that piece tempo came from, which decides what the control is
    // allowed to CALL it. A tempo we did not read must not look like one we
    // did, and there are three genuinely different cases:
    //
    //   "marked"      - printed on the page and read off it.
    //   "transcribed" - lifted out of a transcription of a scanned page.
    //   "default"     - the score declares no tempo at all, so the number
    //                   beside the percentages is the renderer's fallback and
    //                   nothing about it was read anywhere. This one is the
    //                   defect issue #102 was about: it used to read
    //                   "marked ♩ = 120" for an edition that prints
    //                   *Andante* and no number, which is most of the
    //                   classical material in this library.
    //
    // Only "marked" is presented as a marking; the other two carry the same
    // unobtrusive unverified mark the rest of the app uses.
    tempoSource = "marked",
    // The piece's own tempo, for the "100% of what?" the percentages need
    // to be legible. Null when unknown.
    baseTempoLabel = null,
    // Pre-fills. null means "not known", which is not the same as a default:
    // a caller whose context arrives over the network (the practice page
    // learns the tempo last practised at from the server, after this has
    // mounted) passes null first and the real number later, and the effect
    // below adopts a late arrival - but never over a choice already made by
    // hand. See `touched`.
    initialBpm = null,
    initialMeter = "4/4",
    // A localStorage key to remember these settings under, or null to forget
    // them when the component goes away. See the note on persistence below.
    remember = null,
    // Compact drops the meter/subdivision/accent row - the score viewer's
    // toolbar is already crowded, and over a piece the meter comes from the
    // score anyway, so there is nothing there for a player to set.
    compact = false,
    // Prominent is the phone-at-a-music-stand layout: the tempo as a big
    // tabular number and the start/stop and one-bpm controls sized for a
    // thumb, with the settings you touch once demoted underneath. It is the
    // right shape wherever the metronome IS the thing on screen rather than
    // one control in a transport.
    prominent = false,
    // The three settings a compact caller exposes. Bindable, and null-by-
    // default rather than pre-filled here, so that "the parent did not supply
    // one" and "the parent supplied this one" stay distinguishable - see the
    // pre-fill chain below.
    mode = $bindable(null),
    proportion = $bindable(null),
    bpm = $bindable(null),
  } = $props();

  // Persistence, decided per site rather than globally, because "what should
  // still be here tomorrow" genuinely differs:
  //
  //   - Over a piece: NOTHING persists. Every other transport control in the
  //     viewer (speed, looping, count-in) is per-session state that resets
  //     with a fresh load, and a click's tempo is exactly as tied to "the
  //     passage being worked on right now" as those are. A proportion left
  //     over from yesterday's slow passage is not a default today's fast one
  //     wants, and it would be a silent one - you would not know it had been
  //     applied until you heard it.
  //   - Standing on its own: EVERYTHING persists, because there is no other
  //     context to pre-fill from. Its last setting IS its context.
  //
  // localStorage rather than the server's /api/settings, which is where a
  // real preference belongs and would follow a person between devices: the
  // settings endpoint takes a fixed allowlist of keys, and widening it is a
  // server change this work is not permitted to make. So this is per-device,
  // and a phone at a music stand will not inherit the tempo left on a
  // desktop. Worth revisiting when the endpoint can carry it.
  function loadRemembered() {
    if (!remember) return null;
    try {
      const raw = localStorage.getItem(remember);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null; // unavailable (private browsing) or not valid JSON
    }
  }

  const stored = loadRemembered();

  function persist() {
    if (!remember) return;
    try {
      // DEVICE-LOCAL, and that is a recorded compromise rather than the right
      // answer. A real preference belongs on the server, at
      // /api/settings, where the staff theme already lives - that is what
      // follows a person from phone to tablet to desktop, and getting all of
      // a person's data in and out is an explicit goal of this project. That
      // endpoint takes a fixed allowlist of keys, and widening it is a server
      // change the work that introduced this was not permitted to make. So
      // until the endpoint can carry it, this one preference stays in browser
      // storage: a tempo left on a desktop is not inherited by a phone at a
      // music stand. Move it to setSetting() when the allowlist can hold it,
      // and delete this comment with it.
      localStorage.setItem(remember, JSON.stringify({ mode, proportion, bpm, subdivision, accent, meter }));
    } catch {
      // storage unavailable - the settings just will not be remembered
    }
  }

  // Anything the parent left null below is filled in from the pre-fill chain:
  // the remembered setting, then the caller's own initial value, then the
  // plain default. Assigning through a BOUND prop here is what lets a parent
  // that has to outlive this component keep these settings across an unmount -
  // TabViewer's toolbar is torn down entering gig mode and rebuilt on the way
  // out, and "set the tempo up, put it on a stand, come back" is exactly the
  // sequence a player performs, so finding it reset to 100% on return would be
  // a real loss and a silent one. A parent that does not bind them (the
  // standalone and practice pages) lets this component own them outright.
  // untrack: these three deliberately read the value as it is RIGHT NOW, once,
  // to seed state that the player then owns. Said explicitly because the
  // compiler is right to ask - reading a prop once is usually a bug (see
  // ownsClick above for the one where it was), and a build that fails on the
  // warning needs the deliberate cases to declare themselves.
  if (mode == null) mode = stored?.mode ?? (untrack(() => proportionBase) ? "proportion" : "bpm");
  // A percentage, not a 0-1 ratio, because that is how a player would say it
  // out loud - "seventy percent" - and the control below reads that way too.
  if (proportion == null) proportion = stored?.proportion ?? 100;
  if (bpm == null) bpm = stored?.bpm ?? untrack(() => initialBpm) ?? DEFAULT_METRONOME_BPM;

  // Not bindable: these three are only ever shown when `compact` is false, and
  // the only caller that unmounts this component mid-use is the compact one.
  let subdivision = $state(stored?.subdivision ?? 1);
  let accent = $state(stored?.accent ?? true);
  let meter = $state(stored?.meter ?? untrack(() => initialMeter));

  // An engine of this component's own, for a caller with no transport to
  // drive one. Created eagerly rather than on first use: it holds no audio
  // resources until the click is actually switched on (see prime() and
  // ensureAudioCtx in metronome-engine.js), so there is nothing to defer.
  //
  // `ownsClick` is read ONCE, here, and must therefore be true or false on the
  // very first render. That is the whole reason it exists as its own flag
  // instead of being inferred from whether `control` happens to be present: a
  // caller that builds its transport in an effect passes null on mount and the
  // real object a tick later, so inferring it built a second engine nobody
  // asked for. Do not derive `ownsClick` from anything that arrives late, and
  // do not replace it with a `control` check.
  //
  // Worth knowing why that earlier second engine was harmless, since it is not
  // a reason to relax this: toggling drives `target`, so the stray engine's own
  // `enabled` never left false, `prime()` returned early, and no audio context
  // was ever opened. A call site mounting with the click ALREADY on, alongside a
  // late transport, would have had two engines and two clicks. The build's
  // compiler gate catches the shape that produced it, but only that shape -
  // see the note in vite.config.js for what it does not catch.
  let ownTempo = $state(null);
  let ownLimit = $state(null);
  const ownEngine = untrack(() => ownsClick)
    ? createMetronomeEngine({
        onTempo: (rate, atLimit) => {
          ownTempo = rate;
          ownLimit = atLimit;
        },
      })
    : null;
  const target = $derived(ownEngine ?? control);
  const liveTempo = $derived(ownEngine ? ownTempo : tempo);
  const liveLimit = $derived(ownEngine ? ownLimit : limit);

  // True once anything here has been set by hand. A pre-fill is a starting
  // point, so one that arrives late still has to land - but it must never
  // land on top of a choice somebody already made. Without this, logging a
  // practice session (which reloads the sessions this page pre-fills from)
  // would silently throw away the tempo you had just dialled in, which is the
  // same class of fault as a setting lost to an unmount.
  let touched = $state(false);

  function meterParts(text) {
    const m = /^\s*(\d+)\s*\/\s*(\d+)\s*$/.exec(String(text ?? ""));
    if (!m) return null;
    return { numerator: Number(m[1]), denominator: Number(m[2]) };
  }

  // Pushes every setting at `c`, in the order the engine needs them: enabled
  // LAST, because setEnabled is the call that can start the click running and
  // it must not do that against stale values for the instant before the rest
  // land.
  function applyAll(c) {
    if (!c) return;
    c.setMode(mode);
    c.setProportion(proportion / 100);
    c.setBpm(bpm);
    c.setSubdivision?.(subdivision);
    c.setAccent?.(accent);
    const parts = meterParts(meter);
    if (parts && !proportionBase) c.setMeter?.(parts.numerator, parts.denominator);
    c.setEnabled(enabled);
  }

  // Depends on `control`'s identity ALONE. The settings are read through
  // untrack so this does not re-run - and re-push everything - on every
  // keystroke in the tempo box; the handlers below already tell the control
  // about their own change directly. What this is for is the two cases a
  // handler cannot cover: a control that did not exist yet when this mounted,
  // and a control REPLACED by a fresh one (the viewer builds a new renderer
  // per score), which would otherwise silently start at the engine's own
  // defaults while the interface went on showing these values.
  $effect(() => {
    const c = control;
    if (ownEngine || !c) return;
    untrack(() => applyAll(c));
  });

  if (ownEngine) applyAll(ownEngine);

  // A pre-fill that arrives after mount. Tracks `initialBpm` only - the
  // settings are read and written through untrack, so this cannot re-run on
  // its own writes - and gives up the moment anything has been set by hand.
  $effect(() => {
    const next = initialBpm;
    if (next == null) return;
    untrack(() => {
      if (touched || bpm === next) return;
      bpm = next;
      target?.setBpm(next);
    });
  });

  $effect(() => () => ownEngine?.destroy());

  function toggle() {
    enabled = !enabled;
    target?.setEnabled(enabled);
    // Priming from inside this handler is what grants a click audio at all
    // when there is no transport whose own Play button could have done it:
    // browser autoplay policy grants audio to a user gesture's own call
    // stack, and this is that stack. setEnabled above has already created the
    // context synchronously, so there is only a resume left to do.
    //
    // Only for an engine of this component's own (ownsClick), which is exactly
    // the no-transport case. A supplied `control` belongs to a caller that has
    // its own gesture to prime from - the score viewer's Play button - and
    // creating an AudioContext here instead would open one the moment the
    // toggle is touched, on a page whose player may never be used at all.
    // "Switching the click on creates no audio machinery by itself" is a
    // property that suite asserts on directly.
    if (enabled) ownEngine?.prime();
  }

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  function chooseMode(next) {
    // Coming INTO fixed BPM, seed the number from what is actually being
    // heard right now - the live rate reported back by the audio layer, in
    // the meter's own click unit - and not from `proportion` times some
    // score tempo guessed at here. Switching modes should not change the
    // tempo out from under a player mid-passage.
    //
    // Divided by the subdivision, because the box holds the rate per NOTATED
    // click and the engine multiplies it back up. See seedBpmForRate for the
    // case that gets this wrong and the two disagreeing numbers it puts on
    // screen.
    if (next === "bpm" && liveTempo != null) {
      bpm = seedBpmForRate(liveTempo, subdivision);
    }
    touched = true;
    mode = next;
    target?.setMode(mode);
    if (mode === "bpm") target?.setBpm(bpm);
    else target?.setProportion(proportion / 100);
    persist();
  }

  function setProportion(next) {
    // Widened well past the old half-speed floor. For a passage that is
    // genuinely beyond you, half speed is not slow enough, and being able to
    // go much slower is the difference between practising a bar and avoiding
    // it. The ceiling is a real technique too - running a passage faster so
    // the tempo it is meant to be played at feels easy.
    // Validated BEFORE clamping, not after. `Number("")` is 0, not NaN - so a
    // clamp-then-check would turn the preset select's own "between the
    // presets" placeholder option, whose value is the empty string, into a
    // jump to the bottom of the ladder the moment it was selected.
    const n = Number(String(next).trim());
    if (String(next).trim() === "" || !Number.isFinite(n)) return;
    const clamped = clamp(Math.round(n), PROPORTION_PRESETS[0], PROPORTION_PRESETS.at(-1));
    touched = true;
    proportion = clamped;
    target?.setProportion(clamped / 100);
    persist();
  }

  function setBpm(next) {
    // See setProportion above for why this is checked before the clamp.
    const n = Number(String(next).trim());
    if (String(next).trim() === "" || !Number.isFinite(n)) return;
    const clamped = clamp(Math.round(n), MIN_METRONOME_BPM, MAX_METRONOME_BPM);
    touched = true;
    bpm = clamped;
    target?.setBpm(clamped);
    persist();
  }

  /**
   * Fine adjustment: one beat per minute, the granularity a tempo actually
   * gets raised at over weeks, and the one that makes a goal like "this
   * section from 92 to 100" mean anything.
   *
   * A single beat per minute is inherently an ABSOLUTE quantity, so nudging
   * one takes the tempo over as a fixed number: in proportion mode this
   * switches to fixed BPM first, seeded from the live rate - exactly what
   * choosing "Fixed BPM" by hand already does - and then applies the step.
   * The alternative, stepping the percentage by one point, would move the
   * real rate by however many BPM one percent of the piece happens to be,
   * which is not what "one BPM" means and cannot hit 100 on purpose. The two
   * cannot both be held at once, and saying so plainly in the button's own
   * title beats a control that quietly does the other thing.
   */
  function nudge(step) {
    if (mode !== "bpm") chooseMode("bpm");
    setBpm(bpm + step);
  }

  function chooseSubdivision(next) {
    touched = true;
    subdivision = Number(next);
    target?.setSubdivision?.(subdivision);
    persist();
  }

  function toggleAccent() {
    touched = true;
    accent = !accent;
    target?.setAccent?.(accent);
    persist();
  }

  function chooseMeter(next) {
    touched = true;
    meter = next;
    const parts = meterParts(next);
    if (parts) target?.setMeter?.(parts.numerator, parts.denominator);
    persist();
  }

  // What the percentages are percentages OF, spelled out. "70%" on its own is
  // not a tempo, and a player choosing one is entitled to see the number it
  // is being taken from.
  //
  // The word in front of it is the honesty rule, and it is the reason this is
  // shown at all: a tempo lifted out of a transcription was INFERRED from a
  // scanned page, and must not read like one printed on it. So it says
  // "transcribed" rather than "marked", and carries the same unobtrusive mark
  // the rest of the app uses for something unverified. Only in proportion
  // mode - a fixed BPM is a number the player typed, and there is nothing
  // inferred about it to disclose.
  //
  // The third case is a score that declares NO tempo, where the number is the
  // renderer's own 120 fallback. It gets the same treatment and one more
  // thing: it says outright that there was none to read, because "default
  // ♩ = 120" on its own still leaves a reader to work out whether 120 came
  // from somewhere. Nothing here is a percentage of a marking in that case,
  // and the control says so instead of quietly implying otherwise.
  // Said plainly, in visible text, whenever the countable-range clamp rather
  // than the chosen setting is what decided the rate on screen. Without this,
  // a control reading "15%" beside a readout of "20" is a percentage that has
  // quietly stopped being a percentage - and the reader has no way to tell a
  // floor from a bug. Not a title attribute: a phone at a music stand has no
  // pointer to hover with, and this is exactly the situation where the reader
  // is holding an instrument rather than a mouse.
  const limitNote = $derived(
    liveLimit === "slowest"
      ? "at its slowest"
      : liveLimit === "fastest"
        ? "at its fastest"
        : null,
  );
  const limitWhy = $derived(
    liveLimit === "slowest"
      ? `A slower click than ${MIN_METRONOME_BPM} a minute stops being a metronome and starts being a wait, so this is as slow as it goes - the setting above asks for less than the click can sound.`
      : liveLimit === "fastest"
        ? `A faster click than ${MAX_METRONOME_BPM} a minute is not a tempo practice happens at, and one click's tail would run into the next one's attack, so this is as fast as it goes - the setting above asks for more than the click can sound.`
        : null,
  );

  // "marked" is the only word here that claims something was read off a page,
  // so it is the only one that may be said when it was.
  const TEMPO_WORD = { marked: "marked", transcribed: "transcribed", default: "default" };
  const tempoUnverified = $derived(tempoSource !== "marked");
  const baseNote = $derived(
    proportionBase && mode === "proportion" && baseTempoLabel != null
      ? `${TEMPO_WORD[tempoSource] ?? "default"} ♩ = ${baseTempoLabel}` +
          (tempoSource === "default" ? " (none in the score)" : "")
      : null,
  );
  const baseMarkLabel = $derived(
    tempoSource === "default"
      ? `Unverified: this score declares no tempo, so ${baseTempoLabel} is a default rather than a marking`
      : `Unverified: tempo ${baseTempoLabel} came from a transcription, not a printed marking`,
  );
</script>

<div class="metronome" class:compact class:prominent>
  {#if prominent}
    <!-- The one number that matters, at a size that survives being read from
    a stand at arm's length, and present whether or not the click is running -
    "what is this set to" is a question you ask before starting, not only
    while it clicks. Falls back to the value the settings imply when the
    audio layer has not reported one yet (it only reports while there is
    something to report); still never computed here twice - currentRate() is
    the engine's own answer, the same one it schedules. -->
    <div class="metronome-big" aria-live="off">
      <span class="metronome-readout-large" title="clicks per minute">{liveTempo ?? target?.currentRate?.() ?? bpm}</span>
      <span class="metronome-unit">bpm</span>
    </div>
    {#if limitNote}
      <span class="metronome-limit" title={limitWhy}>{limitNote}</span>
    {/if}
  {/if}
  <button class:on={enabled} class:primary={prominent && !enabled} onclick={toggle} title="Metronome click">
    {#if prominent}
      {enabled ? "■ Stop" : "▶ Start"}
    {:else}
      Metronome
      {#if enabled && liveTempo != null}
        <span class="metronome-readout" title="clicks per minute">{liveTempo}</span>
      {/if}
    {/if}
  </button>
  <!-- In the viewer's toolbar the controls appear only once the click is on,
  because until then they are clutter beside six other transport buttons. On a
  page where the metronome IS the content they are always there: "set the
  tempo, then start" is the normal order, and hiding the tempo behind starting
  it would mean every adjustment begins with an unwanted click. -->
  {#if enabled || prominent}
    <div class="metronome-controls">
      {#if proportionBase}
        <select
          class="metronome-mode"
          value={mode}
          onchange={(ev) => chooseMode(ev.target.value)}
          title="What the metronome counts - the score's own tempo, or a number set directly"
        >
          <option value="proportion">% of score tempo</option>
          <option value="bpm">Fixed BPM</option>
        </select>
      {/if}
      {#if mode === "proportion"}
        <!-- A native select for the ladder: keyboard-reachable for free
        (arrow keys step it, which is also what #92's shortcut scheme will
        want to reach), and a full-height native picker on a phone rather
        than a dozen small targets crammed into a toolbar. -->
        <select
          class="metronome-presets"
          value={PROPORTION_PRESETS.includes(proportion) ? proportion : ""}
          onchange={(ev) => setProportion(ev.target.value)}
          title="Tempo, as a proportion of the piece's own"
        >
          {#if !PROPORTION_PRESETS.includes(proportion)}
            <!-- typed, or stepped to, a value between the presets -->
            <option value="">{proportion}%</option>
          {/if}
          {#each PROPORTION_PRESETS as p}
            <option value={p}>{p}%</option>
          {/each}
        </select>
        <label>
          <input
            class="metronome-proportion"
            type="number"
            inputmode="numeric"
            min={PROPORTION_PRESETS[0]}
            max={PROPORTION_PRESETS.at(-1)}
            step="5"
            value={proportion}
            onchange={(ev) => {
              setProportion(ev.target.value);
              // Snapped back to what was actually accepted, so the box can
              // never sit showing a number the click is not using.
              ev.target.value = String(proportion);
            }}
          />
          %
        </label>
      {:else}
        <select
          class="metronome-presets"
          value={BPM_PRESETS.includes(bpm) ? bpm : ""}
          onchange={(ev) => setBpm(ev.target.value)}
          title="Tempo"
        >
          {#if !BPM_PRESETS.includes(bpm)}
            <option value="">{bpm}</option>
          {/if}
          {#each BPM_PRESETS as p}
            <option value={p}>{p}</option>
          {/each}
        </select>
        <label>
          <input
            class="metronome-bpm"
            type="number"
            inputmode="numeric"
            min={MIN_METRONOME_BPM}
            max={MAX_METRONOME_BPM}
            step="1"
            value={bpm}
            onchange={(ev) => {
              setBpm(ev.target.value);
              ev.target.value = String(bpm);
            }}
          />
          bpm
        </label>
      {/if}
      <span class="metronome-fine">
        <button
          class="metronome-slower"
          onclick={() => nudge(-1)}
          title={mode === "bpm" ? "One bpm slower" : "One bpm slower (takes the tempo over as a fixed number)"}
          aria-label="One beat per minute slower"
        >
          −
        </button>
        <button
          class="metronome-faster"
          onclick={() => nudge(1)}
          title={mode === "bpm" ? "One bpm faster" : "One bpm faster (takes the tempo over as a fixed number)"}
          aria-label="One beat per minute faster"
        >
          +
        </button>
      </span>
      {#if limitNote && !prominent}
        <!-- The prominent layout puts this directly under the big number
        instead - see above. Exactly one of the two renders, so there is one
        place a reader looks for it and one element a test can assert on. -->
        <span class="metronome-limit" title={limitWhy}>{limitNote}</span>
      {/if}
      {#if baseNote}
        <span
          class="metronome-base"
          class:inferred={tempoUnverified}
          class:undeclared={tempoSource === "default"}
          title={`The tempo the percentages are of - ${baseNote}`}
        >
          {#if tempoUnverified}
            <!-- The same unobtrusive mark the rest of the app uses for
            something unverified: a tempo we inferred from a transcription -
            or never had at all - must not read as one printed on a page. -->
            <span class="mark" aria-label={baseMarkLabel}>●</span>
          {/if}
          {baseNote}
        </span>
      {/if}
      {#if !compact}
        <select
          class="metronome-meter"
          value={meter}
          onchange={(ev) => chooseMeter(ev.target.value)}
          title="Time signature - which click is accented, and how a bar is subdivided"
        >
          {#if !METER_PRESETS.includes(meter)}
            <option value={meter}>{meter}</option>
          {/if}
          {#each METER_PRESETS as m}
            <option value={m}>{m}</option>
          {/each}
        </select>
        <select
          class="metronome-subdivision"
          value={subdivision}
          onchange={(ev) => chooseSubdivision(ev.target.value)}
          title="How finely each beat is clicked"
        >
          {#each SUBDIVISION_LABELS as [value, label]}
            <option {value}>{label}</option>
          {/each}
        </select>
        <button
          class="metronome-accent"
          class:on={accent}
          onclick={toggleAccent}
          title="Accent the first click of each bar"
          aria-pressed={accent}
        >
          Accent
        </button>
      {/if}
    </div>
  {/if}
</div>

<style>
  /* display:contents so this component adds no box of its own: in the score
     viewer's toolbar the toggle and the controls strip have to remain
     siblings of the Loop/Count-in/Ladder buttons in the same flex row, which
     is what they were before this moved into a component. Base button,
     select and input styling is global (app.css), so only what was
     component-scoped before needs restating here. */
  .metronome {
    display: contents;
  }

  /* The phone-at-a-stand layout: a column, so the big number and the two
     controls a thumb actually uses stack instead of competing for a row. */
  .metronome.prominent {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 14px;
  }

  .metronome-big {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }

  .metronome-readout-large {
    font-size: 64px;
    line-height: 1;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--brass-bright);
  }

  .metronome-unit {
    font-size: 14px;
    color: var(--ink-dim);
  }

  .metronome.prominent > button {
    min-width: 140px;
    min-height: 48px;
    font-size: 16px;
  }

  .metronome.prominent .metronome-controls {
    justify-content: center;
  }

  /* Bigger again where the metronome is the whole screen - there is room, and
     this is the control that gets used while holding an instrument. */
  .metronome.prominent .metronome-fine button {
    min-width: 56px;
    min-height: 56px;
    font-size: 22px;
  }

  button.on {
    color: var(--brass-bright);
    border-color: var(--brass);
  }

  /* The live click tempo, inline in the button that turns it on - "the
     current value visible" has to sit where a glance at the toggle already
     goes, not in a second place a player has to know to look. */
  .metronome-readout {
    margin-left: 4px;
    font-variant-numeric: tabular-nums;
    opacity: 0.85;
  }

  /* Same shape as the viewer's own .ladder-controls (a small bordered strip
     of compact inputs) - the two are siblings in the same button row and
     should read as the same kind of thing, not two different idioms for "the
     button next to it has settings". Wraps, because the full control set is
     wider than a phone. */
  .metronome-controls {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 4px 8px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--surface);
  }

  .metronome-controls select {
    font-size: 12px;
  }

  .metronome-controls label {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: var(--ink-dim);
  }

  .metronome-controls input {
    /* three digits, since a fixed bpm reasonably runs to 208 and beyond */
    width: 52px;
    padding: 2px 4px;
  }

  /* The two largest targets here, on purpose. A single beat per minute is
     what gets nudged mid-passage while standing at a stand, holding an
     instrument, not looking down - so these get a 40px minimum a thumb can
     hit reliably, where everything else in this strip is a 12px control you
     set once and leave. */
  .metronome-fine {
    display: inline-flex;
    gap: 2px;
  }

  .metronome-fine button {
    min-width: 40px;
    min-height: 40px;
    padding: 0;
    font-size: 16px;
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }

  /* Stated, not warned about - no --danger, no bold, no icon. Nothing has
     gone wrong when the click reaches the end of its range; a fact about the
     tempo is being reported, in the same register as the tempo itself. It sits
     directly beside (or, in the prominent layout, directly under) the number
     it explains, because a reason placed anywhere else is a reason nobody
     connects to the thing it is about. */
  .metronome-limit {
    font-size: 11px;
    color: var(--ink-dim);
    white-space: nowrap;
    font-style: italic;
  }

  .metronome.prominent .metronome-limit {
    font-size: 13px;
    margin-top: -8px;
  }

  .metronome-base {
    font-size: 11px;
    color: var(--ink-dim);
    white-space: nowrap;
  }

  /* The undeclared-tempo form is a short sentence rather than a three-token
     label, and the score viewer's toolbar is a single non-wrapping flex row
     with six other controls in it. Held on one line it is the widest thing in
     the metronome block and pushes the row wide enough to clip the profile
     buttons at the far end; allowed to wrap it costs one line of height and
     nothing else. Only this state - the shorter forms have no reason to
     break. */
  .metronome-base.undeclared {
    white-space: normal;
    max-width: 15ch;
  }

  /* No colour, no weight change, no word like "unverified" in the visible
     text: an inferred tempo is flagged, not warned about. The mark plus
     "transcribed" instead of "marked" is the whole signal - the same
     unobtrusive convention ScoreCompare's .gig-mark uses. */
  .metronome-base .mark {
    font-size: 9px;
    vertical-align: 1px;
    margin-right: 3px;
  }
</style>
