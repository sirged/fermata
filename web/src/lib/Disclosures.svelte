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
  //
  // `barsLabel` overrides the plain "bar"/"page" word - form_marks_unanchored
  // sets it to "near bar" because its list is a NEAREST-bar fallback, not the
  // bar the mark was actually drawn over (see disclosures.js's own comment on
  // that row, citing tabextract.py's "Nearest bars:" prose).
  function barsText(row) {
    const unit = row.barsLabel ?? (row.barUnit === "page" ? "page" : "bar");
    const label = row.bars.length === 1 ? unit : `${unit}s`;
    return `${label} ${row.bars.join(", ")}`;
  }

  // A "flag" row (kind: "flag" in disclosures.js - currently only
  // endings_incomplete) is a 0/1 fact, not a count: rendering its value as a
  // bare "1" reads as "one thing", the wrong claim for a yes/no condition.
  // Its presence in `rows` at all already means the answer is yes (a
  // measured 0 is filtered out by disclosureRows before this ever runs), so
  // a measured flag shows no value at all - just its label. An UNMEASURED
  // flag still needs to say "not measured", the same as any other row, so
  // this only suppresses the value for the measured case.
  function showsValue(row) {
    return row.kind !== "flag" || !row.measured;
  }
</script>

{#if rows.length}
  <div class="disclosures" data-testid="disclosures">
    <h3 class="disclosures-title">Structural disclosures</h3>
    <ul role="list">
      {#each rows as row (row.key)}
        {@const withValue = showsValue(row)}
        <li class="disclosure-row" data-disclosure={row.key}>
          <span class="disclosure-label" class:standalone={!withValue}>{row.label}</span>
          {#if withValue}
            {#if row.measured}
              <span class="disclosure-value">{row.value}</span>
            {:else}
              <span class="disclosure-value not-measured">not measured</span>
            {/if}
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

  /* An <h3> for real document structure (a11y review, issue #155) - reset to
     the same quiet register as the rest of this block rather than a
     browser's bold, oversized default. */
  .disclosures-title {
    margin: 0 0 4px;
    font: inherit;
    font-weight: 600;
    letter-spacing: 0.01em;
  }

  .disclosures ul {
    margin: 0;
    padding: 0;
    /* list-style: none removes the bullets AND, in Safari/VoiceOver, the
       list semantics along with them - role="list" on the element (a11y
       review) restores "list"/"listitem" to the accessibility tree even
       though there is nothing left to look like one visually. */
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

  /* A measured flag row (see showsValue above) renders no value after the
     label, so it gets no trailing colon either - "Volta numbering has gaps"
     reads as a complete statement on its own; "Volta numbering has gaps:"
     with nothing after it would read as truncated. */
  .disclosure-label:not(.standalone)::after {
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
