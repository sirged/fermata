<p align="center">
  <img src="assets/banner.png" alt="" width="100%" />
</p>

<h1 align="center">
  <img src="assets/logo.png" alt="" width="72" align="center" />
  Fermata
</h1>

**A self-hosted sheet music server.** Point it at a folder of sheet music, and
get a fast, beautiful library you can browse, search, tag, and practice from —
on any device on your network.

Think of it as the music-stand equivalent of Jellyfin or Calibre-Web: your
files stay yours, on your hardware, organized the way you already organize
them.

## Features

- **Library scanning** — walks your music folder, extracts titles, composers,
  collections, and page counts from folder structure and embedded PDF
  metadata, and generates cover thumbnails.
- **PDF practice reader** — continuous or paged reading, keyboard/pedal page
  turns (any Bluetooth pedal that sends arrow keys works), remembers your last
  page per piece, and a dark "practice room" inversion mode.
- **Notation / tab / both** — MusicXML and Guitar Pro files render
  interactively and can be switched between standard notation, tablature, or
  both staves at once, with audio playback, adjustable speed, and a moving
  cursor.
- **Organize** — collections (from your folder layout), free-form tags,
  favorites, content-type labels (notation / tab / both), full-text search.
- **Upload** — drag files in through the browser; they land in your library
  folder and are indexed immediately.
- **Single container** — SQLite inside, two volume mounts, no external
  services.

## Quick start

```bash
git clone <this repo>
cd fermata
mkdir -p library config
# put some sheet music in ./library (PDF, MusicXML, Guitar Pro)
docker compose up --build -d
```

Open http://localhost:8080 — your library is scanned automatically on startup,
or hit **Scan library** in the sidebar.

### Volumes

| Mount           | Purpose                                        |
| --------------- | ---------------------------------------------- |
| `/data/library` | Your sheet music files (read + uploads)        |
| `/data/config`  | Database, thumbnails, cache — back this up     |

## Development

Backend (FastAPI, Python 3.12):

```bash
cd server
pip install -e .
FERMATA_LIBRARY=../library FERMATA_CONFIG=../config uvicorn fermata.main:app --port 8080 --reload
```

Frontend (Svelte 5 + Vite, proxies `/api` to :8080):

```bash
cd web
npm install
npm run dev
```

## Formats

| Format                          | Viewer                                     |
| ------------------------------- | ------------------------------------------ |
| PDF                             | Practice reader (fixed layout)             |
| MusicXML (`.musicxml`, `.mxl`)  | Interactive: notation / tab / both, audio  |
| Guitar Pro (`.gp3`–`.gp5`, `.gpx`, `.gp`) | Interactive: notation / tab / both, audio |

PDFs are fixed renderings, so the notation/tab toggle applies to the semantic
formats. There's a bundled demo (sidebar → *Notation/tab demo*) if your
library is PDF-only so far.

## Roadmap

- Optical music recognition: optional sidecar to convert engraved PDFs to
  MusicXML so they gain the notation/tab toggle
- Setlists and practice sessions
- Annotations on PDFs (fingerings, markings)
- Splitting compilation books into individual pieces
- Multi-user accounts
- Tablet-first PWA mode with pedal-friendly full-screen

## License

[MIT](LICENSE)
