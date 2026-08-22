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
    pollUntilDone();
  }

  function pollUntilDone() {
    const poll = async () => {
      scan = await api.scanStatus();
      if (scan.scanning) setTimeout(poll, 1500);
      else refresh();
    };
    setTimeout(poll, 800);
  }

  // A refused scan is the one thing here that needs a person, so it is the one
  // thing that gets a button. Fermata will not mark scores missing when the
  // evidence looks like a mount problem rather than like somebody tidying up -
  // and without a way to say "I meant it", that refusal would repeat on every
  // scan for ever, because the same files are missing every time.
  let acknowledging = $state(false);
  let acknowledgeError = $state("");

  async function acknowledgeRemovals() {
    if (!scan?.acknowledge_token) return;
    acknowledging = true;
    acknowledgeError = "";
    try {
      await api.acknowledgeScan(scan.acknowledge_token);
      scan = { ...scan, scanning: true, refused: false };
      pollUntilDone();
    } catch (err) {
      // The usual cause is the library having changed again while this message
      // was on screen, which makes the token stale on purpose - saying so is
      // more use than a silent no-op.
      acknowledgeError =
        err?.message ?? "Fermata could not confirm that. Scan again and re-read the message.";
    } finally {
      acknowledging = false;
    }
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
    // A practice DAY (YYYY-MM-DD), which is the day in the practiser's own
    // time - not the UTC timestamp this used to be handed. That mattered
    // because the answer here is a count of calendar days: reading it off a
    // UTC instant put an evening's practice on the next day for anyone west of
    // Greenwich, so "practised today" became "practised 1d ago" at nine at
    // night. The slice also tolerates a timestamp, so an older server (or a
    // row read through some other path) still reads sensibly rather than
    // showing nothing.
    const [year, month, day] = String(lastPracticed).slice(0, 10).split("-").map(Number);
    if (!year || !month || !day) return "";
    const then = new Date(year, month - 1, day);
    if (!Number.isFinite(then.getTime())) return "";
    const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const days = Math.round((startOfDay(new Date()) - startOfDay(then)) / 86400000);
    if (days <= 0) return "practiced today";
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
          {c.collection}
          <span class="count">{c.count}</span>
          {#if c.missing}
            <!-- Files this collection has on record that are not on disk. Shown
                 because a folder that has partly gone used to be counted as
                 though it were whole. -->
            <span class="count missing-count" title="{c.missing} file(s) not found in your library folder">
              {c.missing} missing
            </span>
          {/if}
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
      <a class="demo-link practice-link" href="#/practice">◴ Practice &amp; goals</a>
      <a class="demo-link" href="#/metronome">♩ Metronome</a>
      <a class="demo-link" href="#/demo">Notation/tab demo →</a>
      <a class="demo-link" href="#/settings">⚙ Settings</a>
    </div>
  </aside>

  <main>
    {#if scan?.refused}
      <!-- The scan declined to change anything because what it saw did not look
           like a description of this library. This used to be invisible: the
           status carried `refused` and a reason and nothing rendered either, so
           somebody with 296 of 297 files gone saw a healthy-looking scan, a full
           library, and no hint that anything was wrong. -->
      <div class="alert" role="alert">
        <div class="alert-head">Fermata did not update your library</div>
        <p class="alert-body">{scan.refused_reason}</p>
        {#if scan.unmatched_count}
          <details class="alert-paths">
            <summary>
              {scan.unmatched_count} file{scan.unmatched_count === 1 ? "" : "s"} not found
              {#if scan.unmatched_count > scan.unmatched_paths.length}
                (first {scan.unmatched_paths.length} shown)
              {/if}
            </summary>
            <ul>
              {#each scan.unmatched_paths as path}
                <li>{path}</li>
              {/each}
            </ul>
          </details>
        {/if}
        {#if scan.acknowledge_token}
          <div class="alert-actions">
            <button onclick={acknowledgeRemovals} disabled={acknowledging}>
              {acknowledging ? "Confirming…" : "Yes, I meant to do that"}
            </button>
            <button onclick={triggerScan} disabled={scan.scanning || acknowledging}>
              Scan again
            </button>
          </div>
          <p class="alert-note">
            Confirming never deletes anything. Files that have moved are matched back to
            their own score; anything Fermata cannot find is marked as missing and keeps
            its practice history, tags and transcriptions.
          </p>
        {/if}
        {#if acknowledgeError}
          <p class="alert-error">{acknowledgeError}</p>
        {/if}
      </div>
    {/if}

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
            <a class="card" class:is-missing={score.missing_since} href={"#/score/" + score.id}>
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
                {#if score.missing_since}
                  <!-- The file is not where Fermata last saw it. The SCORE is
                       untouched - its practice history, tags and any
                       hand-corrected transcription are all still attached, and
                       putting the file back (under this name or another) clears
                       this by itself on the next scan. Saying so on the card is
                       what makes "your library is intact, these files are not
                       reachable" visible instead of merely true. -->
                  <span class="missing-flag" title="Fermata cannot find this file. Nothing about the score has been lost - put the file back and scan again.">
                    file missing
                  </span>
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

  /* Amber rather than red: nothing is broken and nothing is lost, the file is
     just not reachable. Red would say "you have lost this", which is the
     opposite of what happened. */
  .missing-flag {
    position: absolute;
    right: 8px;
    bottom: 8px;
    font-size: 11px;
    letter-spacing: 0.04em;
    background: rgba(22, 19, 14, 0.82);
    color: #e8b45c;
    border: 1px solid rgba(232, 180, 92, 0.5);
    padding: 2px 8px;
    border-radius: 99px;
  }

  /* Dimmed, not hidden. The score is still here and still opens; only the file
     behind it is unreachable, so the card stays reachable too. */
  .card.is-missing .sheet {
    opacity: 0.45;
  }

  .card.is-missing .title {
    color: var(--ink-dim);
  }

  .missing-count {
    color: #e8b45c;
    margin-left: 4px;
  }

  .alert {
    border: 1px solid rgba(232, 180, 92, 0.55);
    background: rgba(232, 180, 92, 0.08);
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 18px;
  }

  .alert-head {
    color: #e8b45c;
    font-weight: 600;
    margin-bottom: 6px;
  }

  .alert-body {
    /* The reason text is written as prose with paragraph breaks in it, and it is
       the same sentence the log carries. Preserving the breaks is what keeps it
       readable rather than one long run. */
    white-space: pre-line;
    margin: 0 0 10px;
    color: var(--ink);
  }

  .alert-paths {
    margin-bottom: 10px;
    color: var(--ink-dim);
    font-size: 13px;
  }

  .alert-paths ul {
    margin: 6px 0 0;
    padding-left: 20px;
    max-height: 220px;
    overflow-y: auto;
  }

  .alert-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .alert-note {
    color: var(--ink-dim);
    font-size: 13px;
    margin: 8px 0 0;
  }

  .alert-error {
    color: #e8b45c;
    font-size: 13px;
    margin: 8px 0 0;
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
