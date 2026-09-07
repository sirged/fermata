<script>
  // Getting everything in and out (issue #58). This component only triggers
  // and reports - every field the archive actually carries, the validation
  // that rejects a bad one, and the writing itself all happen server-side in
  // fermata/api.py's export_library/import_library, per issue #32's rule
  // that the client wraps the documented API rather than reimplementing any
  // of its logic.
  import { api } from "./api.js";

  // Every count ImportOut carries (issue #284), in the order a person would
  // want to hear it - the scores themselves first, then what travels with
  // them. Singular/plural pairs are spelled out rather than run through a
  // generic pluraliser, because a couple of them do not just take an "s".
  const IMPORT_COUNT_FIELDS = [
    ["scores_imported", "score", "scores"],
    ["scores_trashed_imported", "trashed score", "trashed scores"],
    ["files_written", "file written", "files written"],
    ["transcriptions_imported", "transcription", "transcriptions"],
    ["tags_imported", "tag", "tags"],
    ["tags_reused", "tag reused rather than duplicated", "tags reused rather than duplicated"],
    ["score_tags_imported", "tag assignment", "tag assignments"],
    ["instruments_imported", "instrument", "instruments"],
    ["practice_sessions_imported", "practice session", "practice sessions"],
    ["practice_goals_imported", "practice goal", "practice goals"],
    ["settings_imported", "setting", "settings"],
    ["setlists_imported", "setlist", "setlists"],
    ["setlist_scores_imported", "setlist entry", "setlist entries"],
    ["trainer_attempts_imported", "fret-to-note drill attempt", "fret-to-note drill attempts"],
    ["trainer_chord_attempts_imported", "chord drill attempt", "chord drill attempts"],
    ["trainer_presets_imported", "saved drill scope", "saved drill scopes"],
    ["trainer_preset_strings_imported", "drill scope string set", "drill scope string sets"],
  ];

  /** Every word of an ordinary English list: "a", "a and b", "a, b and c". */
  function joinList(items) {
    if (items.length === 0) return "";
    if (items.length === 1) return items[0];
    if (items.length === 2) return `${items[0]} and ${items[1]}`;
    return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
  }

  /** Every count an ImportOut carries, as one sentence - a compact list of
   * whatever is not zero, with every count that IS zero folded into a single
   * closing clause rather than named one by one (#284). Also states the two
   * schema versions and where the archive came from (#282, #275), so this one
   * sentence covers every field of ImportOut apart from `trainer_scope_presets_renamed`,
   * which gets its own line below since it is a list, not a count. */
  function importSummaryText(result) {
    const counts = [];
    let zeros = 0;
    for (const [key, singular, plural] of IMPORT_COUNT_FIELDS) {
      const n = result[key] ?? 0;
      if (n > 0) counts.push(`${n} ${n === 1 ? singular : plural}`);
      else zeros += 1;
    }
    const tail =
      `, written ${result.exported_at} by Fermata ${result.fermata_version}. ` +
      `Read from a schema version ${result.schema_version_read} archive into this Fermata's ` +
      `own schema version ${result.schema_version}.`;
    // The all-zero case gets its own sentence rather than reusing the
    // "verb + body" template below with an empty body: that template read
    // "This archive holds nothing else was in the archive, written …" when
    // every count was zero, because "nothing else" only makes sense next to
    // something that was named first.
    if (counts.length === 0) {
      return `${result.dry_run ? "This archive holds nothing" : "Nothing was imported"}${tail}`;
    }
    const verb = result.dry_run ? "This archive holds" : "Imported";
    const body =
      zeros > 0 ? `${joinList(counts)} - nothing else was in the archive` : joinList(counts);
    return `${verb} ${body}${tail}`;
  }

  /** One rename line, saying why the archived name did not survive - #260's
   * plain collision, or #268's cleaning (with or without a second collision
   * behind it). */
  function renameReasonText(entry) {
    if (entry.reason === "cleaned") {
      return `${entry.from} → ${entry.to} (its name only needed tidying up, nothing collided)`;
    }
    if (entry.reason === "collision") {
      return `${entry.from} → ${entry.to} (tidied, then that name was already taken too)`;
    }
    return `${entry.from} → ${entry.to} (that name was already taken)`;
  }

  let exporting = $state(false);
  let exportError = $state("");

  // The chosen File and the file <input> itself - kept so a confirmed import
  // can re-send the SAME bytes the preview read, and so the input can be
  // cleared after a successful import (or a cancel) without the browser's
  // own "choose a file" affordance still showing a stale name.
  let fileInput = $state(null);
  let selectedFile = $state(null);
  let preview = $state(null);
  let previewing = $state(false);
  let previewError = $state("");
  let applying = $state(false);
  let applyError = $state("");
  let applied = $state(null);

  async function doExport() {
    exporting = true;
    exportError = "";
    try {
      const { blob, filename } = await api.exportLibrary();
      // A page cannot hand the browser a file to save on its own - an <a
      // download> is the ordinary way, built and clicked here and nowhere
      // else in this file.
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      exportError = e?.message ?? "Could not export the library.";
    } finally {
      exporting = false;
    }
  }

  function resetImportState() {
    preview = null;
    previewError = "";
    applyError = "";
    applied = null;
  }

  async function chooseFile(event) {
    resetImportState();
    const file = event.target.files?.[0] ?? null;
    selectedFile = file;
    if (!file) return;
    previewing = true;
    try {
      // dry_run (the default) reads and validates the archive completely
      // without writing anything - see api.import_library's own docstring.
      // What is shown below is exactly what a real import would do.
      preview = await api.importLibrary(file, { dryRun: true });
    } catch (e) {
      previewError = e?.message ?? "Could not read that archive.";
    } finally {
      previewing = false;
    }
  }

  async function confirmImport() {
    if (!selectedFile) return;
    applying = true;
    applyError = "";
    try {
      applied = await api.importLibrary(selectedFile, { dryRun: false });
      preview = null;
      selectedFile = null;
      if (fileInput) fileInput.value = "";
    } catch (e) {
      applyError = e?.message ?? "Could not import that archive.";
    } finally {
      applying = false;
    }
  }

  function cancelImport() {
    resetImportState();
    selectedFile = null;
    if (fileInput) fileInput.value = "";
  }
