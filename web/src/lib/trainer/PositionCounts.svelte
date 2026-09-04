<script>
  // The panel a fretboard drill shows beside itself: which positions get
  // answered incorrectly, and how many times (issue #235). Reads the
  // existing GET /api/trainer/attempts?correct=false and groups client-side
  // (position-counts.js) - docs/practice-data.md's own answer for "which
  // positions am I weak on" is a filter over the raw record, not a bespoke
  // aggregate endpoint, and this panel is that filter's one caller so far.
  //
  // Counts only, same rule the drill itself already holds to: no percentage,
  // no streak, no ranking beyond "these counts, largest first".
  import { api } from "../api.js";
  import {
    ATTEMPT_FETCH_LIMIT,
    NO_POSITIONS_STATEMENT,
    positionCounts,
    positionStatement,
    truncationStatement,
  } from "./position-counts.js";

  // `refreshToken` is any value the caller changes to ask this panel to
  // refetch - FretToNote.svelte bumps it once a logged attempt's own request
  // has resolved, so the list is current without a reload, and only after
  // the row it is about actually exists on the server.
  let { drill = "fret_to_note", limit = 5, refreshToken = 0 } = $props();

  let rows = $state([]);
  let loaded = $state(false);
  let loadError = $state("");
  // What GET /api/trainer/attempts itself said about how much of the record
  // this fetch actually got, kept alongside the grouped `rows` rather than
  // discarded - a fetch that stopped at ATTEMPT_FETCH_LIMIT rows looks
  // identical to a complete one unless something says otherwise (see
  // docs/practice-data.md and ScoreProgress.svelte's own sessions_truncated).
  let truncated = $state(false);
  let returnedCount = $state(0);
  let total = $state(0);

  async function load() {
    try {
      const response = await api.trainerAttempts({
        drill,
        correct: false,
        limit: ATTEMPT_FETCH_LIMIT,
      });
      rows = positionCounts(response.attempts, { limit });
      returnedCount = response.attempts.length;
      total = response.total;
      truncated = response.truncated;
      loadError = "";
    } catch (e) {
      loadError = e?.message || "The positions answered incorrectly could not be loaded.";
    } finally {
      loaded = true;
    }
  }

  $effect(() => {
    // Referenced only to make this effect re-run when the caller bumps it -
    // the fetch itself never uses the value.
    refreshToken;
    load();
  });
</script>

<section
  class="position-counts"
  data-loaded={loaded ? "1" : "0"}
  data-row-count={rows.length}
  data-truncated={truncated ? "1" : "0"}
  aria-live="polite"
>
  <h2>Positions answered incorrectly</h2>
  {#if loadError}
    <p class="notice load-error">{loadError}</p>
  {:else if !loaded}
    <p class="quiet">Loading…</p>
  {:else}
    {#if truncated}
      <p class="quiet truncated">{truncationStatement({ returned: returnedCount, total })}</p>
    {/if}
    {#if rows.length === 0}
      <p class="quiet empty-state">{NO_POSITIONS_STATEMENT}</p>
    {:else}
      <ol>
        {#each rows as row (`${row.string}:${row.fret}`)}
          <li data-string={row.string} data-fret={row.fret} data-count={row.count}>
            {positionStatement(row)}
          </li>
        {/each}
      </ol>
    {/if}
  {/if}
</section>

<style>
  .position-counts {
    width: 100%;
    max-width: 720px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 16px 22px;
    background: var(--bg-raised);
    border: 1px solid var(--line);
    border-radius: var(--radius);
  }

  h2 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: var(--ink);
  }

  .quiet,
  .empty-state {
    margin: 0;
    color: var(--ink-dim);
    font-size: 13px;
  }

  ol {
    margin: 0;
    padding: 0 0 0 20px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  li {
    font-size: 14px;
    color: var(--ink);
    font-variant-numeric: tabular-nums;
  }

  .notice {
    margin: 0;
    font-size: 13px;
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 6px 10px;
  }
</style>
