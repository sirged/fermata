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
  and are always `'local'` today, and no endpoint has a login, a token or a
  credential of its own. The one identity this API knows about comes from
  outside it: with reverse-proxy authentication configured, a request that
  did not arrive from a trusted proxy carrying the configured header is
  refused with `401` (`GET /api/health` alone stays open, so a load balancer
  can probe it), and `GET /api/me` reads back the username the proxy
  vouched for. That is off by default — unset, every request behaves exactly
  as it did before it existed, `GET /api/me` answers `{"enabled": false,
  "username": null}`, and nothing else in the API acts on an identity either
  way. See [SECURITY.md](../SECURITY.md) and
  [docs/deployment.md](deployment.md#reverse-proxy-authentication).

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

**Key, tempo and difficulty (issue #8).** Three more fields `PATCH
/api/scores/{id}` accepts, each within a closed range and each clearable with
an explicit `null` the same way `instrument_id` already is: `key` is a
MusicXML `fifths` count (-7..7) - the same number a transcription's
`key_fifths` carries, not a key name such as "D", because a key signature
alone never says major or minor and this API states only what it actually
knows; `tempo` is a manual bpm (20-400), never copied from a transcription's
own tempo reading, which carries no confidence figure to trust; `difficulty`
is a manual 1-5 rating nothing here infers. `GET /api/scores` filters on all
three - `key`, `difficulty` (exact match) and `tempo_min`/`tempo_max` (either
or both) - composing with every filter already documented above. Transcribing
a score (single or in bulk) opportunistically copies its glyph-decoded key
onto a score whose `key` is still null; a hand-set key, or one filled in this
way already, is never overwritten by a later (re-)transcription.

A move or a delete is refused with `409` while a library scan is running, and a
scan declines to start while one is being applied. One thing that **moves or
removes an existing file** runs at a time: a scan decides what to write from a
directory listing taken when it started, so a file moving underneath it would
read as a file that went missing. `POST /api/upload` and `POST
/api/library/folders` are deliberately outside that rule — the first only ever
writes at a path the client itself named, never one it discovered by walking
the library, so it cannot invalidate a scan's listing the way a move or a
delete could; the second creates a directory — and `scanner.hold_library_still`
documents why each is safe.

Uploading onto a path that already holds a file is refused with `409`, naming
the path, unless the request sends `replace=true` — the stored bytes are
untouched until then (issue #293). A replace is not held against a running
scan either, the same as a fresh upload: whichever content that scan reads
back from the path, it reads as an ordinary file that changed on disk, which
is what a person editing the file by hand while a scan runs already produces.
Every upload's receipt names the path a file was saved under (`saved`) and
whether that call `replaced` an existing one, so a client can show where a
file went and whether it overwrote something without inferring either from
the request it sent.

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

## Two people editing the same score (issue #267)

`PUT /api/scores/{id}/transcription` stores a hand edit as the score's
`source='edited'` row. Two clients - a tablet propped on the music stand and
the desktop it was set up from - can both have that row open, and before this
existed the second save simply replaced the first, with nothing anywhere able
to notice it had happened.

The request body may now carry **`expected_updated_at`**: the `updated_at` of
the edited row the edit was made on top of, echoed back as an `If-Match`-style
precondition.

- **Send it, and it matches** - the save is written, and the response carries
  the new `updated_at`. That new value is what the client sends on its *next*
  save; the value it originally loaded is stale the moment its own save lands.
- **Send it, and the stored row has a different `updated_at`** - `409`, and
  **nothing is written**: not the content, and not `updated_at` either, so the
  client whose edit is stored can still save on top of its own row. The
  `detail` object names what is stored now, so a client can offer to load it
  rather than merely apologise:

  ```json
  {
    "detail": {
      "error": "stale_transcription",
      "message": "This score's transcription changed somewhere else after you loaded it, so this save was not written.",
      "updated_at": "2026-09-06 20:57:17.418",
      "source": "edited"
    }
  }
  ```

- **Send it when there is no edited row at all** - `409` as well, with
  `updated_at` and `source` both `null`. The edit this one was made on top of
  was reverted (`DELETE /api/scores/{id}/transcription`) underneath; letting
  the save through would resurrect it silently, which is the same surprise as
  an overwrite seen from the other side. `null` is how a client tells that case
  from the one above and offers the right thing.
- **Omit it (or send `null`)** - the save is written unconditionally.

**Compatibility.** The field is optional and omitting it behaves exactly as
this endpoint behaved before it existed, so no existing client has to change
and a first save (nothing stored yet) sends nothing. Fermata's own viewer and
compare editor both send the value they loaded.

**A note on `updated_at` for an edited row.** Every other `updated_at` in this
API is written with SQLite's `datetime('now')`, which resolves to one second;
two saves inside the same second therefore share a value, and a precondition
compared against one would have been blind for that whole second. The edited
transcription row is stamped to the millisecond instead
(`YYYY-MM-DD HH:MM:SS.mmm`), and every write is guaranteed to move the value
forward even when the clock does not. It is the same column and the same text
ordering - a value written before this change simply has no fractional part -
so a client that treats `updated_at` as an opaque token to echo back needs no
change. **Extracted** rows are unversioned and keep `datetime('now')`: no
client ever writes one.

## Transcribing many scores at once (issue #55)

`POST /api/transcribe/batch` starts a background pass over many scores and
`GET /api/transcribe/batch/status` polls it - the same start/poll shape
`POST /api/scan` and `GET /api/scan/status` use, rather than a client looping
single `POST /api/scores/{id}/transcribe` calls itself. The MCP layer (see
below) does not cover batch transcription in this release: its tools are
read-only, and batch status is deliberately not among them.

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

**A freshly scanned library transcribes itself (issue #190).** The last scan
of a chain - including the one `POST /api/upload` triggers - starts this
same background pass on its own, over exactly the scores that chain added.
A bulk pass already running by hand is never interrupted for this: the
scan's own attempt is simply skipped, recorded in `GET /api/scan/status`'s
`transcribe_batch_started` and `transcribe_batch_note`, never queued or
retried. `GET /api/scores` accepts `transcribed=yes` or `transcribed=no` to
narrow the library to scores with a transcription (extracted or hand-edited
- undistinguished here) or its exact complement.

## Getting everything in and out (issue #58)

Two endpoints, one archive format, and one rule that holds for both directions:
nothing here is a database file you cannot read. `GET /api/export` and
`POST /api/import` are documented individually in `/docs`; this is the shape
that ties them together.

| Endpoint | What it does |
| --- | --- |
| `GET /api/export` | Every score row, transcription, practice session, goal, tag (and which tags are on which score), instrument, setting, setlist (with its ordered membership), both fretboard-drill attempt tables (fret positions and chords) and named drill scope, plus the score files themselves, as one zip. |
| `POST /api/import` | Restores an archive `GET /api/export` produced. **Dry run by default.** |

**The archive.** A zip with `manifest.json` at its root - a JSON object naming
the `schema_version` (`fermata/db.py`'s `SCHEMA_VERSION`, not the
application's own release number) the rest of it was written against, and
carrying every table's rows verbatim under `tables` (drill history included
since #243, named drill scopes since #236). Score files themselves
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
inserted as a new row with a fresh id - the only exceptions are a tag whose
NAME already matches one already in the target library, which is reused
rather than duplicated, and a named drill scope (a "preset") whose NAME
collides with one already in the target library (or with another preset
earlier in the same archive), which is **renamed**, not reused and not
refused: it is inserted under `<name> (imported)` (then `(imported 2)`,
`(imported 3)`, ... - the first free name, compared the same
case-insensitive way the preset's own uniqueness rule is, and folded
ASCII-only the way SQLite's own `COLLATE NOCASE` is - not Python's broader
`casefold()`, which would flag a collision (e.g. `Straße` against
`STRASSE`) that the unique index itself would never raise on), with its
string set intact and its id remapped so the archive's own sessions still
point at the imported copy. A base name at or near
`trainer_scope_presets.name`'s own length cap is trimmed to make room for the
suffix, so the derived name always satisfies the same cap
`POST /api/trainer/presets` enforces on every name it accepts - never a row
longer than any name the API would otherwise let anyone create.
`ImportOut.trainer_scope_presets_renamed` lists every `{from, to}` pair this
produced - empty when nothing collided - identically on a dry run and an
applied import, so a preview never promises a name the real restore would
not actually use. Import never replaces, and never merges
by guessing which of two similarly-shaped rows is "the same one" - a wrong
guess risks silently discarding practice history, which this feature's one
absolute rule is that it never does; renaming a preset is not that guess,
since both the existing preset and the archive's own copy survive under
their own names. Importing the same archive twice therefore creates two
copies of everything (a second import's preset lands under `(imported 2)`,
having found `(imported)` already taken by the first); the library to import
into is an empty one - a fresh install, or one just scanned onto an empty
database.

**An archive from an older Fermata still imports (#275).** `schema_version`
does not have to match the running one. An archive written at schema version
3 or later imports into any Fermata from that version onwards: rows are
inserted with the columns the archive actually carries, so a column added
after it was written fills from the schema instead of being demanded of it
(a version 4 archive restores its practice sessions with `preset_id` empty,
which is what "practised under no named scope" already means). Two things
are still refused, both with a message that says which and change nothing:

- An archive from a **newer** Fermata than the one reading it. Its rows may
  carry columns and meanings this version knows nothing about, so the answer
  is to upgrade first and import again.
- An archive carrying a **table or column this Fermata no longer has** -
  renamed or dropped since it was written. The message names the table, or
  the table and the columns.

Version 3 is the floor because everything below it is on the far side of a
schema change that had to repair practice rows as it went, and import cannot
make that repair on an archive. Restore such an archive with a Fermata that
reads it, let that one bring the database up to date, and export again.

The import response says both numbers: `schema_version_read` is the version
the archive was written at, and `schema_version` is the one its rows now live
under (always the running Fermata's). They are equal for an archive written
by the version reading it.

**Validated completely before anything is written.** The archive is a real
zip, its manifest parses, its `schema_version` is one this Fermata can read
(see above), every foreign key inside the archive
resolves to a row also in the archive, and every archived file's bytes hash
to what the archive itself records for them. A malformed or
incompatible archive is rejected with a clear message and changes nothing -
no database transaction is even opened. The rarer failure - a valid archive
that still collides with something already in the target library once
writing starts (two goals for the same week, say) - rolls the database back
the way every other write in this API does, and removes any files already
written to the library before the failure, so a rejected import always
leaves the library exactly as it was.

**A named drill scope is checked, not just carried (#268).** Every archived
`trainer_scope_presets` row - and the string set
`trainer_scope_preset_strings` carries for it - is run through
`trainer.normalise_preset`, the same call `POST /api/trainer/presets`
itself makes, before anything above is written and before the rename check
described above ever runs. A row the normaliser refuses (a fret or string
number outside this schema's bounds, an empty string set, a key given
without its pair, a name that cleans to nothing or is still over
`trainer_scope_presets.name`'s length cap once cleaned) refuses the WHOLE
import - nothing applied, in either dry run or applied mode - naming the
row by its position in the archive (`trainer_scope_presets row 3`, say)
and the normaliser's own reason, never repairing it. A row the normaliser
only CLEANS (whitespace collapsed, ends trimmed) is not refused: it is
imported under the cleaned name, and #260's collision check runs against
THAT name, not the raw one the archive carried - a whitespace-padded name
that happens to collide once cleaned is renamed exactly as any other
collision would be.

`ImportOut.trainer_scope_presets_renamed` reports both: an entry with no
`reason` key is a plain #260 collision (the archived name was already a
valid, cleanable one - nothing about it needed cleaning); `reason:
"cleaned"` means the archived name only changed because normalising it
changed it, and the cleaned name did not collide with anything; `reason:
"collision"` means both happened - the archived name was cleaned AND the
cleaned name was ALSO already taken, so `to` is that cleaned name's own
#260-derived rename. `from` is always the name exactly as the archive
carried it, whichever reason applies.

**Every table that has a rule is now checked, not only drill scopes (#286).**
#268 checked presets and nothing else, which left the archive - a JSON file
anybody can open, and the documented way to move a library onto this stack -
as the one route into the database that skipped the rules every `POST`
applies. Measured before this changed: a manifest whose `practice_sessions`
row said `seconds: -30` and whose `trainer_attempts` row said
`target_fret: 99` imported with 200 in both modes and stored both, while
posting those same two values to `/api/practice/sessions` and
`/api/trainer/attempts` was refused with 422. Every row of every table below
now goes through the SAME function that table's own route calls, in the same
validate-before-anything-is-written pass #268 runs in:

| Table | The rule it is run through |
| --- | --- |
| `instruments` | `instruments.normalise` |
| `setlists` | `api._clean_setlist_name` (its name is a setlist's only rule) |
| `practice_sessions` | `practice.normalise_session` |
| `practice_goals` | `practice.normalise_goal` |
| `trainer_attempts` | `trainer.normalise_attempt` |
| `trainer_chord_attempts` | `trainer.normalise_chord_attempt` |
| `trainer_scope_presets` | `trainer.normalise_preset` (#268) |

A row the rule REFUSES refuses the whole import - nothing applied, in either
mode - naming the table, the row's position in the archive, 0-based
(`the archive's practice_sessions row 3 is invalid: ...` names the FOURTH row
of that table) and the rule's own reason. Nothing is repaired: a negative
duration, a fret outside the drill's bounds, a goal with no target, an
instrument whose tuning names fewer strings than it claims are all refusals,
never guesses at what was meant.

A row the rule only CLEANS is imported as the value the route would have
stored, and counted in the response's `cleaned` map (table name to row count,
tables with a non-zero count only, so `{}` means nothing needed touching).
That covers a note that was only padding stored as empty, a string pitch
typed `e2` stored as `E2`, a goal's `period_end` recomputed from its own
start, an attempt's `correct` recomputed from the notes the row itself
records (`correct` is never accepted from a caller either, at any route), and
a preset name whose whitespace was collapsed - which
`trainer_scope_presets_renamed` also reports, in more detail. `cleaned` reads
identically on a dry run and an applied import, the same guarantee the rename
list makes.

