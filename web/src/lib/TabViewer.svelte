<script>
  import { untrack } from "svelte";
  import { api } from "./api.js";
  import { createScoreView } from "./score-render.js";
  import { getSettings, setSetting, STAFF_THEMES, STAFF_THEME_LABELS } from "./settings.svelte.js";

  const settings = getSettings();

  // Changing the theme here just writes the same setting the settings view
  // writes - it's the same store, so the choice still persists and follows
  // the user. What this buys over navigating to #/settings: App.svelte
  // routes with {#if}, so leaving this view for that one and coming back
  // would unmount and remount this component, rebuilding the renderer from
  // scratch - losing scroll position and stopping playback. A theme is
  // exactly the kind of thing you want to change mid-practice (a room going
  // dark, a stage light coming up), so it needs a way to change without
  // navigating away.
  function chooseTheme(ev) {
    setSetting("staff_theme", ev.target.value).catch(() => {
      // setSetting already rolled the optimistic value back (and the select
      // is bound to it), so the control itself already shows the truth
    });
  }

  let {
    score = null,
    demo = false,
    tex = null,
    // Which format `tex` holds. Transcriptions are stored as MusicXML, but a
    // row written before that change - or hand-edited in alphaTex - carries
    // its own format, so this is read from the row rather than assumed.
    format = "alphatex",
    gigMode = false,
    onToggleGig = () => {},
    practiceLabel = null,
    onStopPractice = () => {},
  } = $props();

  let host;
  let scroller;
  let view = $state(null);
  let profile = $state("scoretab");
  let playing = $state(false);
  let playerReady = $state(false);
  let speed = $state(1);
  let looping = $state(false);
  let metronome = $state(false);
  let countIn = $state(false);
  let loadError = $state("");
  let ladder = $state(false);
  let ladderStart = $state(60);
  let ladderStep = $state(5);
  let ladderTarget = $state(100);

  const PROFILE_LABELS = [
    ["score", "Notation"],
    ["tab", "Tab"],
    ["scoretab", "Both"],
  ];

  const SPEEDS = [0.5, 0.75, 1, 1.25];

  const DEMO_TEX = `\\title "Fermata Demo"
\\subtitle "Estudio in E minor"
\\tempo 80
.
:8 0.1 3.2 2.3 0.1 3.2 2.3 0.1 3.2 |
:8 0.2 2.3 2.4 0.2 2.3 2.4 0.2 2.3 |
:8 1.1 0.2 2.3 1.1 0.2 2.3 1.1 0.2 |
:8 0.1 0.2 1.3 0.1 0.2 1.3 0.1 0.2 |
:2 (0.6 2.5 2.4 0.3 0.2 0.1) :2 (0.6 2.5 2.4 0.3 0.2 0.1)`;

  function source() {
    if (demo) return { kind: "alphatex", text: DEMO_TEX };
    if (tex != null) return { kind: format === "musicxml" ? "musicxml" : "alphatex", text: tex };
    if (score) return { kind: "file", url: api.fileUrl(score.id) };
    return null;
  }

  function advanceLadder() {
    if (!ladder) return;
    const next = Math.round(speed * 100) + ladderStep;
    if (next >= ladderTarget) {
      speed = ladderTarget / 100;
      ladder = false;
    } else {
      speed = next / 100;
    }
    view?.setSpeed(speed);
  }

  $effect(() => {
    // read tracked, outside the untrack below: a new tex/score/demo has to
    // rebuild the renderer, which is what makes "Save & render" re-render
    const src = source();
    // a stale error or transport state from a previous load (e.g. a bad
    // edit) must not linger once a new load starts - without this, "Save &
    // render" leaves the old Pause/enabled buttons showing while the new
    // player is still loading its soundfont
    loadError = "";
    playerReady = false;
    playing = false;
    // untrack: everything below is driven imperatively once the view exists;
    // tracking it here would tear down and rebuild the renderer (and stop
    // playback) on a profile switch or a toggle.
    const v = untrack(() =>
      createScoreView(host, {
        scroller,
        source: src,
        profile,
        preset: gigMode ? "stand" : "desk",
        theme: settings.staff_theme,
        transport: { speed, looping, metronome, countIn },
        onReady: () => (playerReady = true),
        onPlaying: (p) => (playing = p),
        onError: (m) => (loadError = m),
        onPassComplete: advanceLadder,
      }),
    );
    view = v;
    return () => v.destroy();
  });

  // Gig mode is the same width read from further away, so it wants a
  // different layout at that width rather than a different width.
  $effect(() => {
    view?.setPreset(gigMode ? "stand" : "desk");
  });

  $effect(() => {
    view?.setTheme(settings.staff_theme);
  });

  function setProfile(p) {
    profile = p;
    view?.setProfile(p);
  }

  function setSpeed(ev) {
    speed = Number(ev.target.value);
    view?.setSpeed(speed);
  }

  function toggleLoop() {
    looping = !looping;
    view?.setLooping(looping);
    // the ladder advances on loop completions, so it cannot run unlooped
    if (!looping) ladder = false;
  }

  function toggleMetronome() {
    metronome = !metronome;
    view?.setMetronome(metronome);
  }

  function toggleCountIn() {
    countIn = !countIn;
    view?.setCountIn(countIn);
  }

  function clamp(n, lo, hi) {
    if (Number.isNaN(n)) return lo;
    return Math.min(hi, Math.max(lo, n));
  }

  function toggleLadder() {
    ladder = !ladder;
    if (!ladder) return;
    // a target at or below the start would step downwards and quit at once
    if (ladderTarget <= ladderStart) ladderTarget = Math.min(200, ladderStart + ladderStep);
    looping = true;
    speed = ladderStart / 100;
    view?.setLooping(true);
    view?.setSpeed(speed);
  }

  function setLadderStart(ev) {
    // 13 is the floor the synth itself enforces; lower values would play
    // faster than the readout claims
    ladderStart = clamp(Number(ev.target.value), 13, 100);
  }

  function setLadderStep(ev) {
    ladderStep = clamp(Number(ev.target.value), 1, 25);
  }

  function setLadderTarget(ev) {
    ladderTarget = clamp(Number(ev.target.value), 10, 200);
  }
