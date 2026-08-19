<script>
  import { untrack } from "svelte";
  import { api } from "./api.js";
  import PdfViewer from "./PdfViewer.svelte";
  import TabViewer from "./TabViewer.svelte";

  let {
    score,
    gigMode = false,
    onToggleGig = () => {},
    practiceLabel = null,
    onStopPractice = () => {},
  } = $props();

  const LAYOUT_KEY = "fermata.layout";

  function loadStoredLayout() {
    try {
      return localStorage.getItem(LAYOUT_KEY);
    } catch {
      return null;
    }
  }

  function persistLayout(l) {
    try {
      localStorage.setItem(LAYOUT_KEY, l);
    } catch {
      // storage unavailable (private browsing, etc) - layout just won't persist
    }
  }

  // no stored preference yet: lean on side-by-side as soon as there's
  // something to compare, since verification is the whole point
  let layout = $state(loadStoredLayout() ?? (untrack(() => score.has_transcription) ? "side" : "pdf"));

  function setLayout(l) {
    layout = l;
    persistLayout(l);
  }

  function maybeDefaultToSide() {
    if (loadStoredLayout()) return; // user already made an explicit choice
    layout = "side";
  }

  // "loading" | "none" | "transcribing" | "ready" | "error"
  let transcriptionState = $state("loading");
  let transcription = $state(null);
  let fetchError = $state("");

  let tsNum = $state("");
  let tsDen = $state("");

  let editorOpen = $state(false);
  let draft = $state("");
  let saving = $state(false);
  let saveError = $state("");
  let reverting = $state(false);

  async function loadTranscription() {
    transcriptionState = "loading";
    fetchError = "";
    try {
      const t = await api.transcription(score.id);
      transcription = t;
      draft = t.content;
      transcriptionState = "ready";
      maybeDefaultToSide();
    } catch (e) {
      const msg = String(e?.message ?? e);
      if (msg.startsWith("404")) {
        transcriptionState = "none";
      } else {
        // endpoint missing entirely, network down, backend not merged yet -
        // degrade rather than pretend a transcription exists
        transcriptionState = "error";
        fetchError = msg;
      }
    }
  }

  $effect(() => {
    void score.id;
    loadTranscription();
  });

  async function runTranscribe() {
    transcriptionState = "transcribing";
    fetchError = "";
    const n = Number(tsNum);
    const d = Number(tsDen);
    const body = {};
    if (tsNum !== "" && tsDen !== "" && Number.isFinite(n) && Number.isFinite(d) && n > 0 && d > 0) {
      body.time_signature = [n, d];
    }
    try {
      const t = await api.transcribe(score.id, body);
      transcription = t;
      draft = t.content;
      transcriptionState = "ready";
      maybeDefaultToSide();
    } catch (e) {
      transcriptionState = "none";
      fetchError = String(e?.message ?? e);
    }
  }

  function openEditor() {
    draft = transcription?.content ?? "";
    saveError = "";
    editorOpen = true;
  }

  async function saveEdit() {
    saving = true;
    saveError = "";
    try {
      const res = await api.saveTranscription(score.id, draft);
      // be defensive about what the endpoint actually echoes back - the
      // edit itself is the source of truth for content/source either way
      transcription = { ...transcription, ...res, content: draft, source: "edited" };
      editorOpen = false;
    } catch (e) {
      saveError = String(e?.message ?? e);
    } finally {
      saving = false;
    }
  }

  async function revertToExtracted() {
    reverting = true;
    fetchError = "";
    try {
      const t = await api.transcribe(score.id);
      transcription = t;
      draft = t.content;
      editorOpen = false;
    } catch (e) {
      fetchError = String(e?.message ?? e);
    } finally {
      reverting = false;
    }
  }
</script>

