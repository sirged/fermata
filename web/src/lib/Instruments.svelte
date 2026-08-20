<script>
  import {
    MAX_FRETS,
    MAX_REFERENCE_HZ,
    MAX_STRINGS,
    MIN_REFERENCE_HZ,
    auditionPitch,
    definitionFrom,
    draftFrom,
    draftStrings,
    formatFrequency,
    getInstruments,
    loadInstruments,
    removeInstrument,
    resizeStrings,
    saveInstrument,
  } from "./instruments.svelte.js";

  const instruments = getInstruments();
  loadInstruments();

  // The definition being edited, or null when nothing is. A draft is a copy,
  // so nothing a person types touches the saved instrument until they save -
  // which matters here more than usual, because the way to check a tuning is
  // to type a pitch and listen to it, and most of what gets typed that way is
  // meant to be thrown away.
  let draft = $state(null);
  let presetKey = $state("");
  let saving = $state(false);
  let error = $state("");
  // What was last sounded, and how many times anything has been. Published
  // onto the section (see data-audition-*) for the same reason score-render.js
  // publishes its layout: it is the only way to see from outside that a click
  // reached the synthesiser rather than merely looking as though it had.
  let auditioned = $state(null);
  let auditions = $state(0);
  let auditionError = $state("");

  const strings = $derived(draftStrings(draft));
  const badStrings = $derived(strings.filter((s) => s.midi == null).length);

  function startFromPreset(key) {
    presetKey = key;
    const preset = instruments.presets.find((p) => p.key === key);
    if (!preset) return;
    draft = draftFrom(preset);
    error = "";
  }

  function edit(instrument) {
    draft = draftFrom(instrument);
    presetKey = "";
    error = "";
  }

  function cancel() {
    draft = null;
    presetKey = "";
    error = "";
  }

  function setStringCount(count) {
    const wanted = Math.max(1, Math.min(MAX_STRINGS, Math.round(count) || 1));
    draft.string_pitches = resizeStrings(draft.string_pitches, wanted);
  }

  function setFretted(fretted) {
    draft.fretted = fretted;
    // Position reasoning and tablature mean nothing without frets, so the
    // fields that only describe frets are dropped rather than hidden and kept -
    // the server rejects them on an unfretted definition, and a value that is
    // invisible on screen but still in the payload is the worst of both.
    if (fretted) {
      draft.fret_count = draft.fret_count ?? 22;
      draft.capo = draft.capo ?? 0;
    } else {
      draft.fret_count = null;
      draft.capo = null;
    }
  }

  async function save() {
    if (saving) return;
    saving = true;
    error = "";
    try {
      const saved = await saveInstrument(draft.id, definitionFrom(draft));
      draft = null;
      presetKey = "";
      return saved;
    } catch (e) {
      error = e?.message ?? "Could not save that.";
    } finally {
      saving = false;
    }
  }

  async function remove(instrument) {
    error = "";
    try {
      await removeInstrument(instrument.id);
      if (draft?.id === instrument.id) draft = null;
    } catch (e) {
      error = e?.message ?? "Could not delete that.";
    }
  }

  async function play(string) {
    if (string.midi == null) return;
    auditionError = "";
    try {
      const sounded = await auditionPitch(string.midi);
      if (!sounded) {
        auditionError = "That pitch is outside what can be played.";
        return;
      }
      auditioned = string;
      auditions += 1;
    } catch (e) {
      auditionError = e?.message ?? "The synthesiser could not be loaded.";
    }
  }

  function summary(instrument) {
    const parts = [`${instrument.string_count} strings`];
    parts.push(instrument.fretted ? `${instrument.fret_count} frets` : "unfretted");
    if (instrument.capo) parts.push(`capo ${instrument.capo}`);
    parts.push(`A${Math.round(instrument.reference_pitch)}`);
    return parts.join(" · ");
  }
</script>

<section
  data-instrument-count={instruments.list.length}
  data-instrument-draft={draft ? (draft.id ?? "new") : ""}
  data-audition-count={auditions}
  data-audition-midi={auditioned?.midi ?? ""}
  data-audition-pitch={auditioned?.pitch ?? ""}
