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
- **Notation / tab / both** — MusicXML files render interactively and can be
  switched between standard notation, tablature, or both staves at once, with
  audio playback, adjustable speed, and a moving cursor. Guitar Pro files are
  accepted by the scanner and handed to the same renderer's importer, which
  reads that format natively; a browser spec carries an original Guitar Pro 7
  fixture through that whole path (see the format table below). The built-in
  synthesizer also drives practice tools: drag-select a passage on the score
  to loop it, plus a count-in. The
  staff is themed to match the interface, lays itself out differently on a
  phone, a tablet on a stand and a desktop, and can be drawn dark for
  practising in the dark — see [how scores are rendered](docs/rendering.md) for
  what the renderer does, what that layer adds, and why this renderer.
- **Tab out of a PDF** — engraved guitar PDFs carry their tab as real text and
  their rhythm in the music font's own glyphs, so Fermata reads both directly
  instead of guessing at pixels, and renders the result as a playable,
  editable staff beside the original page. Three music-font vocabularies are
  calibrated: Finale's Maestro, Sibelius's Opus, and any font following the
  SMuFL standard, which covers what free engravers such as MuseScore produce.
  A score drawn in something else still gives up its fret numbers, with the
  rhythm estimated from note spacing and labelled as such. The transcription
  is written as **MusicXML**, so it opens in other notation software rather
  than only here — see [the tab profile](docs/musicxml-tab-profile.md) for
  exactly what is written. Fret numbers, strings and chords come out reliably; how much of the
  rhythm survives depends on the engraving, and whatever stayed uncertain is
  listed with the staff rather than hidden. A bar that does not add up — because
  its voices were flattened into one, or a note was read short, or one was
  dropped — is reported as such, and that is a stated conformance rule of the
  profile, so any MusicXML tool finds the same bars from the file alone. Where
  one voice of a bar has to be filled out with silence for the bar to play in
  time, that silence is marked in the MusicXML, counted as missing rather than
  as read, and the bars it happened in are named. Scanned PDFs have nothing to
  read and are not transcribed. A tie is written where the curve joining its
  two notes was matched, and a note the engraving marks as a harmonic is
  written as one; a tie drawn across a system break is not matched, and the
  ends of it that were found are counted rather than half-written. A whole
  library, or one collection, transcribes in a single background pass rather
  than one score at a time.
- **Instruments** — define what you actually play: any number of strings, any
  tuning including reentrant ones, a capo, and a reference pitch other than
  A440. Each string shows the note and frequency it sounds and can be played,
  because choosing between two positions for the same written note is done by
  ear, and on an unfretted instrument a heard pitch is the reference rather
  than a convenience. Presets cover guitars, basses, ukulele, violin, viola and
  cello. Transcription does not yet use a score's instrument — it still assumes
  a six-string guitar in standard tuning.
- **Metronome, everywhere** — one click, available over a piece, on the
  practice page, and on a page of its own, because "I just want a metronome" is
  a real thing to want from a practice tool. Wherever it appears it arrives
  already set up for that context — over a piece, to that piece's tempo and
  time signature — and every pre-filled value is still adjustable, because a
  marking is sometimes wrong, sometimes aspirational, and frequently not the
  speed a passage should be practised at today. Tempo is settable as a
  proportion of the piece from 15% to 175% (far below half speed, because for a
  passage that is beyond you half speed is not slow enough) or as a fixed
  number, one beat per minute at a time. When a setting asks for a click slower
  or faster than one can actually be sounded, it shows the rate it is really
  clicking at and says plainly that it is at the end of its range, rather than
  leaving a percentage on screen that has stopped describing what you hear. It
  does not use the renderer's own
  metronome, which cannot run at a tempo different from the notes beside it —
  see [how scores are rendered](docs/rendering.md).
- **Practice tracking** — a session timer per piece, with recently-practised
  and neglected views so the library reflects what you are actually working on.
  A session can record what the work was: which bars or pages, at what tempo,
  section work or a run-through, and how it went. Practice that is not a piece
  at all — technique, ear training, simply playing — is recorded the same way
  and in the same history ([the data model](docs/practice-data.md)).
