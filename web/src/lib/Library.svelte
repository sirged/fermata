<script>
  import { api } from "./api.js";

  let scores = $state([]);
  let collections = $state([]);
  let tags = $state([]);
  let search = $state("");
  let collection = $state("");
  let kind = $state("");
  let tag = $state("");
  let favorite = $state(false);
  let practiced = $state("");
  let scan = $state(null);
  let loading = $state(true);
  let uploadInput;
  let showDuplicates = $state(false);
  let duplicates = $state([]);

  const KINDS = [
    ["", "All"],
    ["notation", "Notation"],
    ["tab", "Tab"],
    ["both", "Notation + Tab"],
    ["unknown", "Unsorted"],
  ];

  async function refresh() {
    loading = true;
    try {
      [scores, collections, tags] = await Promise.all([
        api.scores({ search, collection, kind, tag, favorite, practiced }),
        api.collections(),
        api.tags(),
      ]);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    // re-query whenever a filter changes
    void search, collection, kind, tag, favorite, practiced;
    const t = setTimeout(refresh, 150);
    return () => clearTimeout(t);
  });

  $effect(() => {
    if (!showDuplicates) return;
    api.duplicates().then((d) => (duplicates = d));
  });

  $effect(() => {
    let timer;
    async function poll() {
      scan = await api.scanStatus();
      if (scan.scanning) {
        timer = setTimeout(poll, 1500);
      } else {
        refresh();
      }
    }
    api.scanStatus().then((s) => {
      scan = s;
      if (s.scanning) poll();
    });
    return () => clearTimeout(timer);
  });

  async function triggerScan() {
    await api.scan();
    scan = { ...(scan ?? {}), scanning: true };
    const poll = async () => {
      scan = await api.scanStatus();
      if (scan.scanning) setTimeout(poll, 1500);
      else refresh();
    };
    setTimeout(poll, 800);
  }

  async function onUpload(ev) {
    const files = [...ev.target.files];
    for (const f of files) await api.upload(f);
    ev.target.value = "";
    setTimeout(refresh, 1200);
  }

  async function toggleFavorite(score, ev) {
    ev.preventDefault();
    ev.stopPropagation();
    const updated = await api.patch(score.id, { favorite: !score.favorite });
    scores = scores.map((s) => (s.id === score.id ? updated : s));
  }

  const kindLabel = { notation: "notation", tab: "tab", both: "notation + tab", unknown: "" };

  function practicedAgo(lastPracticed) {
    if (!lastPracticed) return "";
    const iso = lastPracticed.replace(" ", "T") + "Z";
    const then = new Date(iso);
    if (!Number.isFinite(then.getTime())) return "";
    // Compare calendar dates (in local time), not raw elapsed milliseconds,
    // so e.g. 23:00 yesterday reads as 1 day ago rather than "today".
    const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const days = Math.round((startOfDay(new Date()) - startOfDay(then)) / 86400000);
    if (days <= 0) return "practiced <24h ago";
    if (days === 1) return "practiced 1d ago";
    return `practiced ${days}d ago`;
  }
</script>

