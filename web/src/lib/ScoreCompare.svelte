<script>
  import { untrack } from "svelte";
  import { api } from "./api.js";
  import PdfViewer from "./PdfViewer.svelte";
  import TabViewer from "./TabViewer.svelte";
  import { STANDING_LIMITS, BAR_RE } from "./warning-patterns.js";

  let {
    score,
    gigMode = false,
    onToggleGig = () => {},
    practiceLabel = null,
    onStopPractice = () => {},
  } = $props();

  // Viewer.svelte reassigns `score` to a fresh object on every metadata
  // PATCH (favorite, content_kind, tags) - track only the id so those edits
  // don't refire the transcription-load effect and blow away an in-progress
  // draft
  let scoreId = $derived(score.id);

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

  // localStorage is swallowed when unavailable (private browsing), which
  // would otherwise make maybeDefaultToSide() re-read a null every time and
  // snap the layout back to "side" on every load - track the choice here too
  let userChoseLayout = loadStoredLayout() != null;

  function setLayout(l) {
    layout = l;
    userChoseLayout = true;
    persistLayout(l);
  }

  function maybeDefaultToSide() {
    if (userChoseLayout) return; // user already made an explicit choice
    layout = "side";
  }

  // gig mode must show exactly one HUD - side-by-side would mount two
  // competing transport bars and exit buttons on a half-width stage view
  let activeLayout = $derived(gigMode && layout === "side" ? "pdf" : layout);

  // "loading" | "none" | "transcribing" | "ready" | "error"
  let transcriptionState = $state("loading");
  let transcription = $state(null);
  let fetchError = $state("");
  // whether this pdf can even be extracted - only known once /analysis
  // answers; null means "haven't checked (or the endpoint isn't there)"
  let analysis = $state(null);

  let tsNum = $state("");
  let tsDen = $state("");

  let editorOpen = $state(false);
  let draft = $state("");
  let saving = $state(false);
  let saveError = $state("");
  let reverting = $state(false);

  // POST /transcribe echoes warnings at the top level; GET /transcription
  // nests them under confidence.warnings (and confidence may still be the
  // raw JSON string if the backend's own parse of it ever failed) - read
  // whichever shape actually showed up rather than assuming one
  let confidenceBlob = $derived.by(() => {
    if (!transcription) return null;
    let c = transcription.confidence;
    if (typeof c === "string") {
      try {
        c = JSON.parse(c);
      } catch {
        c = null;
      }
    }
    return c && typeof c === "object" ? c : null;
  });

  let warningsList = $derived.by(() => {
    if (!transcription) return [];
    if (Array.isArray(transcription.warnings)) return transcription.warnings;
    return Array.isArray(confidenceBlob?.warnings) ? confidenceBlob.warnings : [];
  });

  // The one number the whole warning list exists to protect: an inferred
  // rhythm mistaken for a verified one. /transcribe writes it nested as
  // confidence.confidence.rhythm; read the flat shape too in case that ever
  // changes.
  let rhythmConfidence = $derived(
    confidenceBlob?.confidence?.rhythm ?? confidenceBlob?.rhythm ?? null,
  );
  let rhythmCapped = $derived(!!rhythmConfidence && !/^high\b/i.test(rhythmConfidence));
  let rhythmLabel = $derived(rhythmConfidence ? rhythmConfidence.split(" - ")[0].trim() : "");

  let standingNotes = $derived.by(() => {
    const found = [];
    for (const w of warningsList) {
      for (const lim of STANDING_LIMITS) {
        if (lim.test.test(w) && !found.includes(lim.label)) found.push(lim.label);
      }
    }
    return found;
  });
  let scopedWarnings = $derived(
    warningsList.filter((w) => !STANDING_LIMITS.some((lim) => lim.test.test(w))),
  );

  // The bar-defect count comes ONLY from transcription.bars_defective /
  // bars_measured - siblings of `confidence` at the TOP LEVEL of the
  // transcription object (not nested inside it), the same counts
  // ExtractionResult already carries as data (see tabextract.py).
  // `confidence` itself is a mapping of aspect to human-readable sentence
  // (frets/rhythm/time_signature) and deliberately stays that way, so these
  // two numbers are lifted out to keep it uniform; _transcription_dict lifts
  // them on both GET and POST, so this is one lookup, not a two-shape
  // fallback like rhythmConfidence above.
  //
  // There is no honest fallback when they're absent (an edited row has no
  // confidence at all, which is a normal case, not an error): the backend
  // emits the overfull and short counts as two separate sentences, and a bar
  // wrong in both directions at once (two-voice writing where one voice is
  // over its meter and the other under) is counted into BOTH - per
  // docs/musicxml-tab-profile.md, overfull + short double-counts such a bar
  // and can exceed bars_measured, while bars_defective is the one figure
  // that counts it once. That total isn't recoverable from the two
  // sentences by any arithmetic - summing guesses high, and "take the
  // larger one" is still a guess - so this says nothing about bars rather
  // than something confidently wrong. The individual sentences are still in
  // the detail list either way.
  let barSummary = $derived.by(() => {
    if (transcription && typeof transcription.bars_measured === "number") {
      return transcription.bars_measured
        ? { defective: transcription.bars_defective ?? 0, total: transcription.bars_measured }
        : null;
    }
    return null;
  });

  // Bar-conformance lines are folded into the headline via barSummary above
  // (only once a real total exists) - don't also count them a second time
  // under "N more", or the toggle would promise more bullets than it opens.
  let barLineCount = $derived(scopedWarnings.filter((w) => BAR_RE.test(w)).length);

  let warningsSummary = $derived.by(() => {
    const parts = [];
    if (barSummary?.defective) {
      parts.push(
        `${barSummary.defective} of ${barSummary.total} bar${barSummary.total === 1 ? "" : "s"} don't add up`,
      );
    }
    if (rhythmCapped) parts.push(`rhythm confidence ${rhythmLabel}`);
    const remaining = scopedWarnings.length - (barSummary ? barLineCount : 0);
    if (!parts.length) {
      const n = remaining || standingNotes.length;
      parts.push(n === 1 ? "1 caveat" : `${n} caveats`);
    } else if (remaining) {
      parts.push(`${remaining} more`);
    }
    return parts.join(" · ");
  });

  // Gig mode drops the warnings block entirely (see the !gigMode guard
  // below) - performance isn't when anyone corrects a transcription. But an
  // unverified rhythm silently drilled at tempo is the exact mistake the
  // rest of this file exists to prevent, so the two facts that must never
  // go missing keep a single unobtrusive mark: no text, no layout cost,
  // nothing at all when the score is clean.
  let gigMarkTitle = $derived.by(() => {
    const parts = [];
    if (barSummary?.defective) {
      parts.push(
        `${barSummary.defective} of ${barSummary.total} bar${barSummary.total === 1 ? "" : "s"} don't add up`,
      );
    }
    if (rhythmCapped) parts.push(`rhythm confidence: ${rhythmLabel}`);
    return parts.join(" · ");
  });

  // A warning's full sentence justifies itself after " - " or a full stop;
  // the lead clause alone already carries the count and the cause. The
  // justification is still shown, just dimmer - a `title` tooltip alone
  // would put it a hover away, unreachable on the tablet-on-a-music-stand
  // this app is mostly used on, so nothing here depends on hover to be read.
  function splitWarning(w) {
    const dot = w.indexOf(". ");
    const dash = w.indexOf(" - ");
    let cut = -1;
    if (dot !== -1 && (dash === -1 || dot < dash)) cut = dot + 1;
    else if (dash !== -1) cut = dash;
    if (cut === -1) return [w, ""];
    return [w.slice(0, cut), w.slice(cut).replace(/^\s*-\s*/, " — ")];
  }

  // Expanded the first time a score's warnings are seen this session,
  // collapsed on every later visit - the caveats don't change between
  // visits, so re-reading them by default is friction, not safety.
  const WARNINGS_SEEN_KEY = "fermata.warningsSeen";
  function warningsSeen(id) {
    try {
      const raw = sessionStorage.getItem(WARNINGS_SEEN_KEY);
      return raw ? JSON.parse(raw).includes(id) : false;
    } catch {
      return false;
    }
  }
  function markWarningsSeen(id) {
    try {
      const raw = sessionStorage.getItem(WARNINGS_SEEN_KEY);
      const seen = raw ? JSON.parse(raw) : [];
      if (!seen.includes(id)) {
        seen.push(id);
        sessionStorage.setItem(WARNINGS_SEEN_KEY, JSON.stringify(seen));
      }
    } catch {
      // storage unavailable - defaults to open every time, which is safe
    }
  }

  let detailOpen = $state(false);

  // The one place that recomputes detailOpen, called after every state
  // change that can make `transcription` (and so warningsList) point at a
  // different row - a fresh load, a transcribe, a saved edit (which drops
  // warnings), or a revert (which can bring them back). Reads warningsList
  // fresh rather than taking it as an argument, so it only has to be called
  // after `transcription` is already reassigned.
  //
  // Guarded on warningsList.length: an edited row carries no confidence, so
  // marking a score "seen" while its list is empty would make a REAL
  // warning list (from a later revert, or reloading in a session that
  // hadn't shown one yet) default to collapsed on its first-ever showing -
  // the opposite of the intent. Also guarded on !gigMode: gig mode
  // suppresses the whole block, so a score only ever viewed in gig mode
  // must not be marked seen either, or its warnings arrive pre-collapsed
  // the first time someone opens it in the toolbar view.
  function refreshWarningsDisplay(id) {
    if (warningsList.length && !gigMode) {
      detailOpen = !warningsSeen(id);
      markWarningsSeen(id);
    } else {
      detailOpen = false;
    }
  }

  // guards against a slower response for a previously-viewed score landing
  // after a newer navigation and overwriting what's on screen
  let loadGen = 0;

  async function loadAnalysis() {
    try {
      analysis = await api.transcriptionAnalysis(score.id);
    } catch {
      // endpoint not deployed yet, or it failed - fall back to just
      // offering the transcribe button and letting that call fail loudly
      analysis = null;
    }
  }

  async function loadTranscription() {
    // read via scoreId, not score.id: this runs synchronously (up to the
    // first await) inside the $effect below, and reading score.id directly
    // here would re-attach the effect to the whole `score` object - right
    // back to reloading on every metadata PATCH that this was meant to fix
    const id = scoreId;
    const gen = ++loadGen;
    transcriptionState = "loading";
    fetchError = "";
    analysis = null;
    try {
      const t = await api.transcription(id);
      if (gen !== loadGen) return; // superseded by a newer load
      transcription = t;
      draft = t.content;
      transcriptionState = "ready";
      refreshWarningsDisplay(id);
      maybeDefaultToSide();
    } catch (e) {
      if (gen !== loadGen) return;
      if (e.status === 404) {
        transcriptionState = "none";
        loadAnalysis();
      } else {
        // endpoint missing entirely, network down, backend not merged yet -
        // degrade rather than pretend a transcription exists
        transcriptionState = "error";
        fetchError = e.message;
      }
    }
  }

  $effect(() => {
    void scoreId;
    loadTranscription();
    return () => {
      loadGen++; // invalidate any request still in flight from this run
    };
  });

  async function runTranscribe() {
    const gen = ++loadGen;
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
      if (gen !== loadGen) return;
      transcription = t;
      draft = t.content;
      transcriptionState = "ready";
      refreshWarningsDisplay(score.id);
      maybeDefaultToSide();
    } catch (e) {
      if (gen !== loadGen) return;
      transcriptionState = "none";
      fetchError = e.message;
    }
  }

  function openEditor() {
    draft = transcription?.content ?? "";
    saveError = "";
    editorOpen = true;
  }

  async function saveEdit() {
    if (!draft.trim()) {
      // the backend requires min_length=1 and would otherwise bounce this
      // as an opaque 422 - catch it here with a clear message instead
      saveError = "Can't save empty content.";
      return;
    }
    saving = true;
    saveError = "";
    try {
      const res = await api.saveTranscription(score.id, draft);
      // be defensive about what the endpoint actually echoes back - the
      // edit itself is the source of truth for content/source either way
      // `res` carries the format the server read off the content, which is
      // what the viewer dispatches on - so let it win over the loaded row's.
      // `res` STATES warnings ([]) and the four bars_* figures (null) on
      // every response rather than omitting them for an edit, which has no
      // confidence to report - an absent key would be silently kept by this
      // spread, which is exactly how a saved edit once went on reporting the
      // bar counts and warnings from the content it had just replaced. An
      // explicit "not recorded" overwrites those stale values instead.
      transcription = { ...transcription, ...res, content: draft, source: "edited" };
      // expected to land on "nothing to show" now that `res` cleared the
      // stale figures above - going through the shared helper rather than
      // assuming that keeps it correct if this ever changes again
      refreshWarningsDisplay(score.id);
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
      // deletes only the edited row and hands back whatever's left - this
      // is a real revert (the extracted row's original content/params),
      // not a re-run of extraction that could bar things differently
      const t = await api.deleteTranscription(score.id);
      transcription = t;
      draft = t.content;
      // reverting can bring warnings BACK (the extracted row can carry them
      // even though the edited row just removed never had any) - recompute
      // rather than leave detailOpen at whatever the edited row last set
      refreshWarningsDisplay(score.id);
      editorOpen = false;
    } catch (e) {
      if (e.status === 404) {
        // no extracted row was left once the edit was removed - not a
        // failure, but don't pretend the old edit is still showing either
        transcription = null;
        transcriptionState = "none";
        detailOpen = false;
        fetchError = "Reverted — no extracted transcription was left to fall back to.";
        loadAnalysis();
      } else if (e.status === 405) {
        // DELETE /transcription isn't deployed on this backend yet - say so
        // plainly rather than silently leaving the stale edit in place
        fetchError = "Revert isn't available yet - the server needs the latest backend deployed.";
      } else {
        fetchError = e.message;
      }
    } finally {
      reverting = false;
    }
  }