- **Hear a note, name it** — the first exercise: a pitch sounds, and you name it
  from four. The three you did not hear are chosen to be worth confusing — a
  semitone away, the same name an octave out, and one a step or two off — because
  four notes far apart teaches nothing. Hear it again as often as you like,
  before and after answering, and nothing counts that. With one instrument
  defined the drill draws from that instrument's own range; with several it uses
  a plain four octaves, because Fermata knows what you own and not which one is
  in your hands. Two counts are stated and nothing is graded: there is no
  accuracy percentage and no streak, and a note you could not name is what the
  practice consists of. The time lands in your practice history as ear training
  and counts towards a weekly goal like anything else.
- **Fretboard drills** — *Fret to note* asks in both directions: a position is
  shown and you name what it sounds, or a note is named and you tap where it
  lies. *Chord flash cards* does the same for major, minor and seventh shapes.
  Either can be narrowed to the strings, the fret range and the key you are
  actually working on, and every answered question is stored as a structured
  row — which positions and which chords were missed, counted, never divided
  into an accuracy percentage ([the data model](docs/practice-data.md)). The
  time lands in your practice history like anything else.
- **Weekly goals, and an honest review** — how many days you mean to practise
  and for how long, on what. While the week runs it says where you stand so the
  goal can still change it; afterwards it states plainly what happened and asks
  whether the goal was realistic. No streaks, no comparison with a better week,
  and nothing that grades you for a week that did not go to plan.
- **Setlists** — ordered collections of pieces to work through: a gig set, a
  lesson plan, a practice rotation. Name one, add scores, and arrange the order
  by hand — the order is kept, not left to chance — then work through it in the
  ordinary viewer, with each piece's own practice progress shown beside it. A
  piece can be in several setlists at once; removing it from one, or deleting a
  setlist outright, never deletes the piece or its history. A piece you have
  sent to the trash stays in its setlists, marked, rather than becoming a dead
  link.
- **Gig mode** — fullscreen, screen kept awake, oversized tap targets and
  half-page turns for a tablet on a music stand.
- **Organize** — collections (from your folder layout), free-form tags,
  favorites, content-type labels (notation / tab / both), full-text search,
  and duplicate detection by file contents. A key signature, a tempo and a
  1-5 difficulty rating can be set per score too — the key is filled in on
  its own from a transcription's decoded key when one is transcribed and left
  alone once set by hand — and the library grid filters by key and
  difficulty (a tempo range is available through the API).
- **Reorganize, from the app** — move and rename scores, make and rename
  folders, move a batch in one go. The change is applied to the real file on
  disk and the score follows it, so its practice history, tags, goals and any
  transcription stay attached. Every bulk move shows you exactly what it will
  do before it does it, and nothing is ever overwritten.
- **Delete that isn't destruction** — deleting a score moves its file to a
  trash folder inside your library and keeps everything hanging off it; Trash
  puts it back exactly as it was. Destroying it for good is a separate,
  deliberate second step that tells you what it will destroy — and even then,
  the practice you logged against it stays in your history.
- **Upload** — drag files in through the browser; they land in your library
  folder and are indexed immediately.
- **Single container** — SQLite inside, two volume mounts, no external
  services.