</script>

<div class="wrap">
  {#if gigMode}
    <!-- gig mode: hide the practice toolbar chrome, but playback and the way
    back out must stay reachable even with no keyboard (touch/tablet) -->
    <div class="gig-hud">
      <button class="primary" disabled={!playerReady} onclick={() => view?.playPause()}>
        {playing ? "❚❚ Pause" : "▶ Play"}
      </button>
      <button disabled={!playerReady} onclick={() => view?.stop()}>■</button>
      {#if practiceLabel}
        <button class="practice-indicator" onclick={onStopPractice} title="Stop practice timer">
          ● {practiceLabel}
        </button>
      {/if}
      <button onclick={onToggleGig} title="Exit gig mode (Esc)">⤢</button>
    </div>
  {:else}
    <div class="toolbar">
      <div class="seg">
        {#each PROFILE_LABELS as [value, label]}
          <button class:on={profile === value} onclick={() => setProfile(value)}>{label}</button>
        {/each}
      </div>
      <select class="theme-picker" value={settings.staff_theme} onchange={chooseTheme} title="Staff theme">
        {#each STAFF_THEMES as t}
          <option value={t}>{STAFF_THEME_LABELS[t]}</option>
        {/each}
      </select>
      <div class="player">
        <button class="primary" disabled={!playerReady} onclick={() => view?.playPause()}>
          {playing ? "❚❚ Pause" : "▶ Play"}
        </button>
        <button disabled={!playerReady} onclick={() => view?.stop()}>■</button>
        <select value={speed} onchange={setSpeed} title="Playback speed">
          {#if !SPEEDS.includes(speed)}
            <!-- the ladder steps to speeds between the presets -->
            <option value={speed}>{Math.round(speed * 100)}%</option>
          {/if}
          {#each SPEEDS as s}
            <option value={s}>{s}×</option>
          {/each}
        </select>
        <div class="practice">
          <button
            class:on={looping}
            onclick={toggleLoop}
            title="Loop playback — drag across bars on the score to loop a section"
          >
            Loop
          </button>
          <button class:on={metronome} onclick={toggleMetronome} title="Metronome click during playback">
            Metronome
          </button>
          <button class:on={countIn} onclick={toggleCountIn} title="Count-in before playback starts">
            Count-in
          </button>
          <button
            class:on={ladder}
            onclick={toggleLadder}
            title="Tempo ladder — loop a passage and step the speed up automatically"
          >
            Ladder
          </button>
          {#if ladder}
            <div class="ladder-controls">
              <label>
                Start
                <input type="number" min="13" max="100" step="1" value={ladderStart} onchange={setLadderStart} />
              </label>
              <label>
                Step
                <input type="number" min="1" max="25" step="1" value={ladderStep} onchange={setLadderStep} />
              </label>
              <label>
                Target
                <input type="number" min="10" max="200" step="1" value={ladderTarget} onchange={setLadderTarget} />
              </label>
              <span class="ladder-readout">{Math.round(speed * 100)}%</span>
            </div>
          {/if}
        </div>
      </div>
    </div>
  {/if}

  {#if loadError}
    <p class="error">{loadError}</p>
  {/if}

  <div class="score-scroll" bind:this={scroller}>
    <div class="at-host" bind:this={host}></div>
  </div>
</div>

<style>
  .wrap {
    position: relative;
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--line);
    background: var(--bg-raised);
  }

  /* Not part of .player (the transport row) on purpose - a persistent
     preference living among per-session playback toggles would read as one
     of them. It's still reachable without leaving this view, which is the
     whole point (see chooseTheme above). */
  .theme-picker {
    flex-shrink: 0;
  }

  .gig-hud {
    position: absolute;
    z-index: 2;
    bottom: 18px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 10px;
    background: rgba(32, 27, 19, 0.92);
    border: 1px solid var(--line);
    border-radius: 99px;
    padding: 6px 12px;
    backdrop-filter: blur(6px);
  }

  .gig-hud button {
    font-size: 16px;
  }

  .practice-indicator {
    font-size: 13px;
    font-variant-numeric: tabular-nums;
    color: var(--brass-bright);
    white-space: nowrap;
  }

  .seg {
    display: inline-flex;
    border: 1px solid var(--line);
    border-radius: 8px;
    overflow: hidden;
  }

  .seg button {
    border: none;
    border-radius: 0;
    background: none;
    padding: 7px 16px;
  }

  .seg button.on {
    background: var(--brass);
    color: #241d0f;
    font-weight: 600;
  }

  .player {
    display: flex;
    align-items: center;
    gap: 8px;
    /* pushed to the far edge now that .toolbar no longer uses
       justify-content: space-between - the theme picker sits between it and
       .seg instead of splitting the row in two */
    margin-left: auto;
  }

  .practice {
    display: flex;
    align-items: center;
    gap: 6px;
    padding-left: 8px;
    border-left: 1px solid var(--line);
  }

  .practice button.on {
    color: var(--brass-bright);
    border-color: var(--brass);
  }

  .ladder-controls {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 8px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--surface);
  }

  .ladder-controls label {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: var(--ink-dim);
  }

  .ladder-controls input {
    width: 44px;
    padding: 2px 4px;
  }

  .ladder-readout {
    font-size: 12px;
    font-weight: 600;
    color: var(--brass);
  }

  .score-scroll {
    flex: 1;
    /* horizontal layout is one endless system, so the stage has to scroll
       sideways as well - page layout never overflows this way */
    overflow: auto;
    padding: 20px;
  }

  .at-host {
    background: var(--score-surface);
    border-radius: 6px;
    /* a reading measure for page layout. score-render.js overrides it for
       horizontal layout, where the paper has to run the whole length of the
       score rather than stop at a comfortable column width. */
    max-width: 1400px;
    margin: 0 auto;
    padding: 24px;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.55);
  }

  /* score-render.js publishes its chosen theme onto the host as
     data-score-theme - reused here rather than a second, component-local
     record of which theme is active. Parchment is the unmarked default.
     Fully :global() because the attribute is written by that module's
     dataset assignment, not by anything in this component's markup - Svelte
     can't see it and would otherwise prune the rule as unused. */
  :global(.at-host[data-score-theme="noir"]) {
    background: var(--score-noir-surface);
  }

  :global(.at-host[data-score-theme="print"]) {
    background: var(--score-print-surface);
  }

  /* The renderer creates its cursors and selection with position only and no
     colour at all - they are invisible until styled here. */
  .at-host :global(.at-cursor-bar) {
    background: var(--score-accent);
    opacity: 0.1;
  }

  /* width is the renderer's: it writes an inline width with a matching scale
     transform, and overriding one without the other would scale our value
     down to nothing */
  .at-host :global(.at-cursor-beat) {
    background: var(--score-accent);
    opacity: 0.85;
  }

  .at-host :global(.at-selection div) {
    background: var(--score-accent);
    opacity: 0.16;
  }

  :global(.at-host[data-score-theme="noir"] .at-cursor-bar),
  :global(.at-host[data-score-theme="noir"] .at-cursor-beat),
  :global(.at-host[data-score-theme="noir"] .at-selection div) {
    background: var(--score-noir-accent);
  }

  :global(.at-host[data-score-theme="print"] .at-cursor-bar),
  :global(.at-host[data-score-theme="print"] .at-cursor-beat),
  :global(.at-host[data-score-theme="print"] .at-selection div) {
    background: var(--score-print-accent);
  }

  .error {
    color: var(--danger);
    text-align: center;
    margin: 8px;
  }
</style>
