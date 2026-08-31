<script>
  // Setlists (issue #6): ordered collections of scores a player works through -
  // a gig set, a lesson plan, a practice rotation.
  //
  // WHAT THIS PAGE OBEYS, since it is where the invariants are easiest to break:
  //
  //   The order is the server's, never this page's. Members are rendered in the
  //   order they arrive and never sorted here; moving one up sends the whole new
  //   order to the reorder endpoint and re-renders from what comes back. So the
  //   thing on screen is always the stored order, and a reorder is a real write,
  //   not a local rearrangement that a reload would forget.
  //
  //   Removing a score from a setlist, and deleting a setlist, never delete a
  //   score. Both go through endpoints that say so; the wording here says so too
  //   ("Remove from setlist", and the delete confirmation states the scores are
  //   kept), so nothing invites a person to believe otherwise.
  //
  //   A trashed score (#56) stays in the setlist, MARKED, and is not a link. A
  //   member whose score is in the trash is drawn with a "in trash" mark and no
  //   way to open it - the score is not in the library to open - rather than
  //   dropped from the list or linked to a dead end.
  //
  //   Practising a setlist reuses the real viewer. "Open" and "Start
  //   practising" navigate to the ordinary score viewer, which is where practice
  //   is logged; there is no separate player here to drift from that one.
  import { api, ApiError } from "./api.js";
  import { formatDuration, shortDate } from "./practice.js";

  // null on the list page, a setlist id on a detail page. App.svelte parses the
  // hash and hands it in, so the two views are two routes and each is
  // deep-linkable.
  let { id = null } = $props();

  let setlists = $state([]); // list view
  let detail = $state(null); // detail view
  let loading = $state(true);
  let error = $state("");

  // List view: the name being typed for a new setlist.
  let newName = $state("");
  let creating = $state(false);

  // Detail view state.
  let renaming = $state(false);
  let nameDraft = $state("");
  let confirmingDelete = $state(false);
  let busy = $state(false); // a membership/order write is in flight
  let adding = $state(false); // the add-scores panel is open
  let candidates = $state([]); // library scores not already in this setlist
  let candidateSearch = $state("");

  async function load() {
    loading = true;
    error = "";
    try {
      if (id == null) {
        setlists = await api.setlists();
      } else {
        detail = await api.setlist(id);
        nameDraft = detail.name;
      }
    } catch (e) {
      error = e instanceof ApiError && e.message ? e.message : "Could not load setlists.";
    } finally {
      loading = false;
    }
  }

  // Re-run whenever the route id changes (navigating list <-> detail without a
  // full reload).
  $effect(() => {
    id;
    load();
  });

  // --- List view ------------------------------------------------------------

  async function create() {
    const name = newName.trim();
    if (!name || creating) return;
    creating = true;
    error = "";
    try {
      const made = await api.createSetlist(name);
      newName = "";
      // Straight into the new setlist, which is where you add scores.
      location.hash = `#/setlists/${made.id}`;
    } catch (e) {
      error = e instanceof ApiError && e.message ? e.message : "Could not create the setlist.";
    } finally {
      creating = false;
    }
  }

  // --- Detail view ----------------------------------------------------------

  // The live (not-trashed) members, in order - what "Start practising" walks and
  // what a reorder is expressed against remains ALL members (see reorder()).
  const liveMembers = $derived((detail?.scores ?? []).filter((m) => !m.score.deleted_at));

  function memberIds() {
    return (detail?.scores ?? []).map((m) => m.score.id);
  }

  async function rename() {
    const name = nameDraft.trim();
    if (!name) return;
    busy = true;
    error = "";
    try {
      detail = await api.renameSetlist(id, name);
      renaming = false;
    } catch (e) {
      error = e instanceof ApiError && e.message ? e.message : "Could not rename the setlist.";
    } finally {
      busy = false;
    }
  }

  async function remove(scoreId) {
    if (busy) return;
    busy = true;
    error = "";
    try {
      detail = await api.removeFromSetlist(id, scoreId);
    } catch (e) {
      error = e instanceof ApiError && e.message ? e.message : "Could not remove that score.";
    } finally {
      busy = false;
    }
  }

  async function move(index, delta) {
    if (busy) return;
    const ids = memberIds();
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    busy = true;
    error = "";
    try {
      detail = await api.reorderSetlist(id, ids);
    } catch (e) {
      error = e instanceof ApiError && e.message ? e.message : "Could not reorder the setlist.";
    } finally {
      busy = false;
    }
  }

  async function destroy() {
    if (busy) return;
    busy = true;
    error = "";
    try {
      await api.deleteSetlist(id);
      location.hash = "#/setlists";
    } catch (e) {
      error = e instanceof ApiError && e.message ? e.message : "Could not delete the setlist.";
      busy = false;
    }
  }

  async function openAdd() {
    adding = true;
    error = "";
    try {
      const all = await api.scores();
      const inSetlist = new Set(memberIds());
      // api.scores() already excludes trashed scores, so a candidate is always
      // a live score not already a member.
      candidates = all.filter((s) => !inSetlist.has(s.id));
    } catch (e) {
      error = e instanceof ApiError && e.message ? e.message : "Could not load your library.";
    }
  }

  async function add(scoreId) {
    if (busy) return;
    busy = true;
    error = "";
    try {
      detail = await api.addToSetlist(id, scoreId);
      candidates = candidates.filter((s) => s.id !== scoreId);
    } catch (e) {
      error = e instanceof ApiError && e.message ? e.message : "Could not add that score.";
    } finally {
      busy = false;
    }
  }

  const shownCandidates = $derived(
    candidateSearch.trim()
      ? candidates.filter((s) =>
          `${s.title} ${s.composer ?? ""}`.toLowerCase().includes(candidateSearch.trim().toLowerCase()),
        )
      : candidates,
  );

  function memberProgress(score) {
    const total = formatDuration(score.practice_seconds || 0);
    if (score.practice_seconds) {
      return score.last_practiced
        ? `${total} · last played ${shortDate(score.last_practiced)}`
        : total;
    }
    return "not practised yet";
  }
