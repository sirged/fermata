<script>
  import {
    getSettings,
    setSetting,
    STAFF_THEMES,
    STAFF_THEME_LABELS,
    STAFF_THEME_TOKEN_PREFIX,
  } from "./settings.svelte.js";

  const settings = getSettings();

  // Which theme has a write in flight. The whole picker is disabled while
  // this is set, not just that one card - letting the others stay clickable
  // would let a second write for the same key race the first, and a slow
  // connection would otherwise make every *other* card look clickable while
  // silently doing nothing.
  let pending = $state(null);
  let error = $state("");

  async function chooseTheme(theme) {
    if (theme === settings.staff_theme || pending) return;
    pending = theme;
    error = "";
    try {
      await setSetting("staff_theme", theme);
    } catch (e) {
      // setSetting already rolled the optimistic value back - surface why,
      // so a failed save reads as a failure rather than an unexplained
      // highlight-then-unhighlight
      error = e?.message ?? "Could not save that.";
    } finally {
      pending = null;
    }
  }
</script>

<div class="settings">
  <header>
    <a class="back" href="#/">← Library</a>
    <h1>Settings</h1>
  </header>

  <main>
    <section>
      <h2>Staff theme</h2>
      <p class="hint">
        How notation and tab are drawn, wherever a score renders. Saved to your account, so it
        follows you to any device.
      </p>
      <div class="theme-grid">
        {#each STAFF_THEMES as theme}
          {@const prefix = STAFF_THEME_TOKEN_PREFIX[theme]}
          <button
            class="theme-card"
            class:on={settings.staff_theme === theme}
            class:saving={pending === theme}
            disabled={!!pending}
            onclick={() => chooseTheme(theme)}
          >
            <span
              class="swatch"
              style={`background:var(${prefix}-surface); color:var(${prefix}-ink); border-color:var(${prefix}-line)`}
            >
              <span class="swatch-line" style={`background:var(${prefix}-line)`}></span>
              Aa
            </span>
            <span class="label">{STAFF_THEME_LABELS[theme]}</span>
          </button>
        {/each}
      </div>
      {#if error}
        <p class="error">{error}</p>
      {/if}
    </section>
  </main>
</div>

<style>
  .settings {
    height: 100vh;
    display: flex;
    flex-direction: column;
  }

  header {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--line);
    background: var(--bg-raised);
  }

  .back {
    color: var(--ink-dim);
    white-space: nowrap;
  }

  .back:hover {
    color: var(--brass-bright);
  }

  header h1 {
    font-size: 18px;
  }

  main {
    flex: 1;
    overflow-y: auto;
    padding: 28px;
  }

  section {
    max-width: 640px;
  }

  h2 {
    font-size: 15px;
    margin-bottom: 6px;
  }

  .hint {
    color: var(--ink-dim);
    font-size: 13px;
    margin: 0 0 18px;
    line-height: 1.5;
  }

  .theme-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px;
  }

  .theme-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    padding: 14px 10px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 10px;
  }

  .theme-card.on {
    border-color: var(--brass);
    box-shadow: 0 0 0 1px var(--brass);
  }

  .theme-card.saving {
    opacity: 0.6;
  }

  .theme-card:disabled {
    cursor: default;
  }

  .error {
    color: var(--danger);
    font-size: 13px;
    margin: 14px 0 0;
  }

  .swatch {
    width: 100%;
    aspect-ratio: 4 / 3;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    border: 1px solid;
    border-radius: 6px;
    font-family: var(--font-display);
    font-size: 22px;
  }

  .swatch-line {
    position: absolute;
    left: 12%;
    right: 12%;
    top: 30%;
    height: 1px;
  }

  .label {
    font-size: 13px;
    color: var(--ink);
  }
</style>
