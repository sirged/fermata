<script>
  // Renders every structural-form/inference disclosure disclosureRows()
  // selects for this transcription - one row per non-zero (or unmeasured)
  // counter, beside the Rule 8 conformance figures ScoreCompare already
  // shows (issue #155). See disclosures.js for the selection rules; this
  // component only lays out what it is handed.
  import { disclosureRows } from "./disclosures.js";

  let { transcription = null } = $props();

  let rows = $derived(disclosureRows(transcription));

  // No in-viewer "jump to this bar" exists yet (see PR body for #155) - so
  // the numbers are plain, readable text rather than links. They still tell
  // a reader exactly where to look on the page open beside this one.
  function barsText(row) {
    const unit = row.barUnit === "page" ? "page" : "bar";
    const label = row.bars.length === 1 ? unit : `${unit}s`;
    return `${label} ${row.bars.join(", ")}`;
  }
</script>

{#if rows.length}
  <div class="disclosures" data-testid="disclosures">
    <p class="disclosures-title">Structural disclosures</p>
    <ul>
      {#each rows as row (row.key)}
        <li class="disclosure-row" data-disclosure={row.key}>
          <span class="disclosure-label">{row.label}</span>
          {#if row.measured}
            <span class="disclosure-value">{row.value}</span>
          {:else}
            <span class="disclosure-value not-measured">not measured</span>
          {/if}
          {#if row.measured && row.bars.length}
            <span class="disclosure-bars">{barsText(row)}</span>
          {/if}
        </li>
      {/each}
    </ul>
  </div>
{/if}

<style>
  /* Stated in the same quiet register the provenance block uses in
     ScoreCompare.svelte - a count of what wasn't read is a fact about this
     transcription, not styled as a fault the way the danger-colored
     warnings box is. */
  .disclosures {
    margin: 10px 16px 0;
    font-size: 11.5px;
    color: var(--ink-dim);
  }

  .disclosures-title {
    margin: 0 0 4px;
    font-weight: 600;
    letter-spacing: 0.01em;
  }

  .disclosures ul {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .disclosure-row {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 4px 8px;
  }

  .disclosure-label {
    color: var(--ink);
  }

  .disclosure-label::after {
    content: ":";
  }

  .disclosure-value {
    font-weight: 600;
    color: var(--ink);
  }

  .disclosure-value.not-measured {
    font-weight: 400;
    font-style: italic;
    color: var(--ink-dim);
  }

  .disclosure-bars {
    color: var(--ink-dim);
  }
</style>