</script>

{#snippet pdfPane(hidden)}
  <div class="pane" class:hidden>
    <PdfViewer {score} {gigMode} {onToggleGig} {practiceLabel} {onStopPractice} />
  </div>
{/snippet}

{#snippet staffPane(hidden)}
  <div class="pane staff-pane" class:hidden>
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
        {#if analysis && !analysis.extractable}
          <h3>No tab to extract</h3>
          <p>{analysis.reason || "This PDF doesn't contain extractable tab or standard notation staves."}</p>
        {:else}
          <h3>No staff transcription yet</h3>
          <p>
            Fermata can pull the guitar tab out of this PDF and render it as a playable,
            editable staff, saved as MusicXML so it opens in other notation software too.
            Fret and string extraction is accurate; how much of the rhythm
            can be recovered depends on how the PDF was engraved, and anything left
            uncertain is listed alongside the finished staff. Check it against the PDF
            before trusting it.
          </p>
          <div class="ts-input">
            <label>
              Time signature <span class="opt">(optional — used only if it can't be read from the score)</span>
              <span class="ts-fields">
                <input type="number" min="1" max="32" placeholder="4" bind:value={tsNum} />
                <span class="slash">/</span>
                <input type="number" min="1" max="32" placeholder="4" bind:value={tsDen} />
              </span>
            </label>
          </div>
          {#if fetchError}<p class="hint warn">{fetchError}</p>{/if}
          <button class="primary" onclick={runTranscribe}>Transcribe this PDF</button>
        {/if}
      </div>
    {:else if transcriptionState === "ready"}
      {#if !gigMode && warningsList.length}
        {#if scopedWarnings.length || rhythmCapped}
          <div class="warnings">
            <button
              class="warnings-summary"
              onclick={() => (detailOpen = !detailOpen)}
              aria-expanded={detailOpen}
              aria-controls="warnings-detail"
            >
              <span class="warn-icon">⚠</span>
              <span class="warn-text">{warningsSummary}</span>
              <span class="chev">{detailOpen ? "▲" : "▼"}</span>
            </button>
            <!-- rendered unconditionally (hidden via the `hidden` attribute,
                 not an {#if}) so aria-controls has something to point at
                 even while collapsed - the one state where that reference
                 actually gets used by anything reading it -->
            <div class="warnings-detail" id="warnings-detail" hidden={!detailOpen}>
              <ul>
                {#each scopedWarnings as w}
                  {@const [lead, tail] = splitWarning(w)}
                  <li>{lead}{#if tail}<span class="detail-tail">{tail}</span>{/if}</li>
                {/each}
              </ul>
              {#if standingNotes.length}
                <p class="standing-note">Also: {standingNotes.join("; ")}.</p>
              {/if}
            </div>
          </div>
        {:else if standingNotes.length}
          <p class="standing-footnote">Standing limits: {standingNotes.join("; ")}.</p>
        {/if}
      {/if}
      {#if editorOpen}
        <div class="editor">
          <textarea class="editor-input" bind:value={draft} spellcheck="false"></textarea>
          <div class="editor-actions">
            {#if saveError}<span class="hint warn">{saveError}</span>{/if}
            <button class="ghost" onclick={() => (editorOpen = false)}>Cancel</button>
            <button class="primary" disabled={saving || !draft.trim()} onclick={saveEdit}>
              {saving ? "Saving…" : "Save & render"}
            </button>
          </div>
        </div>
      {/if}
      <div class="staff-render">
        {#if gigMode && gigMarkTitle}
          <span class="gig-mark" title={gigMarkTitle} aria-label={`Unverified: ${gigMarkTitle}`}>●</span>
        {/if}
        <TabViewer
          tex={transcription.content}
          format={transcription.format}
          {gigMode}
          {onToggleGig}
          {practiceLabel}
          {onStopPractice}
        />
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
          <!-- the stored format, so "Edit source" says what you'd be editing -
               transcriptions are MusicXML, but a row saved before that change,
               or hand-edited in alphaTex, keeps its own format -->
          {#if transcription.format}
            <span class="source-badge">{transcription.format}</span>
          {/if}
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

  <div class="panes" class:side={activeLayout === "side"}>
    <!-- both panes always mount, one hidden with CSS rather than an {#if} -
         switching layout used to unmount+remount PdfViewer (re-fetching and
         re-rendering every page, losing scroll position) and tear down and
         rebuild the score renderer (stopping playback) -->
    {@render pdfPane(activeLayout === "staff")}
    {@render staffPane(activeLayout === "pdf")}
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
    position: relative;
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

  /* both panes stay mounted always; the inactive one is pulled out of flow
     and hidden rather than unmounted, so PdfViewer/TabViewer never get torn
     down (and reset scroll/playback) on a layout switch. Absolute + inset
     (not display:none) keeps it sized to the pane's normal footprint, so a
     PdfViewer mounted while hidden still measures a real width to render at. */
  .pane.hidden {
    position: absolute;
    inset: 0;
    visibility: hidden;
    pointer-events: none;
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
    position: relative;
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  /* The one thing that survives into gig mode: a dot, not a banner, sitting
     clear of TabViewer's own bottom-center gig HUD - present only when
     there's something unverified to flag (see gigMarkTitle), so it never
     becomes decoration that stops meaning anything. */
  .gig-mark {
    position: absolute;
    top: 10px;
    right: 14px;
    z-index: 3;
    font-size: 10px;
    line-height: 1;
    color: var(--danger);
    cursor: default;
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
    border: 1px solid var(--danger);
    border-radius: 8px;
    background: rgba(201, 106, 92, 0.12);
    overflow: hidden;
  }

  .warnings-summary {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    background: none;
    border: none;
    border-radius: 0;
    padding: 9px 14px;
    font-size: 13px;
    color: var(--danger);
    text-align: left;
  }

  .warn-icon,
  .chev {
    flex: none;
  }

  .chev {
    font-size: 10px;
    opacity: 0.7;
  }

  .warn-text {
    flex: 1;
    font-weight: 600;
  }

  .warnings-detail {
    padding: 0 14px 10px;
  }

  .warnings-detail ul {
    margin: 0;
    padding-left: 20px;
    font-size: 12.5px;
    color: var(--ink);
  }

  .warnings-detail li {
    margin: 3px 0;
  }

  /* the justification clause, dimmer but always shown - never only in a
     hover-only title, unreachable on a touch device */
  .detail-tail {
    color: var(--ink-dim);
  }

  .standing-note {
    margin: 8px 0 0;
    font-size: 11.5px;
    color: var(--ink-dim);
  }

  .standing-footnote {
    margin: 10px 16px 0;
    font-size: 11.5px;
    color: var(--ink-dim);
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
