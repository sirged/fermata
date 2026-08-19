<script>
  import { api } from "./api.js";
  import PdfViewer from "./PdfViewer.svelte";
  import TabViewer from "./TabViewer.svelte";

  let { id = null, demo = false } = $props();

  let score = $state(null);
  let error = $state("");
  let editingTags = $state(false);
  let tagsDraft = $state("");

  let viewerEl;
  let gigMode = $state(false);
  let wakeLock = null;

  async function acquireWakeLock() {
    if (!("wakeLock" in navigator)) return;
    try {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => (wakeLock = null));
    } catch {
      wakeLock = null;
    }
  }

  async function releaseWakeLock() {
    try {
      await wakeLock?.release();
    } catch {
      // already released
    }
    wakeLock = null;
  }

  async function enterGigMode() {
    gigMode = true;
    try {
      await viewerEl?.requestFullscreen?.();
    } catch {
      // fullscreen denied or unavailable; gig mode still works windowed
    }
    acquireWakeLock();
  }

  async function exitGigMode() {
    gigMode = false;
    if (document.fullscreenElement === viewerEl) {
      try {
        await document.exitFullscreen();
      } catch {
        // ignore
      }
    }
    releaseWakeLock();
  }

  function toggleGigMode() {
    if (gigMode) exitGigMode();
    else enterGigMode();
  }

  function onFullscreenChange() {
    // the browser may drop fullscreen without going through exitGigMode
    // (Escape, OS gesture, etc) - keep gig mode in sync so the header
    // doesn't stay hidden with no way back to it
    if (gigMode && document.fullscreenElement !== viewerEl) {
      gigMode = false;
      releaseWakeLock();
    }
  }

  function onVisibilityChange() {
    if (gigMode && document.visibilityState === "visible") acquireWakeLock();
  }

  function onKey(e) {
    const tag = e.target?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (e.key === "f" || e.key === "F") {
      e.preventDefault();
      toggleGigMode();
    } else if (e.key === "Escape" && gigMode) {
      e.preventDefault();
      exitGigMode();
    }
  }

  $effect(() => {
    return () => releaseWakeLock();
  });

  $effect(() => {
    if (demo || id == null) return;
    api
      .score(id)
      .then((s) => (score = s))
      .catch((e) => (error = String(e)));
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

<svelte:window onkeydown={onKey} onfullscreenchange={onFullscreenChange} onvisibilitychange={onVisibilityChange} />

<div class="viewer" bind:this={viewerEl}>
  {#if !gigMode}
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
          <button class="ghost fav" class:on={score.favorite} onclick={toggleFavorite}>★</button>
          <button class="ghost" onclick={enterGigMode} title="Distraction-free performance view (F)">
            ⛶ Gig mode
          </button>
        </div>
      {/if}
    </header>
  {/if}

  {#if error}
    <p class="error">{error}</p>
  {:else if demo}
    <TabViewer demo={true} />
  {:else if score}
    {#if score.file_type === "pdf"}
      <PdfViewer {score} {gigMode} onToggleGig={toggleGigMode} />
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

  .tags-input {
    width: 220px;
  }

  .error {
    color: var(--danger);
    text-align: center;
    margin-top: 60px;
  }
</style>
