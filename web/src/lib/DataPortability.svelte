<script>
  // Getting everything in and out (issue #58). This component only triggers
  // and reports - every field the archive actually carries, the validation
  // that rejects a bad one, and the writing itself all happen server-side in
  // fermata/api.py's export_library/import_library, per issue #32's rule
  // that the client wraps the documented API rather than reimplementing any
  // of its logic.
  import { api } from "./api.js";

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
        <p>
          This archive holds {preview.scores_imported} score(s) ({preview.scores_trashed_imported}
          in the trash), {preview.practice_sessions_imported} practice session(s),
          {preview.practice_goals_imported} goal(s), {preview.tags_imported} tag(s) and
          {preview.instruments_imported} instrument(s) - written {preview.exported_at} by Fermata
          {preview.fermata_version}.
        </p>
        <p class="hint">
          Importing adds this to your library - it never replaces or overwrites what is already
          there. Import into an empty library to restore a backup exactly.
        </p>
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
      <p class="success" data-testid="import-success">
        Imported {applied.scores_imported} score(s), {applied.practice_sessions_imported} practice
        session(s) and {applied.practice_goals_imported} goal(s).
      </p>
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
