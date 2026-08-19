<script>
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
  let loadError = $state("");

  const PROFILES = [
    ["score", "Notation"],
    ["tab", "Tab"],
    ["scoretab", "Both"],
  ];

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
        staveProfile: PROFILE_MAP[profile],
        scale: 1,
      },
    });

    at.playerReady.on(() => (playerReady = true));
    at.playerStateChanged.on((e) => (playing = e.state === 1));
    at.error.on((e) => {
      loadError = e?.message ?? "failed to load score";
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
        <option value={0.5}>0.5×</option>
        <option value={0.75}>0.75×</option>
        <option value={1}>1×</option>
        <option value={1.25}>1.25×</option>
      </select>
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