<div class="layout">
  <aside>
    <div class="brand">
      <svg viewBox="0 0 32 32" width="30" height="30" aria-hidden="true">
        <path d="M4 22 A12 12 0 0 1 28 22" fill="none" stroke="var(--brass)" stroke-width="3" stroke-linecap="round" />
        <circle cx="16" cy="22" r="3" fill="var(--brass)" />
      </svg>
      <h1>fermata</h1>
    </div>

    <nav>
      <button class="side-item" class:active={!collection && !favorite && !practiced && !showDuplicates} onclick={() => { collection = ""; favorite = false; practiced = ""; showDuplicates = false; }}>
        All scores
      </button>
      <button class="side-item" class:active={favorite} onclick={() => { favorite = !favorite; showDuplicates = false; }}>
        ★ Favorites
      </button>
      <button class="side-item" class:active={practiced === "recent"} onclick={() => { practiced = practiced === "recent" ? "" : "recent"; showDuplicates = false; }}>
        ◷ Recently practiced
      </button>
      <button class="side-item" class:active={practiced === "neglected"} onclick={() => { practiced = practiced === "neglected" ? "" : "neglected"; showDuplicates = false; }}>
        ⌛ Needs attention
      </button>
      <button class="side-item" class:active={showDuplicates} onclick={() => (showDuplicates = !showDuplicates)}>
        ⧉ Duplicates
      </button>

      <div class="side-label">Collections</div>
      {#each collections as c}
        <button
          class="side-item"
          class:active={collection === c.collection}
          onclick={() => { collection = collection === c.collection ? "" : c.collection; showDuplicates = false; }}
        >
          {c.collection} <span class="count">{c.count}</span>
        </button>
      {/each}

      {#if tags.length}
        <div class="side-label">Tags</div>
        <div class="tag-cloud">
          {#each tags as t}
            <button class="chip" class:active={tag === t.name} onclick={() => { tag = tag === t.name ? "" : t.name; showDuplicates = false; }}>
              {t.name}
            </button>
          {/each}
        </div>
      {/if}
    </nav>

    <div class="side-actions">
      <button onclick={() => uploadInput.click()}>Upload</button>
      <input
        bind:this={uploadInput}
        type="file"
        accept=".pdf,.musicxml,.mxl,.gp,.gp3,.gp4,.gp5,.gpx"
        multiple
        hidden
        onchange={onUpload}
      />
      <button onclick={triggerScan} disabled={scan?.scanning}>
        {scan?.scanning ? `Scanning ${scan.processed}/${scan.total}…` : "Scan library"}
      </button>
      <a class="demo-link" href="#/demo">Notation/tab demo →</a>
      <a class="demo-link" href="#/settings">⚙ Settings</a>
    </div>
  </aside>

  <main>
    {#if showDuplicates}
      <header>
        <span class="result-count">{duplicates.length} duplicate group{duplicates.length === 1 ? "" : "s"}</span>
      </header>

      {#if !duplicates.length}
        <p class="empty">No duplicates found — every score in your library is unique.</p>
      {:else}
        <div class="dupe-list">
          {#each duplicates as group (group.hash)}
            <div class="dupe-group">
              <div class="dupe-head">{group.count} copies — {group.scores[0]?.title ?? "Untitled"}</div>
              <div class="dupe-paths">
                {#each group.scores as s (s.id)}
                  <a class="dupe-path" href={"#/score/" + s.id}>{s.path}</a>
                {/each}
              </div>
            </div>
          {/each}
        </div>
      {/if}
    {:else}
      <header>
        <input class="search" type="search" placeholder="Search title, composer, source…" bind:value={search} />
        <select bind:value={kind}>
          {#each KINDS as [value, label]}
            <option {value}>{label}</option>
          {/each}
        </select>
        <span class="result-count">{scores.length} score{scores.length === 1 ? "" : "s"}</span>
      </header>

      {#if loading && !scores.length}
        <p class="empty">Loading…</p>
      {:else if !scores.length}
        <p class="empty">
          Nothing here yet. Drop files into your library folder and hit <em>Scan library</em>,
          or use <em>Upload</em>.
        </p>
      {:else}
        <div class="grid">
          {#each scores as score (score.id)}
            <a class="card" href={"#/score/" + score.id}>
              <div class="sheet">
                {#if score.file_type === "pdf"}
                  <img src={api.thumbUrl(score.id)} alt="" loading="lazy" onerror={(e) => (e.target.style.display = "none")} />
                {:else}
                  <div class="sheet-icon">𝄞</div>
                {/if}
                <button class="fav" class:on={score.favorite} onclick={(e) => toggleFavorite(score, e)} title="Favorite">★</button>
                {#if kindLabel[score.content_kind]}
                  <span class="kind">{kindLabel[score.content_kind]}</span>
                {/if}
              </div>
              <div class="meta">
                <div class="title">{score.title}</div>
                <div class="sub">{score.source ?? score.composer ?? score.collection ?? ""}</div>
                {#if score.last_practiced}
                  <div class="practiced">{practicedAgo(score.last_practiced)}</div>
                {/if}
              </div>
            </a>
          {/each}
        </div>
      {/if}
    {/if}
  </main>
</div>

<style>
  .layout {
    display: grid;
    grid-template-columns: 250px 1fr;
    height: 100vh;
  }

  aside {
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--line);
    background: var(--bg-raised);
    padding: 18px 14px;
    overflow-y: auto;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 2px 8px 16px;
  }

  .brand h1 {
    font-size: 26px;
    font-weight: 600;
    letter-spacing: 0.5px;
    color: var(--brass-bright);
  }

  nav {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .side-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--ink-dim);
    margin: 16px 8px 6px;
  }

  .side-item {
    text-align: left;
    background: none;
    border: none;
    padding: 7px 10px;
    border-radius: 8px;
    color: var(--ink);
    display: flex;
    justify-content: space-between;
    gap: 8px;
  }

  .side-item:hover {
    background: var(--surface);
  }

  .side-item.active {
    background: var(--surface);
    color: var(--brass-bright);
  }

  .count {
    color: var(--ink-dim);
    font-size: 12px;
  }

  .tag-cloud {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 0 8px;
  }

  .chip {
    font-size: 12px;
    padding: 3px 10px;
    border-radius: 99px;
  }

  .chip.active {
    background: var(--brass);
    color: #241d0f;
    border-color: var(--brass);
  }

  .side-actions {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding-top: 16px;
  }

  .demo-link {
    font-size: 13px;
    text-align: center;
    color: var(--ink-dim);
  }

  .demo-link:hover {
    color: var(--brass-bright);
  }

  main {
    overflow-y: auto;
    padding: 20px 28px 40px;
  }

  header {
    display: flex;
    align-items: center;
    gap: 12px;
    position: sticky;
    top: 0;
    padding: 8px 0 14px;
    background: linear-gradient(var(--bg) 75%, transparent);
    z-index: 2;
  }

  .search {
    flex: 1;
    max-width: 420px;
  }

  .result-count {
    color: var(--ink-dim);
    font-size: 13px;
    margin-left: auto;
  }

  .empty {
    color: var(--ink-dim);
    margin-top: 60px;
    text-align: center;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 22px;
  }

  .dupe-list {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .dupe-group {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 12px 16px;
  }

  .dupe-head {
    font-size: 14px;
    margin-bottom: 8px;
  }

  .dupe-paths {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .dupe-path {
    display: block;
    font-family: "SFMono-Regular", Consolas, Menlo, monospace;
    font-size: 12px;
    color: var(--ink-dim);
  }

  .dupe-path:hover {
    color: var(--brass-bright);
  }

  .card {
    color: var(--ink);
    display: block;
  }

  .sheet {
    position: relative;
    aspect-ratio: 3 / 4;
    background: var(--paper);
    border-radius: 6px;
    overflow: hidden;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.5), 0 10px 24px rgba(0, 0, 0, 0.35);
    transition: transform 0.18s ease, box-shadow 0.18s ease;
  }

  .card:hover .sheet {
    transform: translateY(-4px) rotate(-0.4deg);
    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.5), 0 18px 36px rgba(0, 0, 0, 0.45);
  }

  .sheet img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: top;
  }

  .sheet-icon {
    display: grid;
    place-items: center;
    height: 100%;
    font-size: 64px;
    color: #6b5d3f;
  }

  .fav {
    position: absolute;
    top: 8px;
    right: 8px;
    border: none;
    background: rgba(22, 19, 14, 0.65);
    color: rgba(240, 232, 214, 0.55);
    border-radius: 99px;
    width: 30px;
    height: 30px;
    padding: 0;
    font-size: 15px;
    opacity: 0;
    transition: opacity 0.15s;
  }

  .card:hover .fav,
  .fav.on {
    opacity: 1;
  }

  .fav.on {
    color: var(--brass-bright);
  }

  .kind {
    position: absolute;
    left: 8px;
    bottom: 8px;
    font-size: 11px;
    letter-spacing: 0.04em;
    background: rgba(22, 19, 14, 0.75);
    color: var(--brass-bright);
    padding: 2px 8px;
    border-radius: 99px;
  }

  .meta {
    padding: 8px 2px 0;
  }

  .title {
    font-family: var(--font-display);
    font-size: 15px;
    line-height: 1.25;
  }

  .sub {
    font-size: 12.5px;
    color: var(--ink-dim);
    margin-top: 2px;
  }

  .practiced {
    font-size: 11px;
    color: var(--ink-dim);
    opacity: 0.7;
    margin-top: 3px;
  }
</style>
