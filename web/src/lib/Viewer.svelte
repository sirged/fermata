<script>
  import { api } from "./api.js";
  import PdfViewer from "./PdfViewer.svelte";
  import TabViewer from "./TabViewer.svelte";

  let { id = null, demo = false } = $props();

  let score = $state(null);
  let error = $state("");
  let editingTags = $state(false);
  let tagsDraft = $state("");

  $effect(() => {
    if (demo || id == null) return;
    api
      .score(id)
      .then((s) => (score = s))
      .catch((e) => (error = String(e)));
  });

  const PRACTICE_MIN_SECONDS = 10;

  let practiceStart = $state(null);
  let practiceElapsed = $state(0);
  let practiceInterval;
  let practiceScoreId = null;

  function formatElapsed(sec) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function startPractice() {
    if (!score) return;
    practiceScoreId = score.id;
    practiceStart = Date.now();
    practiceElapsed = 0;
    practiceInterval = setInterval(() => {
      practiceElapsed = Math.floor((Date.now() - practiceStart) / 1000);
    }, 1000);
  }

  function flushPractice() {
    if (practiceStart == null) return;
    const seconds = Math.floor((Date.now() - practiceStart) / 1000);
    const scoreId = practiceScoreId;
    clearInterval(practiceInterval);
    practiceStart = null;
    practiceElapsed = 0;
    practiceScoreId = null;
    if (seconds >= PRACTICE_MIN_SECONDS && scoreId != null) {
      api.logPractice(scoreId, { seconds }).catch(() => {});
    }
  }

  // Flushes on switching to a different score too: the route swaps `id` on
  // this same component instance rather than remounting it.
  $effect(() => {
    void id;
    return () => flushPractice();
  });

  $effect(() => {
    window.addEventListener("beforeunload", flushPractice);
    return () => window.removeEventListener("beforeunload", flushPractice);
  });

  async function setKind(ev) {
    score = await api.patch(score.id, { content_kind: ev.target.value });
  }

  async function toggleFavorite() {
    score = await api.patch(score.id, { favorite: !score.favorite });
  }

  function startTagEdit() {
    tagsDraft = score.tags.join(", ");
    editingTags = true;
  }

  async function saveTags() {
    score = await api.patch(score.id, {
      tags: tagsDraft.split(",").map((t) => t.trim()).filter(Boolean),
    });
    editingTags = false;
  }
</script>

<div class="viewer">
  <header>
    <a class="back" href="#/">← Library</a>
    {#if demo}
      <div class="titles">
        <span class="title">Notation & Tab Demo</span>
        <span class="sub">bundled sample</span>
      </div>
    {:else if score}
      <div class="titles">
        <span class="title">{score.title}</span>
        <span class="sub">
          {[score.composer, score.source].filter(Boolean).join(" · ")}
        </span>
      </div>
      <div class="controls">
        {#if editingTags}
          <input
            class="tags-input"
            bind:value={tagsDraft}
            placeholder="tag, another tag"
            onkeydown={(e) => e.key === "Enter" && saveTags()}
          />
          <button onclick={saveTags}>Save</button>
        {:else}
          <button class="ghost" onclick={startTagEdit}>
            {score.tags.length ? score.tags.join(" · ") : "+ tags"}
          </button>
        {/if}
        <select value={score.content_kind} onchange={setKind} title="Content type">
          <option value="unknown">unsorted</option>
          <option value="notation">notation</option>
          <option value="tab">tab</option>
          <option value="both">notation + tab</option>
        </select>
        <button
          class="ghost timer"
          class:on={practiceStart != null}
          onclick={practiceStart != null ? flushPractice : startPractice}
          title={practiceStart != null ? "Stop practice timer" : "Start practice timer"}
        >
          {practiceStart != null ? `■ ${formatElapsed(practiceElapsed)}` : "▶ Practice"}
        </button>
        <button class="ghost fav" class:on={score.favorite} onclick={toggleFavorite}>★</button>
      </div>
    {/if}
  </header>

  {#if error}
    <p class="error">{error}</p>
  {:else if demo}
    <TabViewer demo={true} />
  {:else if score}
    {#if score.file_type === "pdf"}
      <PdfViewer {score} />
    {:else}
      <TabViewer {score} />
    {/if}
  {/if}
</div>

<style>
  .viewer {
    display: flex;
    flex-direction: column;
    height: 100vh;
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

  .titles {
    display: flex;
    align-items: baseline;
    gap: 10px;
    min-width: 0;
    flex: 1;
  }

  .title {
    font-family: var(--font-display);
    font-size: 18px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .sub {
    color: var(--ink-dim);
    font-size: 13px;
    white-space: nowrap;
  }

  .controls {
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

  .fav.on {
    color: var(--brass-bright);
  }

  .timer {
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .timer.on {
    color: var(--brass-bright);
    border-color: var(--brass);
  }

  .tags-input {
    width: 220px;
  }

  .error {
    color: var(--danger);
    text-align: center;
    margin-top: 60px;
  }
</style>