- **A documented REST API** — everything above is one client of `/api`, which
  is documented, not just present: `/docs` for interactive Swagger UI,
  `/openapi.json` for the schema itself, generated from response models kept
  in sync with what each endpoint actually returns. Enough to script the
  library, log practice, or build a companion app against — see
  [the API guide](docs/api.md). A companion server, off by default, speaks
  the Model Context Protocol, an open standard for describing a set of tools
  to a program that reads them — a structured-data interface offering a
  fixed set of read-only tools built from that same API rather than a second
  copy of it; see [the deployment
  guide](docs/deployment.md#the-model-context-protocol-server).

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

See [the deployment guide](docs/deployment.md) for backups, upgrading, and
current limitations — read that before deciding whether this belongs on your
home network.

### Volumes

| Mount           | Purpose                                        |
| --------------- | ---------------------------------------------- |
| `/data/library` | Your sheet music files (read + uploads)        |
| `/data/config`  | Database, thumbnails, cache — back this up     |

Fermata creates `/data/config` if it needs to, but never `/data/library` — a
library folder that isn't there is usually a volume that didn't mount, and an
empty one is indistinguishable from a library with nothing in it. It stops with
an explanation instead, and recovers by itself once the mount appears.

## Development

Backend (FastAPI, Python 3.12):

```bash
cd server
pip install -e .
FERMATA_LIBRARY=../library FERMATA_CONFIG=../config uvicorn fermata.main:app --port 8080 --reload --no-proxy-headers
```

`--no-proxy-headers` turns off uvicorn's own X-Forwarded-For handling, which
would otherwise let a request carrying that header rewrite what Fermata sees
as its peer address - see docs/deployment.md's "Reverse proxy
authentication" section for why that matters even for a plain dev server
bound to loopback.

Frontend (Svelte 5 + Vite, proxies `/api` to :8080):

```bash
cd web
npm install
npm run dev
```

Everything to do with drawing a staff — appearance, responsive layout, and the
renderer's quirks — lives in `web/src/lib/score-render.js`; components never
touch the renderer directly. [docs/rendering.md](docs/rendering.md) explains
that boundary and the decisions behind it.

Tests:

```bash
cd server
pip install -e ".[dev]"
python -m pytest -q
```

Tests that need real sheet music read it from `FERMATA_TEST_LIBRARY` and skip
when it is unset. Setting `FERMATA_MUSICXML_XSD` to a local copy of the
MusicXML 4.0 schema additionally validates the emitted transcriptions against
it — see [the tab profile](docs/musicxml-tab-profile.md#checking-a-file).

## Formats

| Format | How it reads |
| --- | --- |
| PDF, engraved with a tab staff | Practice reader, plus transcription to MusicXML — a playable staff beside the page, and a file other notation software reads ([profile](docs/musicxml-tab-profile.md)) |
| PDF, engraved without tab | Practice reader; nothing to transcribe from |
| PDF, scanned | Practice reader only — a scan holds no text or glyphs to read |
| MusicXML (`.musicxml`, `.mxl`) | Interactive: notation / tab / both, audio, full fidelity |
| Guitar Pro (`.gp3`–`.gp5`, `.gpx`, `.gp`) | Scanned and indexed, then handed to the renderer's own importer, which reads the format natively[^gp] |

A PDF is a fixed rendering, so the notation/tab toggle belongs to the
structured formats — and to a transcription made from a PDF, which is what the
side-by-side view is for. There's a bundled demo (sidebar → *Notation/tab
demo*) if your library is PDF-only so far.

[^gp]: Checked against a real, original Guitar Pro 7 (`.gp`) fixture: `web/test-fixtures/guitar-pro-import-fixture.gp`, a few original bars built with alphaTab's own exporter from the alphaTeX source committed beside it, never a borrowed arrangement. `web/tests/browser/guitar-pro-import.spec.js` uploads it through the real `/api/upload` path and the real scanner, opens it through the real `Viewer` → `TabViewer` → `score-render.js` path, and checks the bar count, note count and tuning the real importer actually produced.

## Roadmap

- **Tuplets when transcribing** — the largest remaining gap between a
  transcription and a score you could practise from, and the reason bars still
  overfill once voices have been separated
- **Grace notes when transcribing** — currently read as ordinary notes and
  given a duration they do not have, which overfills the bar they sit in
- **A harmonic's DURATION** — the note is marked as a harmonic, but a diamond
  notehead's own value is still not calibrated, so a harmonic engraved as a
  half note reads as a quarter
- **Instrument-aware transcription** — reading a score against the instrument it
  is written for, instead of assuming standard six-string tuning whatever the
  score says
- **Score creation from scratch** — a blank staff built in the browser,
  instrument-agnostic with first-class guitar tablature support. Editing the
  notes, rhythms and spellings of a staff that already exists works today;
  starting from nothing does not
- **More import formats** — MIDI and plain-text tab in particular, since those
  accompany most freely-licensed sheet music you can download
- **Interval and reach drills** — finding the third, the fifth or the octave
  from a given position, and widening a fret span on purpose. The fretboard
  drills that exist today test recall, not reach
- Recognition for what you have accomplished over time
- Annotations on PDFs (fingerings, markings)
- Multi-user accounts (reverse proxy authentication - trusting a login a
  proxy in front of Fermata already did - shipped; see
  docs/deployment.md#reverse-proxy-authentication)
- Splitting compilation books into individual pieces
- Tablet-first PWA mode with pedal-friendly full-screen
- Recognition for scanned PDFs, which carry no readable text or glyphs

## License

[MIT](LICENSE)
