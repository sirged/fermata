// carries the HTTP status so callers can branch on it (e.g. 404 vs a real
// failure) instead of string-sniffing the message
export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
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
  scan: () => fetch("/api/scan", { method: "POST" }).then(j),
  scanStatus: () => fetch("/api/scan/status").then(j),
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
  // `format` says what the edited text actually is. The endpoint will sniff
  // it when omitted, but the client already knows and the renderer dispatches
  // on the stored value, so send it rather than rely on the guess.
  saveTranscription: (id, content, format) =>
    fetch(`/api/scores/${id}/transcription`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, format }),
    }).then(j),
  // deletes only the edited row, leaving the extracted one (if any) as the
  // real revert target; may 404 (nothing left) or 405 (not deployed yet)
  deleteTranscription: (id) =>
    fetch(`/api/scores/${id}/transcription`, { method: "DELETE" }).then(j),
  transcriptionAnalysis: (id) => fetch(`/api/scores/${id}/transcription/analysis`).then(j),
};
