<script>
  import { untrack } from "svelte";
  import * as alphaTab from "@coderline/alphatab";
  import { api } from "./api.js";

  let { score = null, demo = false } = $props();

  let host;
  let scroller;
  let atApi = $state(null);
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

  const PROFILES = [
    ["score", "Notation"],
    ["tab", "Tab"],
    ["scoretab", "Both"],
  ];

  const SPEEDS = [0.5, 0.75, 1, 1.25];

  const PROFILE_MAP = {
    score: alphaTab.StaveProfile.Score,
    tab: alphaTab.StaveProfile.Tab,
    scoretab: alphaTab.StaveProfile.ScoreTab,
  };

  const DEMO_TEX = `\\title "Fermata Demo"
\\subtitle "Estudio in E minor"
\\tempo 80
.
:8 0.1 3.2 2.3 0.1 3.2 2.3 0.1 3.2 |
:8 0.2 2.3 2.4 0.2 2.3 2.4 0.2 2.3 |
:8 1.1 0.2 2.3 1.1 0.2 2.3 1.1 0.2 |
:8 0.1 0.2 1.3 0.1 0.2 1.3 0.1 0.2 |
:2 (0.6 2.5 2.4 0.3 0.2 0.1) :2 (0.6 2.5 2.4 0.3 0.2 0.1)`;

  $effect(() => {
    const at = new alphaTab.AlphaTabApi(host, {
      core: {
        scriptFile: "/alphatab/alphaTab.min.js",
        fontDirectory: "/alphatab/font/",
      },
      player: {
        enablePlayer: true,
        soundFont: "/alphatab/soundfont/sonivox.sf2",
        scrollElement: scroller,
      },
      display: {
        // untrack: profile changes are handled imperatively in setProfile;
        // tracking it here would tear down and recreate the player.
        staveProfile: PROFILE_MAP[untrack(() => profile)],
        scale: 1,
      },
    });

    at.playerReady.on(() => (playerReady = true));
    at.playerStateChanged.on((e) => (playing = e.state === 1));
    at.error.on((e) => {
      loadError = e?.message ?? "failed to load score";
    });
    // playerFinished fires at the end of each loop pass (not just final stop),
    // which is what makes it usable as the "one clean pass done" signal below.
    at.playerFinished.on(() => {
      if (!ladder) return;
      const next = Math.round(speed * 100) + ladderStep;
      if (next >= ladderTarget) {
        speed = ladderTarget / 100;
        ladder = false;
      } else {
        speed = next / 100;
      }
      at.playbackSpeed = speed;
    });

    if (demo) {
      at.tex(DEMO_TEX);
    } else if (score) {
      fetch(api.fileUrl(score.id))
        .then((r) => r.arrayBuffer())
        .then((buf) => at.load(new Uint8Array(buf)))
        .catch((e) => (loadError = String(e)));
    }

    atApi = at;
    return () => at.destroy();
  });

  function setProfile(p) {
    profile = p;
    if (!atApi) return;
    atApi.settings.display.staveProfile = PROFILE_MAP[p];
    atApi.updateSettings();
    atApi.render();
  }

  function setSpeed(ev) {
    speed = Number(ev.target.value);
    if (atApi) atApi.playbackSpeed = speed;
  }

  function toggleLoop() {
    looping = !looping;
    if (atApi) atApi.isLooping = looping;
  }

  function toggleMetronome() {
    metronome = !metronome;
    if (atApi) atApi.metronomeVolume = metronome ? 1 : 0;
  }

  function toggleCountIn() {
    countIn = !countIn;
    if (atApi) atApi.countInVolume = countIn ? 1 : 0;
  }

  function clamp(n, lo, hi) {
    if (Number.isNaN(n)) return lo;
    return Math.min(hi, Math.max(lo, n));
  }

  function toggleLadder() {
    ladder = !ladder;
    if (!ladder) return;
    looping = true;
    speed = ladderStart / 100;
    if (atApi) {
      atApi.isLooping = true;
      atApi.playbackSpeed = speed;
    }
  }

  function setLadderStart(ev) {
    ladderStart = clamp(Number(ev.target.value), 10, 100);
  }

  function setLadderStep(ev) {
    ladderStep = clamp(Number(ev.target.value), 1, 25);
  }

  function setLadderTarget(ev) {
    ladderTarget = clamp(Number(ev.target.value), 10, 200);
  }
</script>

<div class="wrap">
  <div class="toolbar">
    <div class="seg">
      {#each PROFILES as [value, label]}
        <button class:on={profile === value} onclick={() => setProfile(value)}>{label}</button>
      {/each}
    </div>
    <div class="player">
      <button class="primary" disabled={!playerReady} onclick={() => atApi?.playPause()}>
        {playing ? "❚❚ Pause" : "▶ Play"}
      </button>
      <button disabled={!playerReady} onclick={() => atApi?.stop()}>■</button>
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
              <input type="number" min="10" max="100" step="1" value={ladderStart} onchange={setLadderStart} />
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

  {#if loadError}
    <p class="error">{loadError}</p>
  {/if}

  <div class="score-scroll" bind:this={scroller}>
    <div class="at-host" bind:this={host}></div>
  </div>
</div>

<style>
  .wrap {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--line);
    background: var(--bg-raised);
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
    overflow-y: auto;
    padding: 20px;
  }

  .at-host {
    background: var(--paper);
    border-radius: 6px;
    max-width: 1100px;
    margin: 0 auto;
    padding: 24px;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.55);
  }

  .error {
    color: var(--danger);
    text-align: center;
    margin: 8px;
  }
</style>