**Two rules are deliberately NOT applied in full to an archived row**, both
because an archived row is an already-stored row rather than a claim being
made now:

- Only the FLOOR on how far back a practice day may sit from today - not the
  ceiling. `local_date is in the future` is still refused on import exactly as
  it is on `POST /api/practice/sessions`, because restoring an archive still
  claims each session happened on the date it names. What is lifted is the
  lower bound alone: applying it would make every backup older than that
  window unrestorable, which is the opposite of what an archive is for. (This
  is narrower than the exemption `PATCH /api/practice/sessions/{id}` uses,
  which turns the whole window - both bounds - off when `local_date` is not
  the field being changed; import always writes a `local_date`, so it cannot
  use that broader exemption without also silently accepting a future one.)
- The requirement that a session on a piece, or a goal about one, names a
  score. Export itself writes such a row with an empty `score_id` whenever
  the score was left out of the archive or destroyed while its history
  stayed; refusing it on the way back in would discard practice history. This
  one IS passed exactly the way `PATCH /api/practice/sessions/{id}` and
  `PATCH /api/practice/goals/{id}` already pass it for a stored row.

**Tables with no rule to run keep the checks they have always had** - a
manifest that carries them in the right shape, with every foreign key
resolving inside the archive. They are `scores` (created by the scanner from
files on disk, never by a `POST` with a rule of its own),
`tags`, `score_tags`, `transcriptions`, `settings`,
`setlist_scores` and `trainer_scope_preset_strings` (whose rows are the INPUT
to a preset's own rule, deduplicated and bounds-checked there rather than one
row at a time - see #268 above).

**`dry_run` defaults to true**, the same default every bulk operation in this
API uses (see the five rules above). It validates the archive completely and
reports what it found without opening a transaction or writing a file.

## The fretboard drills (issues #27, #28, #236)

Two drills run on the neck — fret to note, and chord flash cards — and both
write the same two kinds of thing. Every answered question is its own
structured row (`POST /api/trainer/attempts` and
`POST /api/trainer/chord-attempts`, listed back by the matching `GET`s), so
"which positions get missed" is a `WHERE` clause rather than something a
reader parses out of prose. The drill's *time* is an ordinary practice session
(`POST /api/practice/sessions`, activity `fretboard` or `chords`) — there is
no separate drill-session row.

**A scope, saved under a name.** What a drill asks about is narrowed by a
scope: which strings, which fret range, and optionally which key. That used to
live in the browser and reset on every page load, leaving nothing behind but
an English sentence in the session's `note`. It is now a row.

| Endpoint | What it does |
| --- | --- |
| `GET /api/trainer/presets` | Every named scope, newest first, each with the strings it allows. |
| `POST /api/trainer/presets` | Save a scope under a name. |
| `DELETE /api/trainer/presets/{id}` | Delete a named scope. The practice logged under it is **not** touched — `sessions_kept` counts it. |

The string set is **one row per string**, in a child table, never a list in a
column and never JSON: "which strings does this scope allow" has to be
answerable by the database (see [docs/practice-data.md](practice-data.md)).
That is also why `strings` is required and may not be empty on the way in —
"every string" is stored by naming every string, so a saved scope can never be
confused with one whose strings failed to write. `key_root` and `key_quality`
travel together or not at all; both absent means every note, which is the
ordinary case.

**Presets are shared, not per-drill.** There is no `drill` column: a scope is a
thing a person is working on, so one saved while naming notes is the same one
the chord drill offers. A name must be unique per owner — a duplicate is
refused with `409`, unlike a setlist, because a preset is picked in order to
change what the next question will be and two identically named entries make
"which scope am I about to practise" unanswerable from the screen.

**A practice session carries `preset_id`.** A drill run on a named scope logs
it on the session, and every session read-back returns it — so "how much time
went into the first five frets in the key of G" is a join rather than a text
search. It is null on almost every row, and that is not a missing value: a
session logged from anywhere but a drill, or from a drill on a scope nobody
named, genuinely has none. Deleting a preset clears the reference and leaves
the session whole: the minutes were still practised. A drill run on an
*unnamed* scope still writes the scope sentence into `note`, because for that
session it is the only trace there is; existing notes are left exactly as they
were.

Named scopes **travel in the portable archive** (issue #58's export / import):
a backup carries each preset, its string set, and each session's reference to
it, and a restore repoints all three at the new rows. An archive written before
these tables existed still imports, with no named scopes and nothing dangling.

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

A companion server speaks the Model Context Protocol, an open standard, and
wraps this REST API rather than reimplementing its logic - which is the
reason "generated but wrong or incomplete" is not good enough here: that
layer's own correctness depends on this one meaning what it says.

That server (issue #31, `server/fermata/mcp_server.py`) is off unless
`FERMATA_MCP` is set, and when it runs it is a CLIENT of this API like any
other: each of its fourteen read-only tools is one documented `GET` from the
list above, called over HTTP, answering with that route's own JSON
unchanged. It never adds an operation to this document - it reads it. The
tool list and every tool's input schema are generated from `app.openapi()`
at startup, so a tool cannot describe a route that no longer looks like
that, and `server/tests/test_mcp_server.py` requires every readable route
here to be either exposed as a tool or recorded with a reason why not -
which means adding or renaming a route in `api.py` fails that test by name
rather than quietly leaving the tool layer behind. Operators turn it on and
publish it as described in
[docs/deployment.md](deployment.md#the-model-context-protocol-server).
