<script>
  import { api } from "./api.js";
  import PdfViewer from "./PdfViewer.svelte";
  import TabViewer from "./TabViewer.svelte";
  import ScoreCompare from "./ScoreCompare.svelte";

  let { id = null, demo = false } = $props();

  let score = $state(null);
  let error = $state("");
  let editingTags = $state(false);
  let tagsDraft = $state("");

  let viewerEl;
  let gigMode = $state(false);
  let wakeLock = null;
  // gig mode can end (Escape, tap exit) before an in-flight wakeLock.request
  // resolves; wantWakeLock says whether a lock should be held right now, so
  // the resolved request can release itself instead of pinning the screen on
  let wantWakeLock = false;

  async function acquireWakeLock() {
    if (wakeLock) return; // already held - don't overwrite the live sentinel
    if (!("wakeLock" in navigator)) return;
    wantWakeLock = true;
    let lock;
    try {
      lock = await navigator.wakeLock.request("screen");
    } catch {
      return;
    }
    if (!wantWakeLock) {
      // gig mode ended while the request was in flight
      lock.release().catch(() => {});
      return;
    }
    wakeLock = lock;
    wakeLock.addEventListener("release", () => {
      // a stale release from a since-replaced lock must not clobber a newer one
      if (wakeLock === lock) wakeLock = null;
    });
  }

  async function releaseWakeLock() {
    wantWakeLock = false;
    try {
      await wakeLock?.release();
    } catch {
      // already released
    }
    wakeLock = null;
  }

  // guards against a stale enter/exit continuation applying its effects
  // after a later call already changed gig mode (e.g. F then Escape fired
  // in quick succession while requestFullscreen was still pending)
  let gigOp = 0;

  async function enterGigMode() {
    const op = ++gigOp;
    gigMode = true;
    try {
      await viewerEl?.requestFullscreen?.();
    } catch {
      // fullscreen denied or unavailable; gig mode still works windowed
    }
    if (op !== gigOp) {
      // superseded by a later call while fullscreen was still engaging - only
      // undo it if gig mode actually ended up off; a newer enter may have
      // already taken over and must not be clobbered by this stale one
      if (!gigMode && document.fullscreenElement === viewerEl) {
        document.exitFullscreen().catch(() => {});
      }
      return;
    }
    acquireWakeLock();
  }

  async function exitGigMode() {
    ++gigOp;
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
    if (e.ctrlKey || e.metaKey || e.altKey) return; // don't hijack Ctrl+F / Cmd+F
    if (e.repeat) return; // OS key auto-repeat must not spam toggleGigMode
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

  // gig mode hides the header (and the timer button in it), but a running
  // session must stay visible and reachable rather than silently ticking
  // away off-screen - the gig HUDs in both viewers surface this
  let practiceLabel = $derived(practiceStart != null ? formatElapsed(practiceElapsed) : null);

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
          <button
            class="ghost timer"
            class:on={practiceStart != null}
            onclick={practiceStart != null ? flushPractice : startPractice}
            title={practiceStart != null ? "Stop practice timer" : "Start practice timer"}
          >
            {practiceStart != null ? `■ ${formatElapsed(practiceElapsed)}` : "▶ Practice"}
          </button>
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
      <ScoreCompare {score} {gigMode} onToggleGig={toggleGigMode} {practiceLabel} onStopPractice={flushPractice} />
    {:else}
      <TabViewer {score} {gigMode} onToggleGig={toggleGigMode} {practiceLabel} onStopPractice={flushPractice} />
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