</script>

<section class="data-portability">
  <h2>Your data</h2>
  <p class="hint">
    Everything Fermata knows, as one archive: every score row, transcription, practice session,
    goal, tag, favourite, instrument and setting - plus the score files themselves. Export it to
    move to another machine or keep a backup; import an archive into an empty library to pick up
    exactly where you left off.
  </p>

  <div class="row">
    <button onclick={doExport} disabled={exporting} data-testid="export-button">
      {exporting ? "Exporting…" : "Export library"}
    </button>
  </div>
  {#if exportError}
    <p class="error" data-testid="export-error">{exportError}</p>
  {/if}

  <div class="import">
    <label class="file-label">
      <span>Choose archive…</span>
      <input
        bind:this={fileInput}
        type="file"
        accept=".zip"
        onchange={chooseFile}
        data-testid="import-file-input"
      />
    </label>

    {#if previewing}
      <p class="hint" data-testid="import-previewing">Reading the archive…</p>
    {/if}
    {#if previewError}
      <p class="error" data-testid="import-error">{previewError}</p>
    {/if}
    {#if preview}
      <div class="preview" data-testid="import-preview">
        <p data-testid="import-counts">{importSummaryText(preview)}</p>
        <p class="hint">
          Importing adds this to your library - it never replaces or overwrites what is already
          there. Import into an empty library to restore a backup exactly.
        </p>
        {#if preview.trainer_scope_presets_renamed?.length}
          <p class="hint" data-testid="import-renames">
            Renamed on import: {joinList(
              preview.trainer_scope_presets_renamed.map(renameReasonText),
            )}
          </p>
        {/if}
        <div class="row">
          <button onclick={confirmImport} disabled={applying} data-testid="import-confirm">
            {applying ? "Importing…" : "Import"}
          </button>
          <button onclick={cancelImport} disabled={applying}>Cancel</button>
        </div>
      </div>
    {/if}
    {#if applyError}
      <p class="error" data-testid="import-apply-error">{applyError}</p>
    {/if}
    {#if applied}
      <p class="success" data-testid="import-success">{importSummaryText(applied)}</p>
      {#if applied.trainer_scope_presets_renamed?.length}
        <p class="hint" data-testid="import-renames">
          Renamed on import: {joinList(
            applied.trainer_scope_presets_renamed.map(renameReasonText),
          )}
        </p>
      {/if}
    {/if}
  </div>
</section>

<style>
  .data-portability {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .row {
    display: flex;
    gap: 10px;
    align-items: center;
  }

  .file-label {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    font-size: 13px;
    color: var(--ink-dim);
  }

  .preview {
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 12px 14px;
    background: var(--surface);
    font-size: 13px;
    line-height: 1.5;
  }

  .error {
    color: var(--danger);
    font-size: 13px;
    margin: 0;
  }

  .success {
    color: var(--brass-bright);
    font-size: 13px;
    margin: 0;
  }
</style>
