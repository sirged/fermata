<script>
  import {
    MAX_FRETS,
    MAX_NAME_CHARS,
    MAX_REFERENCE_HZ,
    MAX_STRINGS,
    MIN_FRETS,
    MIN_REFERENCE_HZ,
    MIN_STRINGS,
    auditionPitch,
    definitionFrom,
    draftFrom,
    draftStrings,
    formatFrequency,
    getInstruments,
    isPlayable,
    loadInstruments,
    removeInstrument,
    resizeStrings,
    saveInstrument,
  } from "./instruments.svelte.js";

  const instruments = getInstruments();
  loadInstruments();

  // The definition being edited, or null when nothing is. A draft is a copy, so
  // nothing a person types touches the saved instrument until they save - which
  // matters here more than usual, because the way to check a tuning is to type
  // a pitch and listen to it, and most of what gets typed that way is meant to
  // be thrown away.
  let draft = $state(null);
  let presetKey = $state("");
  let saving = $state(false);
  let error = $state("");
  // What was last sounded, and how many times anything has been. Published onto
  // the section (see data-audition-*) for the same reason score-render.js
  // publishes its layout: it is the only way to see from outside that a click
  // reached the synthesiser rather than merely looking as though it had.
  let auditioned = $state(null);
  let auditions = $state(0);
  let auditionError = $state("");
  let notice = $state("");
  let retrying = $state(false);

  // Only ever for the draft. A saved instrument's strings come from the server,
  // which has already worked out every note name and frequency - see
  // instruments.svelte.js.
  const strings = $derived(draftStrings(draft));
  const unnamed = $derived(strings.filter((s) => s.midi == null).length);
  const unreachable = $derived(strings.filter((s) => s.midi != null && !isPlayable(s)).length);
  const draftCapo = $derived((draft?.fretted && Number(draft.capo)) || 0);

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
    const wanted = Math.max(MIN_STRINGS, Math.min(MAX_STRINGS, Math.round(count) || MIN_STRINGS));
    draft.string_pitches = resizeStrings(draft.string_pitches, wanted);
  }

  function setFretted(fretted) {
    draft.fretted = fretted;
    // Position reasoning and tablature mean nothing without frets, so the
    // fields that only describe frets are dropped rather than hidden and kept -
    // the server rejects a fret count on an unfretted definition, and a value
    // that is invisible on screen but still in the payload is the worst of both.
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
      await saveInstrument(draft.id, definitionFrom(draft));
      draft = null;
      presetKey = "";
    } catch (e) {
      error = e?.message ?? "Could not save that.";
    } finally {
      saving = false;
    }
  }

  async function remove(instrument) {
    error = "";
    notice = "";
    try {
      const unlinked = await removeInstrument(instrument.id);
      if (draft?.id === instrument.id) draft = null;
      // Said out loud rather than done silently: those scores were written for
      // this instrument, and they no longer name one.
      if (unlinked === 1) notice = "1 score no longer names an instrument.";
      else if (unlinked > 1) notice = `${unlinked} scores no longer name an instrument.`;
    } catch (e) {
      error = e?.message ?? "Could not delete that.";
    }
  }

  async function retryLoad() {
    retrying = true;
    try {
      await loadInstruments();
    } finally {
      retrying = false;
    }
  }

  // Plays the SOUNDING pitch, not the nominal one: a capo raises every string,
  // and an audition that ignored it would be teaching a reference wrong by the
  // capo's position - worse than offering none.
  async function play(string) {
    if (!isPlayable(string)) return;
    auditionError = "";
    try {
      const sounded = await auditionPitch(string.sounding_midi);
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

  function plural(count, word) {
    return `${count} ${word}${count === 1 ? "" : "s"}`;
  }

  /** "A440", "A415.5" - trimmed rather than rounded, so a reference of 415.6
   * does not read back as a tuning nobody uses. */
  function referenceLabel(hz) {
    return `A${Number(Number(hz).toFixed(2))}`;
  }

  function summary(instrument) {
    const parts = [plural(instrument.string_count, "string")];
    if (instrument.fretted) {
      parts.push(plural(instrument.fret_count, "fret"));
      if (instrument.capo) parts.push(`capo ${instrument.capo}`);
    } else {
      parts.push("unfretted");
    }
    parts.push(referenceLabel(instrument.reference_pitch));
    return parts.join(" · ");
  }
</script>

<!-- The sounding half of a string row: where a capo puts it, what that sounds
     at, and the button to hear it. Shared between a saved instrument's strings
     (from the server) and a draft's (computed locally) because both arrive in
     the same shape, and because the capo rule must not be written twice. -->
{#snippet soundingOf(string, capo)}
  {#if capo > 0 && string.sounding_pitch}
    <span class="arrow" aria-hidden="true">→</span>
    <span class="string-sounding">{string.sounding_pitch}</span>
  {/if}
  {#if string.sounding_frequency == null}
    <span class="string-bad">not a pitch name</span>
  {:else}
    <span class="string-hz">{formatFrequency(string.sounding_frequency)}</span>
  {/if}
  <button
    class="play"
    disabled={!isPlayable(string)}
    aria-label={`Play string ${string.number}`}
    onclick={() => play(string)}
  >
    ▶
  </button>
{/snippet}

{#snippet capoLegend(capo)}
  {#if capo > 0}
    <p class="capo-note">
      Nominal tuning → sounding pitch, with the capo at fret {capo}. The stored tuning is the
      open one; what you hear is what the capo makes.
    </p>
  {/if}
{/snippet}

<section
  data-instrument-count={instruments.list.length}
  data-instrument-draft={draft ? (draft.id ?? "new") : ""}
  data-audition-count={auditions}
  data-audition-midi={auditioned?.sounding_midi ?? ""}
  data-audition-pitch={auditioned?.sounding_pitch ?? ""}
>
  <h2>Instruments</h2>
  <p class="hint">
    What you play, and how it is tuned. The same guitar in standard and in dropped D is two
    instruments here, because the tuning is what a score, a fretboard and playback all need to
    know. Play any string on its own to check a tuning by ear.
  </p>

  <!-- One readout for the whole section, not one per panel: a string can be
       played from a saved instrument's row or from the editor, and feedback
       that appeared only inside the editor would report a click that happened
       somewhere else - or nowhere at all, with no editor open. -->
  <!-- A load failure is retryable, and has to be: a preset is the only way to
       start a definition, so an empty list with an empty dropdown and no
       explanation would look like a feature that does not work. -->
  {#if instruments.error}
    <p class="error load">
      {instruments.error}
      <button onclick={retryLoad} disabled={retrying}>
        {retrying ? "Trying…" : "Try again"}
      </button>
    </p>
  {/if}

  {#if auditionError}
    <p class="error audition">{auditionError}</p>
  {:else if auditioned}
    <p class="sounding">
      Sounding string {auditioned.number} — {auditioned.sounding_pitch},
      {formatFrequency(auditioned.sounding_frequency)}
    </p>
  {/if}

  {#if notice}
    <p class="notice">{notice}</p>
  {/if}

  {#if instruments.list.length}
    <ul class="owned">
      {#each instruments.list as instrument (instrument.id)}
        <li data-instrument-id={instrument.id}>
          <div class="owned-head">
            <div class="owned-text">
              <span class="owned-name">{instrument.name}</span>
              <span class="owned-summary">{summary(instrument)}</span>
            </div>
            <div class="owned-actions">
              <button onclick={() => edit(instrument)}>Tune</button>
              <button class="danger" onclick={() => remove(instrument)}>Delete</button>
            </div>
          </div>
          {@render capoLegend(instrument.capo ?? 0)}
          <ul class="strings">
            {#each instrument.strings as string (string.number)}
              <li
                data-string={string.number}
                data-frequency={string.sounding_frequency}
                data-sounding-midi={string.sounding_midi}
              >
                <span class="string-number">{string.number}</span>
                <span class="string-pitch-fixed">{string.pitch}</span>
                {@render soundingOf(string, instrument.capo ?? 0)}
              </li>
            {/each}
          </ul>
        </li>
      {/each}
    </ul>
  {:else if instruments.loaded && !instruments.error}
    <p class="empty">No instruments yet. Start from one of the presets below.</p>
  {/if}

  {#if !draft}
    {#if instruments.presets.length}
      <div class="start">
        <label>
          Start from
          <select value={presetKey} onchange={(e) => startFromPreset(e.currentTarget.value)}>
            <option value="">Choose a preset…</option>
            {#each instruments.presets as preset (preset.key)}
              <option value={preset.key}>{preset.name}</option>
            {/each}
          </select>
        </label>
      </div>
    {/if}
  {:else}
    <div class="editor">
      <div class="row">
        <label class="grow">
          Name
          <input
            type="text"
            maxlength={MAX_NAME_CHARS}
            bind:value={draft.name}
            placeholder="My guitar"
          />
        </label>
        <label class="narrow">
          Strings
          <input
            type="number"
            min={MIN_STRINGS}
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
            <input
              type="number"
              min={MIN_FRETS}
              max={MAX_FRETS}
              bind:value={draft.fret_count}
            />
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

      {@render capoLegend(draftCapo)}

      <ul class="strings">
        {#each strings as string (string.number)}
          <li
            data-string={string.number}
            data-frequency={string.sounding_frequency ?? ""}
            data-sounding-midi={string.sounding_midi ?? ""}
          >
            <span class="string-number">{string.number}</span>
            <input
              class="string-pitch"
              type="text"
              spellcheck="false"
              aria-label={`String ${string.number} pitch`}
              value={string.pitch}
              oninput={(e) =>
                (draft.string_pitches[strings.length - string.number] = e.currentTarget.value)}
            />
            {@render soundingOf(string, draftCapo)}
          </li>
        {/each}
      </ul>

      {#if unreachable > 0}
        <p class="error">
          The capo puts {unreachable === 1 ? "a string" : `${unreachable} strings`} above the
          highest note that can be played.
        </p>
      {/if}

      {#if error}
        <p class="error">{error}</p>
      {/if}

      <div class="actions">
        <button
          class="primary"
          disabled={saving || !draft.name.trim() || unnamed > 0 || unreachable > 0}
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

  .owned > li {
    padding: 10px 14px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
  }

  .owned-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
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

  .owned .strings {
    margin-top: 10px;
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

  .capo-note {
    margin: 0 0 12px;
    font-size: 12px;
    color: var(--ink-dim);
    line-height: 1.5;
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

  .string-pitch-fixed {
    width: 44px;
    font-family: var(--font-display);
  }

  .arrow {
    color: var(--ink-dim);
    font-size: 12px;
  }

  .string-sounding {
    width: 44px;
    color: var(--brass-bright);
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
    min-width: 92px;
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
    margin: 0 0 14px;
    font-size: 13px;
    color: var(--brass);
  }

  .error {
    color: var(--danger);
    font-size: 13px;
    margin: 14px 0 0;
  }

  .error.audition,
  .error.load {
    margin: 0 0 14px;
  }

  .error.load {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .notice {
    margin: 0 0 14px;
    font-size: 13px;
    color: var(--ink-dim);
  }

  .actions {
    display: flex;
    gap: 10px;
    margin-top: 18px;
  }
</style>
