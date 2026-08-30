# The REST API

Fermata's own web frontend is one client of a REST API under `/api`, and that
API is not private to it. Anything that can make an HTTP request can browse
the library, log or query practice, manage instruments, read or write
transcriptions, and trigger a scan - which is what makes a companion app, a
script, or a scoreboard on a second screen possible without touching this
codebase at all.

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
  `/transcription` and `/practice`. The trash view is built out of exactly those
  responses, and being able to look at a score before destroying it for ever is
  the point of a trash you can change your mind from.
- **Writes are refused with `409`** — `PATCH /api/scores/{id}`, logging practice
  against it by either route, setting a goal about it, and extracting, saving or
  deleting its transcription. Each means "work on this piece"; nothing in the
  interface offers them for a score in the trash.
- **Practice already logged is untouched and still counted.** A deleted score
  still appears in `practice/summary`'s `top_scores` and in `practice/history`'s
  `by_score`, with its hours — dropping it would leave those breakdowns not
  adding up to the totals beside them. Both now carry `deleted: true` so a
  client can stop offering a route into a score the library no longer holds.

Deleting a score whose file has **already** gone is allowed and answers
`file_moved: false` with `trashed_to: null` — nothing was moved, because there
was nothing to move. Restoring it (or any score whose trashed file has since
been removed by hand) answers `file_restored: false` and puts the score back in
the library flagged `missing_since`, which is the state it was in before.

## Who else reads this contract

A planned companion server speaks the Model Context Protocol, an open
standard, and is meant to wrap this REST API rather than reimplement its
logic - which is the reason "generated but wrong or incomplete" is not good
enough here: that layer's own correctness depends on this one meaning what it
says.