>
  <h2>Instruments</h2>
  <p class="hint">
    What you play, and how it is tuned. The same guitar in standard and in dropped D is two
    instruments here, because the tuning is what a score, a fretboard and playback all need to
    know. Play any string on its own to check a tuning by ear.
  </p>

  {#if instruments.list.length}
    <ul class="owned">
      {#each instruments.list as instrument (instrument.id)}
        <li data-instrument-id={instrument.id}>
          <div class="owned-text">
            <span class="owned-name">{instrument.name}</span>
            <span class="owned-summary">{summary(instrument)}</span>
          </div>
          <div class="owned-actions">
            <button onclick={() => edit(instrument)}>Tune</button>
            <button class="danger" onclick={() => remove(instrument)}>Delete</button>
          </div>
        </li>
      {/each}
    </ul>
  {:else if instruments.loaded}
    <p class="empty">No instruments yet. Start from one of the presets below.</p>
  {/if}

  {#if !draft}
    <div class="start">
      <label>
        Start from
        <select
          value={presetKey}
          onchange={(e) => startFromPreset(e.currentTarget.value)}
        >
          <option value="">Choose a preset…</option>
          {#each instruments.presets as preset (preset.key)}
            <option value={preset.key}>{preset.name}</option>
          {/each}
        </select>
      </label>
    </div>
  {:else}
    <div class="editor">
      <div class="row">
        <label class="grow">
          Name
          <input
            type="text"
            maxlength="80"
            bind:value={draft.name}
            placeholder="My guitar"
          />
        </label>
        <label class="narrow">
          Strings
          <input
            type="number"
            min="1"
            max={MAX_STRINGS}
            value={draft.string_pitches.length}
            onchange={(e) => setStringCount(Number(e.currentTarget.value))}
          />
        </label>
        <label class="narrow">
          Reference (Hz)
          <input
            type="number"
            min={MIN_REFERENCE_HZ}
            max={MAX_REFERENCE_HZ}
            step="0.5"
            bind:value={draft.reference_pitch}
          />
        </label>
      </div>

      <div class="row">
        <label class="check">
          <input
            type="checkbox"
            checked={draft.fretted}
            onchange={(e) => setFretted(e.currentTarget.checked)}
          />
          Fretted
        </label>
        {#if draft.fretted}
          <label class="narrow">
            Frets
            <input type="number" min="1" max={MAX_FRETS} bind:value={draft.fret_count} />
          </label>
          <label class="narrow">
            Capo
            <input
              type="number"
              min="0"
              max={draft.fret_count ?? MAX_FRETS}
              bind:value={draft.capo}
            />
          </label>
        {:else}
          <p class="fretless">
            No frets, so no fret numbers and no tablature — the strings below are the reference
            a note is found against.
          </p>
        {/if}
      </div>

      <ul class="strings">
        {#each strings as string (string.number)}
          <li data-string={string.number} data-frequency={string.frequency ?? ""}>
            <span class="string-number">{string.number}</span>
            <input
              class="string-pitch"
              type="text"
              spellcheck="false"
              aria-label={`String ${string.number} pitch`}
              value={string.pitch}
              oninput={(e) =>
                (draft.string_pitches[strings.length - string.number] =
                  e.currentTarget.value)}
            />
            {#if string.midi == null}
              <span class="string-bad">not a pitch name</span>
            {:else}
              <span class="string-hz">{formatFrequency(string.frequency)}</span>
            {/if}
            <button
              class="play"
              disabled={string.midi == null}
              aria-label={`Play string ${string.number}`}
              onclick={() => play(string)}
            >
              ▶
            </button>
          </li>
        {/each}
      </ul>

      {#if auditionError}
        <p class="error">{auditionError}</p>
      {:else if auditioned}
        <p class="sounding">
          Sounding string {auditioned.number} — {auditioned.pitch},
          {formatFrequency(auditioned.frequency)}
        </p>
      {/if}

      {#if error}
        <p class="error">{error}</p>
      {/if}

      <div class="actions">
        <button
          class="primary"
          disabled={saving || !draft.name.trim() || badStrings > 0}
          onclick={save}
        >
          {draft.id == null ? "Save instrument" : "Save changes"}
        </button>
        <button onclick={cancel}>Cancel</button>
      </div>
    </div>
  {/if}
</section>

<style>
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

  .empty {
    color: var(--ink-dim);
    font-size: 13px;
    margin: 0 0 14px;
  }

  .owned {
    list-style: none;
    margin: 0 0 16px;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .owned li {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 14px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
  }

  .owned-text {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  .owned-name {
    font-family: var(--font-display);
  }

  .owned-summary {
    color: var(--ink-dim);
    font-size: 12px;
  }

  .owned-actions {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }

  button.danger:hover {
    border-color: var(--danger);
    color: var(--danger);
  }

  .start {
    margin-bottom: 8px;
  }

  .editor {
    padding: 16px;
    background: var(--bg-raised);
    border: 1px solid var(--line);
    border-radius: var(--radius);
  }

  .row {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: 12px;
    margin-bottom: 14px;
  }

  label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 12px;
    color: var(--ink-dim);
  }

  label.grow {
    flex: 1;
    min-width: 160px;
  }

  label.narrow {
    width: 96px;
  }

  label.check {
    flex-direction: row;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    color: var(--ink);
  }

  label.check input {
    width: 16px;
    height: 16px;
    padding: 0;
    accent-color: var(--brass);
  }

  .fretless {
    flex: 1;
    min-width: 200px;
    margin: 0;
    font-size: 12px;
    color: var(--ink-dim);
  }

  .strings {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .strings li {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .string-number {
    width: 20px;
    text-align: right;
    color: var(--ink-dim);
    font-size: 12px;
    flex-shrink: 0;
  }

  .string-pitch {
    width: 72px;
    text-align: center;
    font-family: var(--font-display);
  }

  .string-hz {
    /* tabular so a column of frequencies lines up on the decimal point */
    font-variant-numeric: tabular-nums;
    color: var(--ink-dim);
    font-size: 13px;
    min-width: 92px;
  }

  .string-bad {
    color: var(--danger);
    font-size: 12px;
    min-width: 136px;
  }

  .play {
    padding: 4px 10px;
    line-height: 1;
  }

  .play:disabled {
    opacity: 0.4;
    cursor: default;
  }

  .sounding {
    margin: 14px 0 0;
    font-size: 13px;
    color: var(--brass);
  }

  .error {
    color: var(--danger);
    font-size: 13px;
    margin: 14px 0 0;
  }

  .actions {
    display: flex;
    gap: 10px;
    margin-top: 18px;
  }
</style>
