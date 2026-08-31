// carries the HTTP status so callers can branch on it (e.g. 404 vs a real
// failure) instead of string-sniffing the message
export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// FastAPI reports its own request validation as a LIST of objects under
// `detail`, not a string - and a caller that only looked for a string showed
// "Request failed (422)" for a rejection the server had described precisely,
// naming the field. `loc` starts with the source ("body", "path", "query"),
// which is noise to a person looking at a form, so it is dropped.
function describeValidationErrors(errors) {
  return errors
    .map((e) => {
      const path = Array.isArray(e?.loc)
        ? e.loc.filter((part) => !["body", "path", "query"].includes(part)).join(".")
        : "";
      const message = e?.msg ?? "is not valid";
      return path ? `${path}: ${message}` : message;
    })
    .filter(Boolean)
    .join("; ");
}

async function j(res) {
  if (!res.ok) {
    // FastAPI puts the actionable text in `detail` (422 validation errors,
    // explicit HTTPException) - fall back to the status line only when the
    // body isn't JSON or has no detail. res.statusText is empty over
    // HTTP/2, so that fallback can't be relied on alone either.
    let detail = "";
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = describeValidationErrors(body.detail);
    } catch {
      // not JSON - nothing to extract
    }
    throw new ApiError(res.status, detail || res.statusText || `Request failed (${res.status})`);
  }
  return res.json();
}

