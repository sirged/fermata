<script>
  // Named drill scopes (issue #236) - the picker BOTH drills use.
  //
  // A scope (which strings, which fret range, which key) used to live in
  // whichever component held it and reset on every page load, leaving behind
  // nothing but an English sentence in the practice session's own `note`.
  // This is the control that saves one as a row instead
  // (POST /api/trainer/presets) and picks it up again - in this drill, or in
  // the other one, because the presets table has no `drill` column by
  // design. See server/fermata/db.py's note on trainer_scope_presets.
  //
  // ONE COMPONENT, USED TWICE, not a shape each drill re-implements. That is
  // the whole reason the scope arithmetic was factored into constraints.js in
  // the first place (see its module comment), and the same rule applies to
  // the interface over it: a second copy of "save this, pick that" is a
  // second copy free to drift.
  //
  // TONE, the same rule practice.js's vocabulary list states: nothing here
  // grades or nudges. A saved scope is a thing a person chose to keep, so the
  // words are about choosing and keeping - never about how much of the neck
  // somebody is or is not covering.
  import { api } from "../api.js";
  import { presetFromScope, scopeFromPreset } from "./constraints.js";

  let {
    // The instrument's own strings, so an unfiltered string set can be
    // spelled out by name when it is saved - see presetFromScope.
    strings = [],
    // The scope as it stands right now, which is what Save stores.
    scope = {},
    // The id of the preset currently in force, or null. Held by the parent
    // (it is the parent that knows when a hand-turned control has taken the
    // scope somewhere the preset no longer describes) and passed back down.
    selectedId = null,
    // Nothing here may be touched mid-drill, the same rule every other scope
    // control in both drills follows.
    disabled = false,
    onSelect = () => {},
  } = $props();

  let presets = $state([]);
  let loaded = $state(false);
  let name = $state("");
  let saving = $state(false);
  let problem = $state("");

  async function load() {
    try {
      presets = await api.trainerPresets();
      problem = "";
    } catch (e) {
      problem = e?.message || "Saved scopes could not be loaded.";
    } finally {
      loaded = true;
    }
  }

  $effect(() => {
    load();
  });

  async function save() {
    const typed = name.trim();
    if (!typed || saving || disabled) return;
    saving = true;
    problem = "";
    try {
      const saved = await api.createTrainerPreset(presetFromScope(typed, strings, scope));
      presets = [saved, ...presets];
      name = "";
      // Saving is also choosing: the scope that was stored is the scope the
      // drill is on, so the new entry is the selected one immediately rather
      // than needing a second tap to become true.
      onSelect(scopeFromPreset(saved), saved.id);
    } catch (e) {
      problem = e?.message || "That scope could not be saved.";
    } finally {
      saving = false;
    }
  }

  function choose(preset) {
    if (disabled) return;
    onSelect(scopeFromPreset(preset), preset.id);
  }

  async function remove(preset) {
    if (disabled) return;
    problem = "";
    try {
      await api.deleteTrainerPreset(preset.id);
      presets = presets.filter((p) => p.id !== preset.id);
      if (selectedId === preset.id) onSelect(null, null);
    } catch (e) {
      problem = e?.message || "That scope could not be removed.";
    }
  }

  /** What a saved scope covers, said plainly: the strings it names (all of
   * them is not stated - it is the ordinary case), its fret range, and its
   * key when it has one. Deliberately NOT scopeLabel from constraints.js:
   * that one takes a live instrument's string list to decide what "every
   * string" is, and a preset is shared across instruments by design. */
  function presetLabel(preset) {
    const parts = [];
    const named = preset.strings ?? [];
    if (named.length && named.length !== (strings ?? []).length) {
      parts.push(`string${named.length === 1 ? "" : "s"} ${named.join(", ")}`);
    }
    parts.push(`frets ${preset.start_fret}-${preset.end_fret}`);
    if (preset.key_root) {
      parts.push(`key of ${preset.key_root} ${preset.key_quality ?? "major"}`);
    }
    return parts.join(", ");
  }
</script>

<div class="presets" data-preset-count={presets.length} data-selected-preset={selectedId ?? ""}>
  <div class="row save-row">
    <label>
      <span>Save this scope as</span>
      <input
        class="preset-name"
        type="text"
        bind:value={name}
        {disabled}
        placeholder="A name for it"
      />
    </label>
    <button class="save-preset" onclick={save} disabled={disabled || saving || !name.trim()}>
      {saving ? "Saving…" : "Save"}
    </button>
  </div>

  {#if loaded && presets.length}
    <ul class="preset-list">
      {#each presets as preset (preset.id)}
        <li class="preset" data-preset-id={preset.id}>
          <button
            class="preset-choice"
            class:active={selectedId === preset.id}
            data-preset-name={preset.name}
            {disabled}
            onclick={() => choose(preset)}
          >
            <span class="preset-title">{preset.name}</span>
            <span class="preset-scope">{presetLabel(preset)}</span>
          </button>
          <button
            class="delete-preset"
            {disabled}
            aria-label={`Remove ${preset.name}`}
            onclick={() => remove(preset)}
          >
            Remove
          </button>
        </li>
      {/each}
    </ul>
  {:else if loaded}
    <p class="quiet no-presets">
      No scope has been saved yet. Set the strings, the frets and the key you want, give it a
      name, and it will be here in this drill and in the other one.
    </p>
  {/if}

  {#if problem}
    <p class="notice preset-problem">{problem}</p>
  {/if}
</div>

<style>
  .presets {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .save-row label {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
  }

  input[type="text"] {
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 8px 10px;
    font: inherit;
    font-size: 14px;
    min-height: 44px;
    min-width: 12ch;
  }

  .preset-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .preset {
    display: flex;
    align-items: stretch;
    gap: 8px;
  }

  /* Big touch targets throughout - this app's tablet-at-a-music-stand rule
     (issue #25/#119): every tappable control here is at least 44px tall. */
  button {
    min-height: 44px;
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 8px 14px;
    font: inherit;
    font-size: 14px;
    cursor: pointer;
  }

  button:hover:enabled {
    border-color: var(--brass);
  }

  button:disabled {
    opacity: 0.55;
    cursor: default;
  }

  .preset-choice {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
    text-align: left;
  }

  .preset-choice.active {
    border-color: var(--brass);
    color: var(--brass-bright);
  }

  .preset-title {
    font-size: 15px;
  }

  .preset-scope {
    font-size: 13px;
    color: var(--ink-dim);
    font-variant-numeric: tabular-nums;
  }

  .quiet {
    margin: 0;
    color: var(--ink-dim);
    font-size: 13px;
    line-height: 1.5;
  }

  .notice {
    margin: 0;
    font-size: 14px;
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 8px 12px;
  }
</style>
