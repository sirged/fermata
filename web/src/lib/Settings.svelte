<script>
  import {
    getSettings,
    setSetting,
    STAFF_THEMES,
    STAFF_THEME_LABELS,
    STAFF_THEME_TOKEN_PREFIX,
  } from "./settings.svelte.js";

  const settings = getSettings();

  // Which theme (if any) has a write in flight, so only that card disables
  // itself rather than the whole picker while the request is out.
  let pending = $state(null);

  async function chooseTheme(theme) {
    if (theme === settings.staff_theme || pending) return;
    pending = theme;
    try {
      await setSetting("staff_theme", theme);
    } catch {
      // setSetting already rolled the optimistic value back; nothing else to do
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
            disabled={pending === theme}
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
