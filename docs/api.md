# The REST API

Fermata's own web frontend is one client of a REST API under `/api`, and that
API is not private to it. Anything that can make an HTTP request can browse
the library, log or query practice, manage instruments, read or write
transcriptions, trigger a scan, and export or import everything Fermata knows
as one portable archive - which is what makes a companion app, a script, or a
scoreboard on a second screen possible without touching this codebase at all.

## Where the contract actually lives

The API is documented by generating its OpenAPI schema rather than by hand:
every route in `server/fermata/api.py` declares a `response_model` (a Pydantic
model in `server/fermata/api_models.py`), a docstring, and a tag, and FastAPI
turns that into the same schema Swagger UI and any codegen read.

- **`GET /docs`** - interactive Swagger UI: every route, its parameters, its
  request and response shapes, and a "Try it out" button that calls a real,
  running instance.
- **`GET /openapi.json`** - the schema itself, for generating a client or
  feeding a tool that reads OpenAPI directly.

Both are served by the same process as the API itself - there is nothing
separate to stand up or keep in sync. `server/tests/test_api_docs.py` pins
that the generated document validates against the OpenAPI spec, that every
route carries a summary, a description, a tag and a response schema, and
that FastAPI's response-model validation is genuinely switched on for this
app rather than merely declared in a decorator - so an endpoint added without
documenting it fails that test, with a message naming what is missing,
rather than shipping undocumented.

## What to expect between releases

Fermata does not yet version this API independently of the application - the
number `GET /api/version` reports is the application's own release, and there
is no `/api/v2` alongside `/api/v1`. Until that changes, the practical
expectations are:

- **A new field reaches clients once its response model carries it, not the
  moment a handler starts computing it.** Every response is filtered through
  a Pydantic model (`server/fermata/api_models.py`) before it leaves the
  server, so a field a handler adds to its own return value is invisible on
  the wire until the matching model grows to match - see, for example, how
  the transcription endpoints' Rule 8 conformance figures and provenance
  fields (`bars_defective`, `time_signature_source`, and the rest - see
  `TranscriptionOut`) had to be added to that model, not only to the
  handler, to actually reach a reader (issues #143, #146). Growing a field
  in the ordinary case is not a breaking change; forgetting to grow it is a
  bug, and it is the one `server/tests/test_api_docs.py` actually guards
  against: every request its tests make is checked against the RAW value the
  handler returned, captured (via `fastapi.routing.run_endpoint_function`)
  before response_model ever touched it, against what actually reached the
  wire - and fails naming the exact route and field the moment a model falls
  behind its handler, across every endpoint group in one mechanism rather
  than one hand-written pin per model.
- **A field already documented is not silently repurposed or removed.**
  Renaming or dropping a field, or changing what a value means, is called out
  in the release it ships in.
- **`null` is a meaningful answer, not "not implemented yet".** Several
  fields across this API are `null` on purpose - a Rule 8 figure nothing has
  measured, a goal's `sessions_inferred` when the goal is not countable, a
  transcription's provenance on a hand edit - and each is documented as such
  in `api_models.py` rather than treated as a gap to fill in later. A client
  should not infer "this will become non-null once the feature is finished".
- **This is a single-owner, self-hosted server**, not a multi-tenant service.
  `owner` fields exist in several tables for a future multi-account version
  and are always `'local'` today; nothing in the API takes or checks
  credentials yet (see [SECURITY.md](../SECURITY.md)).

The data model behind the practice endpoints specifically - what a session
and a goal mean, what is derived versus stored, and what deliberately has no
column - is documented in more depth in
[docs/practice-data.md](practice-data.md); the MusicXML this API's
transcription endpoints read and write is documented in
[docs/musicxml-tab-profile.md](musicxml-tab-profile.md).

## The endpoints that write to your files

Everything else in this API reads the library and writes only to Fermata's own
database. The library-management endpoints (issue #56) move, rename and delete
a person's own sheet music, so they are documented here as a group as well as
individually in `/docs`:

| Endpoint | What it does |
| --- | --- |
| `POST /api/scores/{id}/move` | Moves one score's file to another folder, renames it, or both. |
| `POST /api/library/move` | Moves several scores into one folder. **Dry run by default.** |
| `GET /api/library/folders` | The library's folder tree, for offering destinations. |
| `POST /api/library/folders` | Creates a folder. |
| `POST /api/library/folders/rename` | Renames a folder, taking its scores with it. **Dry run by default.** |
| `DELETE /api/scores/{id}` | Deletes a score: the file goes to the trash, the row and its history stay. |
| `GET /api/trash` | Scores that have been deleted and not destroyed. |
| `POST /api/trash/{id}/restore` | Puts a deleted score back where it came from. |
| `DELETE /api/trash/{id}` | Destroys a deleted score for good. The only endpoint here that really deletes. |

Five rules hold across all of them, and a client can rely on each:

- **Nothing is written outside the library folder.** The check is on the
  resolved path, so a symlink out of the library is refused as well as a `..`.
- **Deleting is a move.** The file goes to a `.fermata-trash` folder inside the
  library and the score row is marked with `deleted_at`; its practice sessions,
  goals, tags and transcription stay attached, and the response counts each of
  them. Destroying takes a second, deliberate request.
- **Nothing is destroyed as a side effect of an organisational change.** A move
  onto an existing file is refused rather than overwriting it, and a batch
  containing one blocked line applies none of it.
- **A bulk operation is a dry run unless `dry_run: false` is sent.** The
  response shape is the same either way, so the preview is a preview of the
  thing itself.
- **The score row follows the file by content hash** - the same identity test
  the scanner's relink uses - so a move cannot attach one score's practice
  history to another score's music.

Moving a file re-derives only `collection` and `series`, which are read off the
folders. `title`, `composer` and `source` are statements about the music, can
have been corrected by hand, and are edited with `PATCH /api/scores/{id}`
instead - which is why renaming a file and renaming a piece are two different
requests here.

A move or a delete is refused with `409` while a library scan is running, and a
scan declines to start while one is being applied. One thing that **moves or
removes an existing file** runs at a time: a scan decides what to write from a
directory listing taken when it started, so a file moving underneath it would
read as a file that went missing. `POST /api/upload` and `POST
/api/library/folders` are deliberately outside that rule — the first only ever
creates a file at a path nothing claims, the second creates a directory — and
`scanner.hold_library_still` documents why each is safe.

### What a deleted score may still be asked for

A deleted score's row is still there, which is what makes deleting recoverable,
so every endpoint that takes a score id can still reach one:

- **Reads answer normally** — `GET /api/scores/{id}`, its `/file`, `/thumb`,
  `/transcription`, `/practice` and `/practice/progress`. The trash view is
  built out of exactly those responses, and being able to look at a score
  before destroying it for ever is the point of a trash you can change your
  mind from.
- **Writes are refused with `409`** — `PATCH /api/scores/{id}`, logging practice
  against it by either route, setting a goal about it, and extracting, saving or
  deleting its transcription. Each means "work on this piece"; nothing in the
  interface offers them for a score in the trash.
- **Practice already logged is untouched and still counted.** A deleted score
  still appears in `practice/summary`'s `top_scores` and in `practice/history`'s
  `by_score`, with its hours — dropping it would leave those breakdowns not
  adding up to the totals beside them. Both carry `deleted: true`, and
  `practice/sessions` carries `score_deleted: true` on each session naming that
  piece, so a client can stop offering a route into a score the library no
  longer holds. `score_deleted` is **not** `score_missing`: the first means the
  score is in the trash and can be put back, the second means its row is gone
  and there is no piece left to name.
  `GET /api/scores/{id}/practice/progress` answers in full for a deleted score
  too, with `deleted: true` — refusing would be this API deciding a deletion
  erases practice, and the hours were still spent.

Deleting a score whose file has **already** gone is allowed and answers
`file_moved: false` with `trashed_to: null` — nothing was moved, because there
was nothing to move. Restoring it (or any score whose trashed file has since
been removed by hand) answers `file_restored: false` and puts the score back in
the library flagged `missing_since`, which is the state it was in before.

## Transcribing many scores at once (issue #55)

`POST /api/transcribe/batch` starts a background pass over many scores and
`GET /api/transcribe/batch/status` polls it - the same start/poll shape
`POST /api/scan` and `GET /api/scan/status` use, rather than a client looping
single `POST /api/scores/{id}/transcribe` calls itself. The planned MCP
layer (see below) wraps this endpoint for exactly that reason.

**Selection.** Give `score_ids` (an explicit list, honoured exactly - even an
id that turns out not to be a pdf or to be in the trash gets its own outcome
rather than vanishing), or `collection` (every live pdf score under one
folder), or neither (every live pdf score in the whole library). Giving both
is a `422`.

**Every score gets an outcome - never a silent skip.** `results` on the
status response carries one line per score: `transcribed`,
`already_transcribed` (with why - already extracted, or hand-edited and
therefore protected), `non_extractable` (with the extractor's own reason), or
`errored` (with its own reason, e.g. a missing file). `reconvert: true` asks
an already-EXTRACTED score to be re-run; an EDITED transcription is never
replaced, `reconvert` or not (issue #10's protection, applied in bulk).

**Not a dry run, and not held against a running scan, in either direction.**
Unlike the library-management endpoints above, this never moves, renames or
deletes a file - it only reads a score's PDF and writes to the
`transcriptions` table - so there is nothing here for a scan's directory
listing to be invalidated by, and no destructive action needing a preview
first. A scan may start while a batch is running and a batch may start while
a scan is running; each score's row is read fresh at its own turn rather than
from a snapshot taken when the batch started.

**No job state persists across a restart.** A killed pass leaves only
complete, already-committed rows behind - nothing half-written - so
"resuming" is simply starting a fresh pass over the same selection: the
scores already done come back `already_transcribed` and the rest are
attempted for the first time.

## Getting everything in and out (issue #58)

Two endpoints, one archive format, and one rule that holds for both directions:
nothing here is a database file you cannot read. `GET /api/export` and
`POST /api/import` are documented individually in `/docs`; this is the shape
that ties them together.

| Endpoint | What it does |
| --- | --- |
| `GET /api/export` | Every score row, transcription, practice session, goal, tag, instrument, setting and setlist (with its ordered membership), plus the score files themselves, as one zip. |
| `POST /api/import` | Restores an archive `GET /api/export` produced. **Dry run by default.** |

**The archive.** A zip with `manifest.json` at its root - a JSON object naming
the exact `schema_version` (`fermata/db.py`'s `SCHEMA_VERSION`, not the
application's own release number) the rest of it was written against, and
carrying every table's rows verbatim under `tables`. Score files themselves
live under `files/<content-hash><extension>`, named by the same identity the
scanner already uses (`scanner.hash_file`) rather than by a person's folder
names, which is what lets two scores that happen to share content share one
entry instead of two. `include_trash` (default true) decides whether a score
currently in the trash - deleted but not yet destroyed, see the
library-management section above - travels too; leaving it true is what makes
an export a real backup, since a restorable score left out of one is data
loss the moment the original library is gone. `include_files` (default true)
decides whether the score files' own bytes are bundled at all - score files
are already ordinary files in a folder, so `include_files=false` is for
someone moving the library folder across by other means and wanting the
archive to carry only the part that is not already portable that way.

**What import does, exactly: it ADDS.** Every row from a validated archive is
inserted as a new row with a fresh id - the only exception is a tag whose
NAME already matches one already in the target library, which is reused
rather than duplicated. Import never replaces, and never merges by guessing
which of two similarly-shaped rows is "the same one" - a wrong guess risks
silently discarding practice history, which this feature's one absolute rule
is that it never does. Importing the same archive twice therefore creates two
copies of everything; the library to import into is an empty one - a fresh
install, or one just scanned onto an empty database.

**Validated completely before anything is written.** The archive is a real
zip, its manifest parses, its `schema_version` matches this Fermata's exactly
(cross-version migration is not implemented yet - restore an old archive with
the Fermata version that wrote it), every foreign key inside the archive
resolves to a row also in the archive, and every archived file's bytes hash
to what the archive itself records for them. A malformed or
incompatible archive is rejected with a clear message and changes nothing -
no database transaction is even opened. The rarer failure - a valid archive
that still collides with something already in the target library once
writing starts (two goals for the same week, say) - rolls the database back
the way every other write in this API does, and removes any files already
written to the library before the failure, so a rejected import always
leaves the library exactly as it was.

**`dry_run` defaults to true**, the same default every bulk operation in this
API uses (see the five rules above). It validates the archive completely and
reports what it found without opening a transaction or writing a file.

## Setlists (issue #6)

A setlist is an ordered collection of scores a player works through — a gig
set, a lesson plan, a practice rotation. The order is the server's: it is a
stored `position`, not the order rows happen to come back in, so a reorder is a
real write and not something a client arranges and a reload forgets.

| Endpoint | What it does |
| --- | --- |
| `GET /api/setlists` | Every setlist, newest first, each with its `score_count`. |
| `POST /api/setlists` | Create a new, empty setlist with a name. |
| `GET /api/setlists/{id}` | One setlist and its scores, in order. |
| `PATCH /api/setlists/{id}` | Rename it. |
| `DELETE /api/setlists/{id}` | Delete the setlist. Its scores are **not** touched — `scores_untouched` counts them. |
| `POST /api/setlists/{id}/scores` | Add a score, appended at the end. |
| `DELETE /api/setlists/{id}/scores/{score_id}` | Remove a score from the setlist. The score is **not** deleted. |
| `PUT /api/setlists/{id}/order` | Set the whole order — `score_ids` must be exactly the current members, each once. |

**What removing and deleting do not do.** Removing a score from a setlist
removes one membership row and nothing else — the score, its file, its practice
history, its tags and its transcription stay, and it stays in every other
setlist it is in. Deleting a setlist reaches only its membership rows; the
scores are untouched. A score can be in any number of setlists at once.

**A deleted score in a setlist (issue #56).** A member whose score is in the
trash is still listed, carrying its `score.deleted_at` — a client marks it as
deleted rather than showing a broken link, and it keeps its place in the order.
It cannot be newly added while trashed, the same way every other write against a
trashed score is refused. A score **purged** from the trash leaves its setlists
on its own, because the membership row is removed with the score row.

**Practising a setlist reuses the ordinary viewer** — there is no separate
"gig session" row. Each member carries the same practice totals the library and
progress views show (issue #32's one-source-of-truth rule), so a client shows
per-piece progress within a setlist without counting anything itself.

Setlists **travel in the portable archive** (issue #58's export / import): a
backup carries each setlist and its ordered membership, and a restore repoints
both foreign keys at the new setlist and score rows so the arrangement survives
intact. A membership row for a score the export leaves out (a trashed one, when
`include_trash=false`) is dropped from the archive rather than carried as a
dangling reference — the setlist itself still travels, just without that member.

## Who else reads this contract

A planned companion server speaks the Model Context Protocol, an open
standard, and is meant to wrap this REST API rather than reimplement its
logic - which is the reason "generated but wrong or incomplete" is not good
enough here: that layer's own correctness depends on this one meaning what it
says.