</script>

{#if id == null}
  <!-- ================= LIST VIEW ================= -->
  <div class="setlists" data-setlist-count={setlists.length}>
    <header>
      <a class="back" href="#/">← Library</a>
      <h1>Setlists</h1>
    </header>

    <main>
      {#if error}
        <p class="notice" role="status">{error}</p>
      {/if}

      <form
        class="create-setlist"
        onsubmit={(e) => {
          e.preventDefault();
          create();
        }}
      >
        <input
          class="new-name"
          type="text"
          placeholder="Name a new setlist…"
          maxlength="200"
          bind:value={newName}
        />
        <button type="submit" class="create" disabled={!newName.trim() || creating}>
          {creating ? "Creating…" : "Create"}
        </button>
      </form>

      {#if loading}
        <p class="quiet">Loading…</p>
      {:else if setlists.length === 0}
        <p class="quiet empty">
          No setlists yet. A setlist is an ordered group of pieces to work through — a gig set,
          a lesson plan, a practice rotation. Name one above to start.
        </p>
      {:else}
        <ul class="setlist-list">
          {#each setlists as s (s.id)}
            <li class="setlist-row">
              <a class="setlist-link" href={`#/setlists/${s.id}`}>
                <span class="setlist-name">{s.name}</span>
                <span class="setlist-count"
                  >{s.score_count} {s.score_count === 1 ? "score" : "scores"}</span
                >
              </a>
            </li>
          {/each}
        </ul>
      {/if}
    </main>
  </div>
{:else}
  <!-- ================= DETAIL VIEW ================= -->
  <div class="setlist-detail">
    <header>
      <a class="back" href="#/setlists">← Setlists</a>
      {#if renaming}
        <form
          class="rename"
          onsubmit={(e) => {
            e.preventDefault();
            rename();
          }}
        >
          <input class="name-draft" type="text" maxlength="200" bind:value={nameDraft} />
          <button type="submit" class="save-name" disabled={!nameDraft.trim() || busy}>Save</button>
          <button
            type="button"
            class="cancel-name"
            onclick={() => {
              renaming = false;
              nameDraft = detail?.name ?? "";
            }}>Cancel</button
          >
        </form>
      {:else}
        <h1 class="setlist-title">{detail?.name ?? "Setlist"}</h1>
        {#if detail}
          <button class="rename-setlist" onclick={() => (renaming = true)}>Rename</button>
        {/if}
      {/if}
    </header>

    <main>
      {#if error}
        <p class="notice" role="status">{error}</p>
      {/if}

      {#if loading}
        <p class="quiet">Loading…</p>
      {:else if !detail}
        <p class="quiet">This setlist could not be loaded. Reload to try again.</p>
      {:else}
        <div class="detail-actions">
          {#if liveMembers.length}
            <a class="start-practising" href={`#/score/${liveMembers[0].score.id}`}
              >▶ Start practising</a
            >
          {/if}
          <button class="add-scores" onclick={openAdd} disabled={adding}>Add scores</button>
          {#if confirmingDelete}
            <span class="confirm-delete">
              Delete this setlist? Your scores are kept.
              <button class="confirm-delete-yes" onclick={destroy} disabled={busy}>Delete</button>
              <button class="confirm-delete-no" onclick={() => (confirmingDelete = false)}
                >Keep</button
              >
            </span>
          {:else}
            <button class="delete-setlist" onclick={() => (confirmingDelete = true)}
              >Delete setlist</button
            >
          {/if}
        </div>

        {#if detail.scores.length === 0}
          <p class="quiet empty">
            This setlist is empty. Add scores to build the order you want to work through.
          </p>
        {:else}
          <ol class="members" data-member-count={detail.scores.length}>
            {#each detail.scores as m, i (m.score.id)}
              <li class="member" class:deleted={!!m.score.deleted_at} data-score-id={m.score.id}>
                <span class="member-order">{i + 1}</span>
                <span class="member-body">
                  {#if m.score.deleted_at}
                    <span class="member-title">{m.score.title}</span>
                    <span class="member-deleted" title="This score is in the trash."
                      >in trash</span
                    >
                  {:else}
                    <a class="member-title member-open" href={`#/score/${m.score.id}`}
                      >{m.score.title}</a
                    >
                  {/if}
                  <span class="member-progress">{memberProgress(m.score)}</span>
                </span>
                <span class="member-controls">
                  <button
                    class="reorder-up"
                    title="Move up"
                    onclick={() => move(i, -1)}
                    disabled={busy || i === 0}>↑</button
                  >
                  <button
                    class="reorder-down"
                    title="Move down"
                    onclick={() => move(i, 1)}
                    disabled={busy || i === detail.scores.length - 1}>↓</button
                  >
                  <button
                    class="remove-member"
                    title="Remove from setlist"
                    onclick={() => remove(m.score.id)}
                    disabled={busy}>Remove</button
                  >
                </span>
              </li>
            {/each}
          </ol>
        {/if}

        {#if adding}
          <section class="add-panel">
            <div class="add-panel-head">
              <h2>Add scores</h2>
              <button class="close-add" onclick={() => (adding = false)}>Done</button>
            </div>
            <input
              class="candidate-search"
              type="text"
              placeholder="Search your library…"
              bind:value={candidateSearch}
            />
            {#if shownCandidates.length === 0}
              <p class="quiet">
                {candidates.length === 0
                  ? "Every score in your library is already in this setlist."
                  : "No scores match that search."}
              </p>
            {:else}
              <ul class="candidates">
                {#each shownCandidates as s (s.id)}
                  <li class="add-candidate" data-score-id={s.id}>
                    <span class="candidate-title">{s.title}</span>
                    {#if s.composer}<span class="candidate-composer">{s.composer}</span>{/if}
                    <button class="add-candidate-btn" onclick={() => add(s.id)} disabled={busy}
                      >Add</button
                    >
                  </li>
                {/each}
              </ul>
            {/if}
          </section>
        {/if}
      {/if}
    </main>
  </div>
{/if}

<style>
  .setlists,
  .setlist-detail {
    max-width: 820px;
    margin: 0 auto;
    padding: 1.5rem 1.25rem 4rem;
    color: var(--ink);
  }

  header {
    display: flex;
    align-items: baseline;
    gap: 1rem;
    flex-wrap: wrap;
    margin-bottom: 1.5rem;
  }

  .back {
    color: var(--ink-dim);
    text-decoration: none;
    font-family: var(--font-ui);
    font-size: 0.95rem;
  }
  .back:hover {
    color: var(--brass-bright);
  }

  h1 {
    font-family: var(--font-display);
    font-weight: 500;
    margin: 0;
    font-size: 1.7rem;
  }

  .quiet {
    color: var(--ink-dim);
    font-family: var(--font-ui);
  }
  .empty {
    line-height: 1.5;
    max-width: 46ch;
  }

  .notice {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 0.6rem 0.9rem;
    font-family: var(--font-ui);
    margin-bottom: 1rem;
  }

  button,
  .start-practising,
  .create {
    font-family: var(--font-ui);
    cursor: pointer;
  }
  button {
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 0.4rem 0.75rem;
    font-size: 0.9rem;
  }
  button:hover:not(:disabled) {
    border-color: var(--brass);
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }

  /* --- list view --- */
  .create-setlist {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1.5rem;
  }
  input[type="text"] {
    flex: 1;
    background: var(--bg-raised);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 0.45rem 0.7rem;
    font-family: var(--font-ui);
    font-size: 0.95rem;
  }
  input[type="text"]:focus {
    outline: none;
    border-color: var(--brass);
  }
  .create {
    background: var(--brass);
    color: var(--bg);
    border: none;
    border-radius: var(--radius);
    padding: 0.45rem 1.1rem;
    font-weight: 600;
  }

  .setlist-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .setlist-link {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
    padding: 0.9rem 1rem;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    text-decoration: none;
    color: var(--ink);
  }
  .setlist-link:hover {
    border-color: var(--brass);
  }
  .setlist-name {
    font-family: var(--font-display);
    font-size: 1.15rem;
  }
  .setlist-count {
    color: var(--ink-dim);
    font-family: var(--font-ui);
    font-size: 0.85rem;
  }

  /* --- detail view --- */
  .setlist-title {
    font-size: 1.5rem;
  }
  .rename {
    display: flex;
    gap: 0.5rem;
    flex: 1;
  }
  .detail-actions {
    display: flex;
    gap: 0.6rem;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 1.25rem;
  }
  .start-practising {
    background: var(--brass);
    color: var(--bg);
    border-radius: var(--radius);
    padding: 0.45rem 1rem;
    font-weight: 600;
    text-decoration: none;
  }
  .start-practising:hover {
    background: var(--brass-bright);
  }
  .delete-setlist {
    margin-left: auto;
    color: var(--danger);
    border-color: var(--danger);
  }
  .confirm-delete {
    margin-left: auto;
    display: inline-flex;
    gap: 0.5rem;
    align-items: center;
    font-family: var(--font-ui);
    font-size: 0.85rem;
    color: var(--ink-dim);
  }
  .confirm-delete-yes {
    color: var(--danger);
    border-color: var(--danger);
  }

  .members {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .member {
    display: flex;
    align-items: center;
    gap: 0.85rem;
    padding: 0.7rem 0.9rem;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
  }
  .member.deleted {
    opacity: 0.65;
  }
  .member-order {
    font-family: var(--font-ui);
    color: var(--ink-dim);
    width: 1.5rem;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .member-body {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    min-width: 0;
  }
  .member-title {
    font-family: var(--font-display);
    font-size: 1.05rem;
    color: var(--ink);
    text-decoration: none;
  }
  a.member-open:hover {
    color: var(--brass-bright);
  }
  .member-deleted {
    font-family: var(--font-ui);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--danger);
    margin-left: 0.5rem;
  }
  .member-progress {
    font-family: var(--font-ui);
    font-size: 0.8rem;
    color: var(--ink-dim);
  }
  .member-controls {
    display: flex;
    gap: 0.35rem;
  }
  .member-controls button {
    padding: 0.35rem 0.55rem;
  }

  .add-panel {
    margin-top: 1.75rem;
    border-top: 1px solid var(--line);
    padding-top: 1.25rem;
  }
  .add-panel-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .add-panel h2 {
    font-family: var(--font-display);
    font-weight: 500;
    font-size: 1.2rem;
    margin: 0 0 0.5rem;
  }
  .candidate-search {
    width: 100%;
    margin-bottom: 0.75rem;
  }
  .candidates {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    max-height: 22rem;
    overflow-y: auto;
  }
  .add-candidate {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.5rem 0.75rem;
    background: var(--bg-raised);
    border: 1px solid var(--line);
    border-radius: var(--radius);
  }
  .candidate-title {
    font-family: var(--font-ui);
    color: var(--ink);
  }
  .candidate-composer {
    font-family: var(--font-ui);
    font-size: 0.8rem;
    color: var(--ink-dim);
  }
  .add-candidate-btn {
    margin-left: auto;
  }
</style>
