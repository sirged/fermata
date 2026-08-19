async function j(res) {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
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
  saveTranscription: (id, content) =>
    fetch(`/api/scores/${id}/transcription`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }).then(j),
  transcriptionAnalysis: (id) => fetch(`/api/scores/${id}/transcription/analysis`).then(j),
};
