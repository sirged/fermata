<script>
  import { untrack } from "svelte";
  import { api } from "./api.js";
  import { createScoreView, UNRENDERABLE_MESSAGE } from "./score-render.js";
  import { getSettings, setSetting, STAFF_THEMES, STAFF_THEME_LABELS } from "./settings.svelte.js";
  import Metronome from "./Metronome.svelte";

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
    // Whether THIS instance's single-key shortcuts (#92) should respond to
    // the keyboard at all. Defaults true, which is right everywhere this
    // component is mounted on its own (Viewer.svelte's plain notation/tab
    // path). ScoreCompare mounts a TabViewer and a PdfViewer at once and
    // only hides the one not on screen with CSS, not by unmounting it - see
    // ScoreCompare's own snippets - and PdfViewer already owns Space and the
    // plain arrow keys for turning pages. Left both listening unconditionally,
    // a single Space press in side-by-side or PDF-only layout would BOTH
    // toggle playback here and turn the page there - exactly the "does the
    // shortcut set stay sane" question #92 asks about gig mode, where a
    // pedal sends nothing but arrow keys. ScoreCompare passes this only
    // `true` while its staff pane is the one actually on screen (never in
    // "side" layout, where two panes are visible at once and no single key
    // press can unambiguously mean one of them - see the comment on
    // `activeLayout` there).
    active = true,
  } = $props();

  let host;
  let scroller;
  let view = $state(null);
  let profile = $state("scoretab");
  // Which profile buttons are worth showing at all - null while unknown (no
  // score has finished loading yet), otherwise whatever score-render.js
  // found this score can actually be drawn with - possibly empty. null is
  // deliberately not "everything": a `score` prop loads over an async fetch,
  // so there is a real window between mount and the load actually landing
  // during which every button being live would let a click on "Tab" go
  // through for a notation-only file before its content is even known - and
  // then get silently walked back (the button vanishing out from under the
  // pointer) the moment the fetch resolves and corrects it. Starting at null
  // and rendering no buttons at all during that window is what avoids
  // offering a choice this component cannot yet vouch for.
  let profileOptions = $state(null);
  let playing = $state(false);
  let playerReady = $state(false);
  let speed = $state(1);
  let looping = $state(false);
  // Whether the click is on. Owned here rather than inside Metronome.svelte
  // because gig mode strips the toolbar - and its metronome controls - while
  // still having to show the readout, so this component needs the answer too.
  // Bound to the component below; nothing else here sets it.
  //
  // None of the metronome's settings are server-persisted, deliberately, and
  // none of them are kept here either: they live in Metronome.svelte, which
  // re-pushes them whenever `view` is replaced. Every other transport control
  // here (speed, looping, count-in) is per-session state that resets with a
  // fresh load, and a click's tempo is exactly as tied to "the passage being
  // worked on right now" as those are.
  let metronome = $state(false);
  // The value the audio layer reports it is actually clicking at right now -
  // null until a score has loaded and it has one to report. This is read
  // back FROM the audio layer (see onMetronomeTempo below), not computed
  // here a second time: showing a value this component derived itself would
  // stay green through a bug in what the audio layer actually does with it.
  let metronomeTempo = $state(null);
  // "slowest"/"fastest" when the click's countable-range clamp - rather than
  // the chosen percentage - is what decided that rate, otherwise null. Passed
  // down so the control can say why the two have stopped agreeing.
  let metronomeLimit = $state(null);
  // The metronome's three visible settings, owned here rather than inside
  // Metronome.svelte for one reason: gig mode unmounts the whole toolbar, and
  // a setting that lived in the component would silently revert to its default
  // on the way back out. null means "not chosen yet" - the component fills each
  // one in from its own pre-fill chain and hands it back. Still not persisted
  // anywhere: see the note on `metronome` above.
  let metronomeMode = $state(null);
  let metronomeProportion = $state(null);
  let metronomeBpm = $state(null);
  // The tempo the percentages are percentages OF - see Metronome.svelte's
  // baseTempoLabel - and where it came from: "start", "later" or "none" (see
  // tempoProvenance in score-render.js). Two values, because `score.tempo`
  // alone cannot tell a declared tempo from the renderer's 120 fallback, which
  // is how a score printing no tempo came to be described as "marked ♩ = 120"
  // (issue #102). "none" until a score says otherwise: the number is
  // meaningless before then, and "not yet loaded" must not read as "declared".
  let scoreTempo = $state(null);
  let scoreTempoFrom = $state("none");
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
    // a new score has its own tempo - the old readout would otherwise show
    // the previous score's number until this one's first report arrives
    metronomeTempo = null;
    metronomeLimit = null;
    scoreTempo = null;
    scoreTempoFrom = "none";
    // unknown again for this score, not "same as the last one" - see the
    // comment on profileOptions's declaration
    profileOptions = null;
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
        transport: { speed, looping, countIn },
        onReady: () => (playerReady = true),
        onPlaying: (p) => (playing = p),
        onError: (m) => (loadError = m),
        onPassComplete: advanceLadder,
        onMetronomeTempo: (bpm, limit) => {
          metronomeTempo = bpm;
          metronomeLimit = limit;
        },
        onScoreTempo: (t, from) => {
          scoreTempo = t;
          scoreTempoFrom = from;
        },
        // Only which buttons to offer, not which one is highlighted - see
        // onProfileApplied for that. A score with nothing drawable reports an
        // empty array here, not a fallback list; the empty-state notice in
        // the markup below is what tells the profileOptions.length === 0
        // case apart from "still loading" (profileOptions still null).
        onProfiles: (profiles) => {
          profileOptions = profiles;
        },
        // The highlighted button has to follow what actually finished
        // drawing, not what was most recently requested - setProfile() below
        // deliberately does not set `profile` itself, because a requested
        // profile that fails to render (or hasn't rendered yet) must not
        // move the highlight onto a staff that isn't the one on screen.
        // loadError is cleared here too: a render succeeding means whatever
        // problem it described is resolved, and it must not keep showing
        // (or keep the .error paragraph occupying space) once the view has
        // recovered - score-render.js's own setProfile() guard would
        // otherwise never get a chance to retry a *different* profile
        // switch while a stale error from an earlier one sat on screen.
        onProfileApplied: (p) => {
          profile = p;
          loadError = "";
        },
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
    // the buttons only ever offer a viable profile, but guard anyway rather
    // than trust that
    if (!profileOptions?.includes(p)) return;
    // `profile` (the highlighted button) is deliberately not set here. It is
    // set from onProfileApplied, above, once a render with this profile has
    // actually finished - setting it eagerly would move the highlight onto a
    // staff that is not yet, or never, actually on screen: a render that
    // fails for a reason other than an unsupported profile (still possible -
    // see onError) would otherwise leave the highlight on a profile whose
    // render just threw, with the previous, different render frozen
    // underneath it. That mismatch was the original bug's second half.
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

  // ---------------------------------------------------------- #92: shortcuts
  //
  // "S" changes playback speed by stepping through the same presets the
  // select already offers, wrapping past either end - a single key standing
  // in for opening Songsterr's speed panel, since this toolbar has no panel
  // to open, only a picker. Deliberately over SPEEDS only, not the ladder's
  // in-between values: a mid-ladder speed is something the ladder is
  // actively managing, and a key nudging it to the nearest preset would
  // fight the ladder rather than complement it.
  function cycleSpeed() {
    const at = SPEEDS.indexOf(speed);
    speed = SPEEDS[(at + 1 + SPEEDS.length) % SPEEDS.length] ?? SPEEDS[0];
    view?.setSpeed(speed);
  }

  // "T" cycles the staff theme - our own equivalent key (#92 asks for one;
  // Songsterr has nothing to model it on). Same write chooseTheme() above
  // makes by hand, just without an <select> change event to read from.
  function cycleTheme() {
    const at = STAFF_THEMES.indexOf(settings.staff_theme);
    const next = STAFF_THEMES[(at + 1 + STAFF_THEMES.length) % STAFF_THEMES.length] ?? STAFF_THEMES[0];
    setSetting("staff_theme", next).catch(() => {});
  }

  // Mirrors exactly what Metronome.svelte's own toggle() does when it is
  // driving a supplied `control` rather than owning an engine of its own
  // (ownsClick={false}, exactly how it is used below) - `enabled` bound back
  // here, and the control told directly. There is no click to prime from a
  // keyboard event the way toggle() primes one for a caller with no
  // transport of its own (see that function's own comment): the score's own
  // Play button is what gets audio going in this view, keyboard or not.
  function toggleMetronome() {
    metronome = !metronome;
    view?.metronome?.setEnabled(metronome);
  }

  function isTypingTarget(el) {
    const tag = el?.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable;
  }

  // Space is also how a focused BUTTON activates itself - a standard browser
  // default action this file does not own, and Enter does the same, but
  // nothing here binds Enter so only Space needs this exemption. A <button>
  // is none of INPUT/TEXTAREA/SELECT/contenteditable, so isTypingTarget alone
  // does not cover it: tabbing to the Loop button and pressing Space used to
  // preventDefault() the browser's own click-on-Space before it could fire,
  // starting playback instead of toggling Loop - and the same for every
  // other button in this toolbar, the viewer header, and the session-detail
  // panel, since this listener sits on the window and sees every keydown
  // regardless of which button has focus.
  function isButtonLikeTarget(el) {
    const tag = el?.tagName;
    return tag === "BUTTON" || el?.getAttribute?.("role") === "button";
  }

  /**
   * The single-key transport (#92). Guarded on: whether this instance is
   * even the one allowed to answer the keyboard right now (see `active`
   * above); whether the keypress is really meant for this view and not a
   * browser/OS shortcut (a held Ctrl/Meta - Alt is allowed through
   * deliberately, see below); whether a text field has focus - the one the
   * issue's own comment calls worse than no shortcut at all if it is wrong,
   * checked HERE, before the per-key switch, precisely because nothing below
   * it is safe to run while a field is focused, typing guard first; and,
   * narrower still and case-by-case rather than a blanket exemption, whether
   * the focused element already owns the specific key being pressed (Space
   * on a button - see isButtonLikeTarget).
   */
  function onKey(e) {
    if (!active) return;
    if (isTypingTarget(e.target)) return;
    // Never hijack a browser/OS shortcut. Alt is included even though #92's
    // reference comment builds its own fine-tempo shortcut on Alt+A/D - see
    // TabViewer's PR notes for why that one is not wired here: this view's
    // playback speed is a plain multiplier with no bpm of its own to step by
    // one, and inventing one is the preset-ladder rework the brief for this
    // issue says is explicitly not in scope.
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.repeat && e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "ArrowUp" && e.key !== "ArrowDown") {
      // held-key repeat is exactly what makes stepping the cursor or nudging
      // a loop boundary usable without tapping an arrow key twenty times -
      // every other shortcut here is a toggle or a one-shot action, where an
      // OS auto-repeat firing it a dozen times a second is just noise (or,
      // for L/N/C, a rapid double-toggle that looks like nothing happened).
      return;
    }
    switch (e.key) {
      case " ":
        // A focused BUTTON owns Space already - see isButtonLikeTarget.
        // Checked before preventDefault(), not after: calling
        // preventDefault() on this keydown at all suppresses the browser's
        // own default action for it regardless of who called it, which
        // includes "activate the focused button" - so this has to be a
        // return, not a fall-through that merely skips playPause().
        if (isButtonLikeTarget(e.target)) return;
        // preventDefault BEFORE the readiness check, always, for this one and
        // every case below that has a native default worth blocking: Space
        // scrolls the page and Backspace can navigate history when nothing
        // else claims them, and both would otherwise still happen during the
        // (brief, but real) window before playerReady - see PdfViewer's own
        // onKey, which does the same for its page-turn keys unconditionally.
        e.preventDefault();
        if (playerReady) view?.playPause();
        return;
      case "Backspace":
        e.preventDefault();
        if (playerReady) view?.stop();
        return;
      case "ArrowLeft":
      case "ArrowRight": {
        e.preventDefault();
        if (!playerReady) return;
        const dir = e.key === "ArrowRight" ? 1 : -1;
        if (e.shiftKey) view?.nudgeLoopBoundary(dir);
        else view?.moveCursorBeat(dir);
        return;
      }
      case "ArrowUp":
      case "ArrowDown": {
        e.preventDefault();
        if (!playerReady) return;
        view?.moveCursorBar(e.key === "ArrowDown" ? 1 : -1);
        return;
      }
      case "l":
      case "L":
        toggleLoop();
        return;
      case "s":
      case "S":
        cycleSpeed();
        return;
      case "n":
      case "N":
        toggleMetronome();
        return;
      case "c":
      case "C":
        toggleCountIn();
        return;
      case "t":
      case "T":
        cycleTheme();
        return;
      case "1":
      case "2":
      case "3":
        setProfile(["score", "tab", "scoretab"][Number(e.key) - 1]);
        return;
      default:
        return;
    }
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="wrap">
  {#if gigMode}
    <!-- gig mode: hide the practice toolbar chrome, but playback and the way
    back out must stay reachable even with no keyboard (touch/tablet) -->
    <div class="gig-hud">
      <button class="primary" disabled={!playerReady} onclick={() => view?.playPause()}>
        {playing ? "❚❚ Pause ((Space))" : "▶ Play ((Space))"}
      </button>
      <button disabled={!playerReady} onclick={() => view?.stop()} aria-label="Stop — back to the start ((Backspace))">
        ■
      </button>
      {#if metronome && metronomeTempo != null}
        <!-- Read-only here on purpose - gig mode strips the toolbar down to
        large, glanceable touch targets, and choosing a mode or typing a
        number is neither. "The current value visible while playing" still
        has to hold at a music stand, though, so the readout comes along even
        though its controls stay behind in the full toolbar, set up before
        stepping into gig mode. -->
        <span class="metronome-indicator" title="Metronome, clicks per minute">♩ {metronomeTempo}</span>
      {/if}
      {#if practiceLabel}
        <button class="practice-indicator" onclick={onStopPractice} title="Stop practice timer">
          ● {practiceLabel}
        </button>
      {/if}
      <button onclick={onToggleGig} aria-label="Exit gig mode ((Esc))" title="Exit gig mode (Esc)">⤢</button>
    </div>
  {:else}
    <div class="toolbar">
      {#if profileOptions?.length}
        <div class="seg">
          {#each PROFILE_LABELS as [value, label], i}
            {#if profileOptions.includes(value)}
              <!-- aria-label, not appended text: toolbar-responsive.spec.js
                   matches this button by its exact text ("Tab", anchored) -
                   see the note on the theme/speed selects above for the same
                   reason applied to a <button> instead of a <select>. -->
              <button
                class:on={profile === value}
                onclick={() => setProfile(value)}
                aria-label={`${label} ((${i + 1}))`}
              >
                {label}
              </button>
            {/if}
          {/each}
        </div>
      {/if}
      <select
        class="theme-picker"
        value={settings.staff_theme}
        onchange={chooseTheme}
        title="Staff theme"
        aria-label="Staff theme ((T))"
      >
        {#each STAFF_THEMES as t}
          <option value={t}>{STAFF_THEME_LABELS[t]}</option>
        {/each}
      </select>
      <div class="player">
        <button class="primary" disabled={!playerReady} onclick={() => view?.playPause()}>
          {playing ? "❚❚ Pause ((Space))" : "▶ Play ((Space))"}
        </button>
        <button disabled={!playerReady} onclick={() => view?.stop()} aria-label="Stop — back to the start ((Backspace))">
          ■
        </button>
        <select value={speed} onchange={setSpeed} title="Playback speed" aria-label="Playback speed ((S))">
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
            title="Loop playback ((L)) — drag across bars on the score to loop a section"
          >
            Loop ((L))
          </button>
          <!-- The general metronome (issue #97), pre-filled from this score:
          its declared tempo as the base the percentages are of, and its own
          time signature (which the click reads live from the playhead, so a
          meter change mid-piece is followed rather than assumed). compact,
          because over a piece the meter and subdivision come from the score
          and there is nothing there for a player to set; and nothing here
          persists, for the reason on `metronome`'s declaration above.

          tempoSource is what the control is allowed to CALL that base tempo.
          "not declared at the start" beats both of the other two: a score that
          prints no tempo there hands the renderer's fallback to a
          transcription and to a MusicXML file alike, and neither of them read
          it anywhere. tempoElsewhere then separates "the document says nothing
          about its tempo" from "it says something, just not here" - only the
          first may be stated as a fact about the document. -->
          <Metronome
            ownsClick={false}
            control={view?.metronome ?? null}
            bind:enabled={metronome}
            tempo={metronomeTempo}
            limit={metronomeLimit}
            proportionBase={true}
            baseTempoLabel={scoreTempo}
            tempoSource={scoreTempoFrom !== "start" ? "default" : tex != null ? "transcribed" : "marked"}
            tempoElsewhere={scoreTempoFrom === "later"}
            bind:mode={metronomeMode}
            bind:proportion={metronomeProportion}
            bind:bpm={metronomeBpm}
            compact={true}
            keyHint="N"
          />
          <button class:on={countIn} onclick={toggleCountIn} title="Count-in before playback starts ((C))">
            Count-in ((C))
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

  {#if profileOptions && profileOptions.length === 0}
    <!-- Nothing about this score is drawable under any profile - see
    score-render.js's supportedProfiles(). The renderer's own attempt to
    render it anyway still runs (it cannot be cancelled from here) and still
    throws internally, but that failure is caught and suppressed at the
    source rather than shown, so this plain sentence - not a stack trace - is
    the only thing a guitarist sees. The staff area below stays in the DOM
    (score-render.js's `host` binding needs a stable element) but is hidden
    rather than left showing whatever the failed render left behind. -->
    <p class="notice">{UNRENDERABLE_MESSAGE}</p>
  {/if}

  <div class="score-scroll" class:hidden={profileOptions?.length === 0} bind:this={scroller}>
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

  /* Below ~869px this row no longer fits in one line at all (issue #106):
     on a portrait tablet - 834 and 768 are ordinary widths for one, and the
     project's own stated primary form factor - the row used to hold its
     width and let flex-shrink squeeze individual controls instead. That
     shrinking hit `.seg` hardest, because `.seg` has its own overflow:hidden
     for its rounded corners: shrunk below its buttons' combined width, the
     buttons it couldn't show were not just squeezed, they were clipped away
     entirely - present in the DOM, invisible, and unreachable by touch or
     mouse alike. Narrower still (430px), the whole row stopped fitting even
     after every control had shrunk as far as it could, and the toolbar
     itself overflowed the page, taking Metronome/Count-in/Ladder off the
     right edge with no scrollbar reachable by touch.

     The fix is to never shrink a control at all and instead let the row
     wrap - vertical space is what a tablet on a stand has to spare, reach
     across a clipped or off-screen button is what it does not. `.player`
     and `.practice` below get the same treatment, so the wrap cascades:
     first the toolbar's own top-level groups, then the transport row's
     controls, then the loop/metronome/count-in/ladder cluster - each only
     drops to a new line once it, not something it contains, stops fitting. */
  .toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
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

  /* Play, speed and Loop are what a player reaches for mid-piece (#106), so
     below the width where anything has to move to a second line, this is
     the row that stays first and whole - not the profile switch or the
     theme picker, neither of which anyone reaches for while a piece is
     running. flex-basis: 100% forces everything after it (`.seg`,
     `.theme-picker`) onto a line of their own rather than sharing this one
     and leaving less room for it to wrap internally in a sensible order. */
  @media (max-width: 900px) {
    .player {
      order: -1;
      flex-basis: 100%;
      margin-left: 0;
    }
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
    flex-wrap: wrap;
    gap: 8px;
    /* pushed to the far edge now that .toolbar no longer uses
       justify-content: space-between - the theme picker sits between it and
       .seg instead of splitting the row in two. Only holds at widths where
       the whole row fits on one line - the narrow-width rule above resets
       it, since a row forced flush left by wrapping has nothing to be
       pushed away from. */
    margin-left: auto;
  }

  .practice {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
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

  /* .metronome-readout and .metronome-controls moved into Metronome.svelte
     along with the controls themselves. What stays here is the gig-mode
     HUD's own read-only echo below, which is this component's markup.

     The gig-mode HUD's read-only echo of the same value - see the markup
     above for why the controls themselves do not follow it into gig mode. */
  .metronome-indicator {
    font-size: 14px;
    font-variant-numeric: tabular-nums;
    color: var(--brass-bright);
    white-space: nowrap;
  }

  .score-scroll {
    flex: 1;
    /* horizontal layout is one endless system, so the stage has to scroll
       sideways as well - page layout never overflows this way */
    overflow: auto;
    padding: 20px;
  }

  /* Stays in the DOM (score-render.js's `host` binding has to stay stable)
     for a score with nothing drawable - just not shown, so whatever the
     renderer's own suppressed failed render left behind is never visible. */
  .score-scroll.hidden {
    display: none;
  }

  .notice {
    color: var(--ink-dim);
    text-align: center;
    margin: 32px 8px;
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