export const api = {
  scores: (params = {}) => {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v)),
    );
    return fetch(`/api/scores?${q}`).then(j);
  },
  score: (id) => fetch(`/api/scores/${id}`).then(j),
  patch: (id, body) =>
    fetch(`/api/scores/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),
  collections: () => fetch("/api/collections").then(j),
  duplicates: () => fetch("/api/duplicates").then(j),
  tags: () => fetch("/api/tags").then(j),
  practice: (id) => fetch(`/api/scores/${id}/practice`).then(j),
  logPractice: (id, body) =>
    fetch(`/api/scores/${id}/practice`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),
  practiceSummary: () => fetch("/api/practice/summary").then(j),
  // How one piece is going (#57): its whole record, the window's per-day
  // totals, the tempo each session was practised at, how the time split
  // between section work and run-throughs, the sessions with their notes, and
  // any goal set about this piece. One call, because every figure on it is
  // counted by the server - a client totalling sessions itself would be
  // writing arithmetic the API already owns, and getting it subtly different
  // from whatever else reads that API next (issue #32).
  scoreProgress: (id, days, today) =>
    fetch(`/api/scores/${id}/practice/progress?days=${days}&today=${today}`).then(j),
  // Detail added to a session already logged. The timer stores the length the
  // moment it stops and this fills in how it went, so a stopped clock is never
  // waiting on a form and a session is never lost to an abandoned one. An
  // explicit null clears a field - which is how a rating entered by mistake
  // comes off again.
  patchSession: (id, body) =>
    fetch(`/api/practice/sessions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),
  deleteSession: (id) => fetch(`/api/practice/sessions/${id}`, { method: "DELETE" }).then(j),
  // Practice that is not against a piece at all - an exercise, or simply
  // playing. `score_id` is optional for every activity except "piece".
  logSession: (body) =>
    fetch("/api/practice/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),
  sessions: (params = {}) => {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== "")),
    );
    return fetch(`/api/practice/sessions?${q}`).then(j);
  },
  practiceHistory: (days, today) =>
    fetch(`/api/practice/history?days=${days}&today=${today}`).then(j),
  // `today` is the BROWSER's date on every one of these. The server's own date
  // is UTC, and whether a week is still running must not be an accident of the
  // hour - west of Greenwich the UTC date is already tomorrow while somebody
  // still has their evening to practise in.
  currentGoal: (today) => fetch(`/api/practice/goals/current?today=${today}`).then(j),
  goals: (today) => fetch(`/api/practice/goals?today=${today}`).then(j),
  setGoal: (body, today) =>
    fetch(`/api/practice/goals?today=${today}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),
  patchGoal: (id, body, today) =>
    fetch(`/api/practice/goals/${id}?today=${today}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),
  deleteGoal: (id) => fetch(`/api/practice/goals/${id}`, { method: "DELETE" }).then(j),
  practiceReview: (weeks, today) =>
    fetch(`/api/practice/review?weeks=${weeks}&today=${today}`).then(j),
  scan: () => fetch("/api/scan", { method: "POST" }).then(j),
  scanStatus: () => fetch("/api/scan/status").then(j),
  // Says "yes, I meant to remove that much" about ONE refused reconciliation.
  // The token names the exact set of files the refusal was about, so consent
  // cannot be replayed against a larger loss that arrived in the meantime.
  acknowledgeScan: (token) =>
    fetch("/api/scan/acknowledge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    }).then(j),
  upload: (file, folder = "Uploads") => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch(`/api/upload?folder=${encodeURIComponent(folder)}`, {
      method: "POST",
      body: fd,
    }).then(j);
  },
  // --- Managing the library (issue #56) ------------------------------------
  // The one part of this API that writes to the user's own files. Two habits
  // are baked in here rather than left to each caller: a bulk operation is
  // asked for as a dry run first and applied as a second, separate call, and
  // nothing here has a "force" of any kind - a refusal comes back as an
  // ApiError with the server's own sentence in it, which is what the UI shows.
  //
  // `folder` may be "" (the library root), which is not the same as omitting
  // it, so it is passed through as given rather than filtered.
  moveScore: (id, { folder, filename, dryRun = false } = {}) =>
    fetch(`/api/scores/${id}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(folder === undefined ? {} : { folder }),
        ...(filename === undefined ? {} : { filename }),
        dry_run: dryRun,
      }),
    }).then(j),
  moveScores: (ids, folder, dryRun = true) =>
    fetch("/api/library/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ score_ids: ids, folder, dry_run: dryRun }),
    }).then(j),
  folders: () => fetch("/api/library/folders").then(j),
  createFolder: (path) =>
    fetch("/api/library/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }).then(j),
  renameFolder: (from, to, dryRun = true) =>
    fetch("/api/library/folders/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_path: from, to_path: to, dry_run: dryRun }),
    }).then(j),
  // Soft: the file goes to the library's trash folder and the score row - with
  // its practice history, goals, tags and transcription - stays. The response
  // counts each of those, which is what the confirmation shows.
  deleteScore: (id) => fetch(`/api/scores/${id}`, { method: "DELETE" }).then(j),
  trash: () => fetch("/api/trash").then(j),
  restoreScore: (id) => fetch(`/api/trash/${id}/restore`, { method: "POST" }).then(j),
  // The destructive one, and the only one. Deliberately named for what it does
  // rather than "delete", so no caller reaches for it by accident.
  destroyScore: (id) => fetch(`/api/trash/${id}`, { method: "DELETE" }).then(j),
  fileUrl: (id) => `/api/scores/${id}/file`,
  thumbUrl: (id) => `/api/scores/${id}/thumb`,
  transcription: (id) => fetch(`/api/scores/${id}/transcription`).then(j),
  transcribe: (id, body) =>
    fetch(`/api/scores/${id}/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    }).then(j),
  // No `format` is sent: what the user typed into the source editor decides
  // it, not the format of the row they opened, and the server reads it off the
  // content. Sending the loaded row's format stored a pasted alphaTex edit as
  // musicxml, after which the viewer handed it to the MusicXML loader and the
  // staff never appeared. Sniffing here as well would be a second copy of the
  // rule with its own chance to disagree; the endpoint still accepts an
  // explicit format for a client that genuinely knows.
  saveTranscription: (id, content) =>
    fetch(`/api/scores/${id}/transcription`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }).then(j),
  // deletes only the edited row, leaving the extracted one (if any) as the
  // real revert target; may 404 (nothing left) or 405 (not deployed yet)
  deleteTranscription: (id) =>
    fetch(`/api/scores/${id}/transcription`, { method: "DELETE" }).then(j),
  transcriptionAnalysis: (id) => fetch(`/api/scores/${id}/transcription/analysis`).then(j),
  // Bulk transcription (issue #55) - the scan's own pattern: start it, poll
  // its status. `scoreIds` and `collection` are mutually exclusive (the
  // server 422s if both are given); omitting both selects every eligible
  // score in the whole library.
  transcribeBatch: (scoreIds, { collection, reconvert = false } = {}) =>
    fetch("/api/transcribe/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(scoreIds ? { score_ids: scoreIds } : {}),
        ...(collection !== undefined ? { collection } : {}),
        reconvert,
      }),
    }).then(j),
  transcribeBatchStatus: () => fetch("/api/transcribe/batch/status").then(j),
  instruments: () => fetch("/api/instruments").then(j),
  instrumentPresets: () => fetch("/api/instruments/presets").then(j),
  // A whole definition, not a patch: string_count and string_pitches have to
  // agree, and fret_count exists only when fretted, so half a definition
  // merged onto an old one is how a five-string bass ends up with four pitches.
  createInstrument: (body) =>
    fetch("/api/instruments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),
  saveInstrument: (id, body) =>
    fetch(`/api/instruments/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),
  deleteInstrument: (id) => fetch(`/api/instruments/${id}`, { method: "DELETE" }).then(j),
  // Structured, per-question fretboard drill results (issue #27, #32) - one
  // row per answered question, independent of the practice_sessions row the
  // drill's own TIME is logged against (see fret-to-note.js's module
  // docstring for why the two are separate calls).
  logTrainerAttempt: (body) =>
    fetch("/api/trainer/attempts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),
  trainerAttempts: (params = {}) => {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== "")),
    );
    return fetch(`/api/trainer/attempts?${q}`).then(j);
  },
  // Structured, per-question chord flash card results (issue #28, #32) - the
  // same idea as logTrainerAttempt, in a table of its own (see
  // server/fermata/trainer.py's module docstring for why a chord does not
  // fit trainer_attempts' single-note columns).
  logChordAttempt: (body) =>
    fetch("/api/trainer/chord-attempts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),
  chordAttempts: (params = {}) => {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== "")),
    );
    return fetch(`/api/trainer/chord-attempts?${q}`).then(j);
  },
  version: () => fetch("/api/version").then(j),
  settings: () => fetch("/api/settings").then(j),
  putSettings: (values) =>
    fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    }).then(j),
  // --- Getting everything in and out (issue #58) ----------------------------
  // Everything Fermata knows, as one zip archive. Returns the response's own
  // bytes and the filename the server chose (from Content-Disposition)
  // rather than parsed JSON - `j()` assumes a JSON body, and export answers
  // with a real archive instead.
  exportLibrary: async ({ includeTrash = true, includeFiles = true } = {}) => {
    const q = new URLSearchParams({
      include_trash: String(includeTrash),
      include_files: String(includeFiles),
    });
    const res = await fetch(`/api/export?${q}`);
    if (!res.ok) {
      let detail = "";
      try {
        const body = await res.json();
        if (typeof body?.detail === "string") detail = body.detail;
      } catch {
        // not JSON - nothing to extract
      }
      throw new ApiError(res.status, detail || res.statusText || `Request failed (${res.status})`);
    }
    const blob = await res.blob();
    const disposition = res.headers.get("content-disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    return { blob, filename: match ? match[1] : "fermata-export.zip" };
  },
  // `dryRun` defaults true, the same default every bulk operation in this API
  // uses (see moveScore/moveScores/renameFolder above) - a caller previews
  // what an archive holds before writing anything from it.
  importLibrary: (file, { dryRun = true } = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch(`/api/import?dry_run=${dryRun}`, { method: "POST", body: fd }).then(j);
  },
  // --- Setlists (issue #6) --------------------------------------------------
  // A setlist is an ordered collection of scores. The server owns the order (a
  // stored position), so the client never sorts members itself - it renders
  // them as they arrive - and every mutation returns the whole setlist as it
  // now stands, so nothing here re-fetches to learn the new state. Removing a
  // score from a setlist, or deleting a setlist, never deletes a score.
  setlists: () => fetch("/api/setlists").then(j),
  setlist: (id) => fetch(`/api/setlists/${id}`).then(j),
  createSetlist: (name) =>
    fetch("/api/setlists", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).then(j),
  renameSetlist: (id, name) =>
    fetch(`/api/setlists/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).then(j),
  deleteSetlist: (id) => fetch(`/api/setlists/${id}`, { method: "DELETE" }).then(j),
  addToSetlist: (id, scoreId) =>
    fetch(`/api/setlists/${id}/scores`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ score_id: scoreId }),
    }).then(j),
  removeFromSetlist: (id, scoreId) =>
    fetch(`/api/setlists/${id}/scores/${scoreId}`, { method: "DELETE" }).then(j),
  // The whole order at once, a permutation of the current members - the server
  // rejects anything that is not exactly that (see the reorder endpoint), which
  // is what makes "move this up one" safe to express as the new full order.
  reorderSetlist: (id, scoreIds) =>
    fetch(`/api/setlists/${id}/order`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ score_ids: scoreIds }),
    }).then(j),
};
