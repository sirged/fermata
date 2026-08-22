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
  settings: () => fetch("/api/settings").then(j),
  putSettings: (values) =>
    fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    }).then(j),
};