{#snippet pdfPane()}
  <div class="pane">
    <PdfViewer {score} {gigMode} {onToggleGig} {practiceLabel} {onStopPractice} />
  </div>
{/snippet}

{#snippet staffPane()}
  <div class="pane staff-pane">
    {#if transcriptionState === "loading"}
      <p class="hint">Checking for a transcription…</p>
    {:else if transcriptionState === "error"}
      <div class="empty-state">
        <p class="hint warn">Couldn't reach the transcription service{fetchError ? `: ${fetchError}` : ""}.</p>
        <button onclick={loadTranscription}>Retry</button>
      </div>
    {:else if transcriptionState === "transcribing"}
      <p class="hint">Transcribing…</p>
    {:else if transcriptionState === "none"}
      <div class="empty-state">
        <div class="empty-icon">𝄢</div>
        <h3>No staff transcription yet</h3>
        <p>
          Fermata can pull the guitar tab out of this PDF and render it as a playable,
          editable staff. Fret and string extraction is accurate, but the rhythm and time
          signature are guessed from spacing on the page — treat the staff as a draft to
          check against the PDF, not a verified score.
        </p>
        <div class="ts-input">
          <label>
            Time signature <span class="opt">(optional — helps the rhythm guess)</span>
            <span class="ts-fields">
              <input type="number" min="1" max="32" placeholder="4" bind:value={tsNum} />
              <span class="slash">/</span>
              <input type="number" min="1" max="32" placeholder="4" bind:value={tsDen} />
            </span>
          </label>
        </div>
        {#if fetchError}<p class="hint warn">{fetchError}</p>{/if}
        <button class="primary" onclick={runTranscribe}>Transcribe this PDF</button>
      </div>
    {:else if transcriptionState === "ready"}
      {#if transcription.warnings?.length}
        <div class="warnings">
          <div class="warnings-head">⚠ Unverified — check against the PDF</div>
          <ul>
            {#each transcription.warnings as w}
              <li>{w}</li>
            {/each}
          </ul>
        </div>
      {/if}
      {#if editorOpen}
        <div class="editor">
          <textarea class="editor-input" bind:value={draft} spellcheck="false"></textarea>
          <div class="editor-actions">
            {#if saveError}<span class="hint warn">{saveError}</span>{/if}
            <button class="ghost" onclick={() => (editorOpen = false)}>Cancel</button>
            <button class="primary" disabled={saving} onclick={saveEdit}>
              {saving ? "Saving…" : "Save & render"}
            </button>
          </div>
        </div>
      {/if}
      <div class="staff-render">
        <TabViewer tex={transcription.content} {gigMode} {onToggleGig} {practiceLabel} {onStopPractice} />
      </div>
    {/if}
  </div>
{/snippet}

<div class="compare">
  {#if !gigMode}
    <div class="toolbar">
      <div class="seg">
        <button class:on={layout === "pdf"} onclick={() => setLayout("pdf")}>PDF</button>
        <button class:on={layout === "staff"} onclick={() => setLayout("staff")}>Staff</button>
        <button class:on={layout === "side"} onclick={() => setLayout("side")}>Side by side</button>
      </div>
      {#if transcriptionState === "ready" && layout !== "pdf"}
        <div class="editor-controls">
          <span class="source-badge" class:edited={transcription.source === "edited"}>
            {transcription.source === "edited" ? "edited" : "extracted"}
          </span>
          <button class="ghost" onclick={() => (editorOpen ? (editorOpen = false) : openEditor())}>
            {editorOpen ? "Close editor" : "Edit source"}
          </button>
          {#if transcription.source === "edited"}
            <button class="ghost" disabled={reverting} onclick={revertToExtracted}>
              {reverting ? "Reverting…" : "Revert to extracted"}
            </button>
          {/if}
        </div>
      {/if}
    </div>
  {/if}

  <div class="panes" class:side={layout === "side"}>
    {#if layout === "pdf"}
      {@render pdfPane()}
    {:else if layout === "staff"}
      {@render staffPane()}
    {:else}
      {@render pdfPane()}
      {@render staffPane()}
    {/if}
  </div>
</div>

<style>
  .compare {
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

  .editor-controls {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .ghost {
    background: none;
    border-color: transparent;
    color: var(--ink-dim);
  }

  .ghost:hover {
    border-color: var(--line);
    color: var(--ink);
  }

  .source-badge {
    font-size: 11px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--ink-dim);
    border: 1px solid var(--line);
    border-radius: 99px;
    padding: 2px 9px;
  }

  .source-badge.edited {
    color: var(--brass-bright);
    border-color: var(--brass);
  }

  .panes {
    flex: 1;
    min-height: 0;
    display: flex;
  }

  .panes.side {
    flex-direction: row;
  }

  .pane {
    flex: 1;
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .panes.side .pane + .pane {
    border-left: 1px solid var(--line);
  }

  @media (max-width: 860px) {
    .panes.side {
      flex-direction: column;
    }

    .panes.side .pane + .pane {
      border-left: none;
      border-top: 1px solid var(--line);
    }
  }

  .staff-pane {
    overflow-y: auto;
  }

  .staff-render {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .hint {
    color: var(--ink-dim);
    text-align: center;
    margin: 40px 20px;
  }

  .hint.warn {
    color: var(--danger);
  }

  .empty-state {
    max-width: 420px;
    margin: 40px auto;
    padding: 0 20px;
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 14px;
  }

  .empty-icon {
    font-size: 40px;
    color: var(--brass);
  }

  .empty-state h3 {
    font-size: 17px;
  }

  .empty-state p {
    color: var(--ink-dim);
    font-size: 13.5px;
    line-height: 1.55;
    margin: 0;
  }

  .ts-input {
    width: 100%;
  }

  .ts-input label {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    font-size: 12.5px;
    color: var(--ink-dim);
  }

  .opt {
    font-size: 11px;
    opacity: 0.8;
  }

  .ts-fields {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .ts-fields input {
    width: 52px;
    text-align: center;
  }

  .slash {
    color: var(--ink-dim);
  }

  .warnings {
    margin: 12px 16px 0;
    padding: 10px 14px;
    border: 1px solid var(--danger);
    border-radius: 8px;
    background: rgba(201, 106, 92, 0.12);
  }

  .warnings-head {
    font-size: 13px;
    font-weight: 600;
    color: var(--danger);
  }

  .warnings ul {
    margin: 6px 0 0;
    padding-left: 20px;
    font-size: 12.5px;
    color: var(--ink);
  }

  .warnings li {
    margin: 2px 0;
  }

  .editor {
    margin: 12px 16px 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .editor-input {
    width: 100%;
    min-height: 160px;
    resize: vertical;
    font-family: "SFMono-Regular", Consolas, Menlo, monospace;
    font-size: 12.5px;
    line-height: 1.5;
  }

  .editor-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
  }
</style>
