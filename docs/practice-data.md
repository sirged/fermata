# The practice data model

What Fermata records about practice, how it is stored, and how to ask it
questions. Two things live here: a **session**, which is a fact about work that
was done, and a **goal**, which is an intention with a period attached.

Practice history is the only data in Fermata that cannot be regenerated. A
score row can be rebuilt by rescanning the library and a transcription by
re-extracting, but nothing on disk remembers that somebody sat down and
practised for forty minutes. Everything below is designed around that.

## Sessions

One table, `practice_sessions`, for every kind of practice.

| Column | Meaning |
| --- | --- |
| `id` | Stable for the life of the row, including across schema upgrades. |
| `owner` | `'local'` until real accounts exist. |
| `score_id` | The piece, when the work was against one. `NULL` otherwise. |
| `activity` | What kind of work it was. See the vocabulary below. |
| `mode` | `section`, `run_through`, or `NULL` for unstated. |
| `started_at` | UTC timestamp of when the session was **recorded**. |
| `local_date` | The calendar day the practice **happened** on, in the practiser's own time. |
| `seconds` | How long, 1 to 86400. |
| `from_bar`, `to_bar` | The bars worked. |
| `from_page`, `to_page` | The pages worked - for a PDF, where bar numbers are not addressable. |
| `tempo_bpm` | The tempo actually practised at. |
| `target_tempo_bpm` | The tempo being worked towards. |
| `rating` | 1-5, the player's own sense of how it went. `NULL` for not rated. |
| `note` | Free text, up to 2000 characters. |
| `preset_id` | The named drill scope this was practised under (see **Trainer scope presets** below), or `NULL`. |

### Why there is one table and not one per kind

Practice is not only pieces. The exercises that come next - fret-to-note, ear
training, chord drills - produce practice in exactly the way a piece does, and
so does sitting down and playing. A table per kind would mean every history
view and every goal calculation growing another special case with each of
them, forever. So `activity` says what a row was, and `score_id` is present
when there was a piece behind it.

`activity` is one of: `piece`, `technique`, `sight_reading`, `ear_training`,
`fretboard`, `chords`, `improvisation`, `theory`, `free`, `other`. `piece` is
the only value that requires a `score_id`; `free` is unstructured playing.

### Why the practice day is stored rather than derived

`started_at` is UTC. West of Greenwich an evening session falls on the next UTC
day, so "how many days did I practise this week" would be wrong by one for
exactly the sessions somebody is most likely to have - and at a week boundary
it would land in the wrong week entirely, against a goal counting days. So the
client sends `local_date`, and every day-based query counts that.

A back-dated session is a first-class thing rather than a contradiction:
`started_at` is when it was **recorded** and `local_date` is when the practice
**happened**. Somebody entering yesterday's forgotten hour is lying about
neither.

Rows written before this column existed hold `NULL`, and are attributed to
`date(started_at)` because that is the only day they ever recorded. Every
session response carries `local_date_source`, which is `recorded` or `utc_date`,
so a reader is never handed an inferred day as though it were a recorded one.
The practice page says which beside the date on every session row, because a
distinction that only exists in the API is one nobody is ever told about.

An inferred day is filed in whichever week UTC puts it in, while the page asks
for the week around the practiser's own day — so west of Greenwich, an evening's
practice from before this column existed can sit in the week after the one it
happened in. Storing the day is what fixed that going forward and is the reason
the column exists; a row that predates it cannot be fixed that way. Which is a
reason to mark such a day rather than a reason to stop counting it, but the mark
is carrying two facts at once: the day was not recorded, and the week it landed
in is not necessarily the practiser's own.

### What is derived rather than stored

- `reached_target` is `tempo_bpm >= target_tempo_bpm`, computed on read. A
  stored answer is one that can end up contradicting the two numbers it came
  from. `null` means one of them is missing, which is not the same as "did not
  reach it".
- Totals, day counts and goal progress are all counted from sessions when
  asked. Nothing anywhere caches them.

### What deletes a session

Almost nothing. `score_id` is `ON DELETE SET NULL`: removing a score forgets
which piece the practice was about and keeps the practice. The hours were spent
whether or not the file is still on disk, and the record of them belongs to the
person rather than to the file - which is the whole premise of this feature and
the reason these rows are the one thing here that cannot be regenerated.

A session that outlives its score keeps `activity = 'piece'` with no
`score_id`, and that pair is what identifies it: every other activity may
legitimately have no score, and a `piece` session cannot be *created* without
one. Every session response carries `score_missing` for exactly this, so a
reader can name it as practice on a piece that has gone rather than showing a
blank where a title belongs. Nothing filters these rows out of any total, any
day count or any goal.

`DELETE /api/practice/sessions/{id}` is the only way to remove a session, and
exists because a timer left running by accident is otherwise permanent.

A dangling `score_id` - a reference to a score that is not in the table, which
this application cannot produce but the `sqlite3` command line produces
trivially, since its default is `foreign_keys` off - is repaired to `NULL` on
the next startup and announced in the log. That is what the reference's own `ON
DELETE SET NULL` would have done had the deletion gone through the database.

## Goals

One table, `practice_goals`. A goal says how many days somebody means to
practise and for how long in total, over one period, optionally scoped to one
piece or one kind of work.

| Column | Meaning |
| --- | --- |
| `period` | `'week'`. Nothing else is implemented. |
| `period_start`, `period_end` | Inclusive dates. Stored, not derived. |
| `target_days` | Days with practice in them, 1 to 7. |
| `target_minutes` | Total minutes across the period. |
| `scope` | `all`, `score`, or `activity`. |
| `score_id`, `activity` | Whichever the scope names. The other is `NULL`. |
| `intent` | What they mean to work on, in their words. |
| `reflection`, `realistic` | Written afterwards, by them, and by nothing else. |

At least one target is required: a goal has to be concrete enough to be either
met or missed, which is the point of setting one. Setting another goal for the
same period replaces it - changing your mind about the week is the ordinary
case, and a period with two goals in it is a scorecard. The reflection is
cleared when that happens: it was written about the intention being replaced,
and carrying it over would have the review ask whether a goal was realistic and
answer with words about a different one.

**No two goals may share a day.** Not merely no two with the same start date:
overlap is refused with a 409 naming the period that clashes. Overlapping goals
are how the same practice gets counted against two intentions, and it becomes
reachable through the ordinary interface the moment `week_starts_on` changes,
because the new grid's weeks are offset from the old grid's rather than being
different weeks.

The period is stored as its two dates rather than as a week number, and those
dates are what everything reads. A goal therefore keeps meaning exactly what it
meant when it was set, and changing `week_starts_on` afterwards cannot re-slice
it: the review lists each goal's **own** period rather than matching goals to
slots on today's grid. Matching by grid slot meant the same history reported a
different result after a preference change, and a past week carrying somebody's
own intent and reflection rendered as "no goal was set" - a false statement
about their own record.

A goal about a piece that has since left the library reports
`progress.countable = false` with `met` as `null`. Its sessions are still in the
history but no longer identifiable as being about that piece, so it cannot be
counted - and saying so is the only honest answer. Reporting zero days would
turn a week somebody practised into a shortfall, and a goal already reached into
one that was not.

**There is no column recording whether a goal was met, and there will not be
one.** Progress is counted from the sessions inside the period every time it is
asked for. That is what keeps a goal and the history it is about from ever
disagreeing, and it is the only version of this that stays true when an hour is
remembered and logged three days late.

### What happens when a period ends

Nothing. No archiving, no closing, no grading, no deletion. The goal stays with
its targets and whatever was written about it, because a record of what
somebody meant to do is what makes the next goal a better one. A finished
period gains one question - *was this goal realistic?* - whose answer is
`realistic` and whose detail is `reflection`.

### What is deliberately absent

- **No streaks, and no run of anything.** Missing a week because of a busy job
  is information, not a moral failure.
- **No comparison between periods.** No best week, no average, no ranking. A
  best week is the mechanism by which a good month becomes the standard a bad
  month is measured against. No query here returns one and no field invites
  one.
- **No pace advice.** A running period reports the days left in it and nothing
  about what is needed per day to catch up, which is a verdict wearing
  arithmetic.
- **No verdict vocabulary.** The interface states counts - "3 of 4 planned
  days" - and stops. `web/src/lib/practice.js` owns the phrasing and carries
  the word list its tests check it against.
- **No trend line, and no rate of improvement.** This one bites hardest on the
  per-piece view (#57), where there is a single subject and a row of numbers
  about it. The tempo points are stated with their days and joined in the order
  they happened; nothing fits a line through them, reports a slope, or says a
  piece is coming on. A piece is put down for a fortnight and picked up again
  by design, and a direction drawn through that is a claim about somebody's
  playing these numbers cannot support.
- **No average rating.** Counts per rating and never a mean, for the same
  reason a drill records counts and never a rate: a number out of five with a
  decimal point on it is a mark rather than a fact, and it invites a colour.

## Asking questions

Every field is readable over the REST API, and the endpoints are shaped around
the questions rather than around the tables.

### Sessions

- `POST /api/scores/{id}/practice` - log practice against a piece. Only
  `seconds` is required; the response carries the new session's `id` so detail
  can be added afterwards.
- `POST /api/practice/sessions` - log practice that need not be against a
  piece.
- `PATCH /api/practice/sessions/{id}` - add or correct detail. The whole record
  is re-checked, so a patch cannot reach a state a fresh log would be refused.
  An explicit `null` clears a field.
- `DELETE /api/practice/sessions/{id}`
- `GET /api/practice/sessions?start=&end=&score_id=&activity=&limit=` - the raw
  record across every piece and every kind of work, filtered by practice day.
  *What did I actually do this week.* Returns `total` and `truncated` beside the
  rows: a list that stops at the limit and says nothing looks identical to a
  complete one, and a reader totalling it would report less practice than there
  was. `/practice/history` does the same for its by-piece breakdown, with
  `scores_worked` and `by_score_truncated`.

### Aggregates

- `GET /api/practice/history?days=&today=` - per-day totals, per-piece totals
  and per-activity totals over a window of up to a year. *Where has the time
  gone over three months.*
- `GET /api/scores/{id}/practice/progress?days=&today=&limit=` - **how one
  piece is going.** *Where am I with this piece.* One response, because
  reassembling it from the general endpoints meant a client filtering the whole
  history by `score_id` and doing the arithmetic itself - the arithmetic a
  second reader of this API would then write again and get subtly differently.
  It carries:

  | Block | What it answers |
  | --- | --- |
  | `all_time` | Sessions, seconds, minutes, and the **first** and **last** practice day. The one block no window bounds. |
  | `window` | `period_facts` scoped to this piece: every day in the window including the empty ones, and the window's totals. |
  | `tempo` | One point per session that recorded a tempo, oldest first, with its day, its target and `reached_target`. |
  | `modes` | Section work against run-throughs, and the sessions that said neither. |
  | `ratings` | How many sessions got each 1-5 rating, and how many got none. Every bucket present, whether or not it was ever chosen. |
  | `goals` | Goals scoped to **this piece** whose period touches the window, each with its progress. Never a `scope='all'` goal. |
  | `sessions` | This piece's own sessions in the window, newest first, with their notes - and `session_total` / `sessions_truncated` beside them. |

  `practised` says whether the piece has ever been practised at all, which is a
  different fact from a window of zeros and is what lets an interface show a
  real empty state rather than a screen of noughts. `deleted` is true for a
  piece in the trash: it answers in full and is still counted, and only the way
  into the library goes away. `grouped_by` names the column every figure was
  grouped by, always `local_date` - see *Why the practice day is stored rather
  than derived*.

  On the tempo block specifically: `comparable` is false for a single point.
  One session at a tempo is one session at a tempo, and a view that draws it as
  a progression is inventing the thing the reader came to look for.
  `sessions_without_tempo` is how much of the piece's practice those points say
  nothing about. `axis_low` and `axis_high` span both the tempos and the
  targets and exist so a chart's bounds are decided once rather than by each
  reader separately; **they are not a personal best**, nothing states them as
  text, and no field here compares one point to another. `latest_target` is the
  target most recently written down, not the highest ever set - somebody who
  decided 140 was too fast and set 110 is aiming at 110.
- `GET /api/scores?practiced=recent|neglected` - the library's own views.
  *Which pieces have I neglected.* Windowed on the practice day, like
  everything else: the library is the view a person sees first, so it must not
  disagree with the practice page about when they last played something, and a
  back-dated session counts from the day it says it happened. `last_practiced`
  on a score is that day, not a timestamp.
- `GET /api/practice/summary` - the last seven days, for the library header.

### Goals and the review

- `GET /api/practice/goals/current?today=` - the goal covering today, with
  progress. `goal: null` is an answer, not an error.
- `GET /api/practice/goals?limit=&today=`
- `POST /api/practice/goals?today=` - set or replace the goal for a period.
  Omit `period_start` for the week containing `today`.
- `PATCH /api/practice/goals/{id}?today=` - adjust targets while the period
  runs, or write the reflection after it ends.
- `DELETE /api/practice/goals/{id}` - deletes the intention, never the
  practice.
- `GET /api/practice/review?weeks=&today=` - recent weeks, each with its goal
  if it had one, its own facts either way, and where its time went. *What did
  they plan versus what did they do.*

### `today`, on every endpoint that needs a date

The server's own date is UTC, and whether a period is still running must not be
an accident of the hour: west of Greenwich the UTC date is already tomorrow
while somebody still has their evening. So a client passes its own date as
`today`, and a client that passes nothing gets the server's UTC date.

It is bounded to the same window a practice day may name - roughly a year back
and a day forward. Unbounded, `today=2099-01-01` was answered with a
plausible-looking empty week, which is the worst kind of wrong answer because
nothing in the response marks it as suspect. The window is deliberately as wide
as the back-dating window rather than as narrow as a timezone: reasoning about a
period that has already ended is a legitimate use of the parameter, and the
thing that makes every period rule here testable without waiting for the
calendar.

### Progress, as reported

`progress` on a goal carries `days_practised`, `minutes`, `seconds`,
`sessions`, a `days` array covering **every** day in the period including the
empty ones, `status` (`upcoming`, `running` or `past`), `days_left`,
`countable`, and `met_days` / `met_minutes` / `met`.

It also carries `sessions_inferred`: how many of the sessions behind those
totals had no recorded practice day and were attributed to their UTC one. A
single session already says which it is; a total said nothing, so a window
spanning the upgrade quietly added two kinds of day together. Zero on any
install that has only ever run this version, and the week's own statement says
so when it is not.

A `met_*` value is `null` when that target was not set, which is not the same
as unmet - a goal with no minutes target has nothing to say about minutes, and
`false` there would read as a shortfall against a target nobody chose.

## Schema changes

`server/fermata/db.py` holds two mechanisms, and they are not interchangeable.

`COLUMN_ADDITIONS` expresses `ADD COLUMN` and nothing else. It runs on every
startup, so idempotence is its only safety property, and a change that can be
expressed there still belongs there - it repairs a database that half-upgraded.

`MIGRATIONS` is the real runner: ordered steps, each taking a database from the
version below its key to that key, run once and then stamped into `PRAGMA
user_version`. A step may do anything SQLite can do, because it is not re-run.
All pending steps share one transaction and each stamps its own version as it
completes, so an interrupted upgrade resumes from the last step that actually
landed, and the work and its stamp cannot disagree.

Steps run **before** the schema script and before `COLUMN_ADDITIONS`, so a step
sees the database exactly as the previous version left it: no table `SCHEMA`
would have created, and no column `COLUMN_ADDITIONS` would have added. A step
that needs such a column has to add it itself. The order matters the other way
too: `SCHEMA` creates indexes over columns that only exist once a rebuild has
happened.

A step that rebuilds a table runs with foreign keys **off**, as SQLite's own
table-rebuild recipe says to. With them on, one pre-existing row whose reference
had come loose would be rejected on the copy and take startup down on every
subsequent boot. Consistency is checked rather than assumed: dangling practice
references are repaired to `NULL`, anything else remaining is reported, and both
are announced in the log.

Version 2 rebuilds `practice_sessions` so `score_id` can be `NULL`; version 3
rebuilds both practice tables so a deleted score sets that reference to `NULL`
rather than cascading the practice away. Every existing row is carried across
with its id intact, by name rather than by position.
`server/tests/test_practice_migration.py` builds real version 0, 1 and 2
databases with real practice rows in them - including a hand-edited one with a
dangling reference - and checks the rows survive, then compares each upgraded
table against a freshly created one so the two definitions cannot drift apart.

There is no down direction. A database written by a newer release is refused
outright at startup rather than written to blind; restoring a backup is the way
back. See the Backups section of [deployment.md](deployment.md).

## Trainer attempts

The per-attempt table this section spent a long time refusing to guess at.
*Hear a note, name it* (issue #61) was the first trainer and deliberately did
not get one - one exercise was not enough to know whether the unit was a
position, an interval, or a pitch, and designing a table around the first
would have been the same guess this section was written against. *Fret to
note* (issue #27) is the second, and it decided: the unit is one QUESTION, and
every question - whichever direction a fretboard drill asks it in - reduces to
two facts, a note being tested and a note the answer named.

One table, `trainer_attempts`, independent of `practice_sessions`:

| Column | Meaning |
| --- | --- |
| `id` | Stable for the life of the row. |
| `owner` | `'local'`, same as everywhere else. |
| `session_id` | The `practice_sessions` row logging the surrounding drill's TIME, when there is one yet - `NULL` otherwise. See below for why that is the ordinary case, not an edge one. |
| `drill` | Which exercise asked it. `'fret_to_note'` today; a second fretboard drill would widen this tuple, not require a migration. |
| `direction` | `position_to_note` (shown a position, asked for its note) or `note_to_position` (shown a note, asked to find a position). |
| `target_string`, `target_fret` | The position a question NAMED - set only on `position_to_note`, where there is one. A note prompt has no single expected position (most notes sound in several places), so these stay `NULL` on `note_to_position`. |
| `target_note` | The note being tested, always set. |
| `given_string`, `given_fret` | The position a TAP answered with - set only on `note_to_position`. `NULL` on `position_to_note`, where the answer was a note choice and no position was touched. |
| `given_note` | The note the answer actually named, always set. On `note_to_position` this is the note that SOUNDS at the tapped position, worked out from the instrument's own tuning by the client that drew the neck - never from what the player meant to tap, the same rule ear_training's question-from-what-sounded already applies one layer up. |
| `correct` | `given_note = target_note`. Computed server-side from the two note fields, unconditionally, the same rule for both directions - never accepted from a client. |
| `response_ms` | How long the question took, informational. |
| `created_at` | When the row was written. |

**Why exactly one of the two position pairs is ever populated, decided by
`direction`.** A position-to-note question has a target position and a chosen
note for an answer; a note-to-position question has a target note and a
tapped position for an answer. Trying to force both directions into the same
"target position, given position" shape would leave a `note_to_position` row
inventing a target position it never had, or a `position_to_note` row
inventing a tapped position that was never touched.

**Why `session_id` is usually `NULL`, and that is not a gap.** An attempt is
written the moment a question is answered - mid-drill, typically well before
the practice_sessions row that will carry the drill's total TIME even exists
(that one is written when the drill stops, the same as every other activity).
Linking every attempt to a session was never the point; querying "which
positions get missed" is, and that needs no session_id at all - see the
endpoints below. `ON DELETE SET NULL`, for the same reason `score_id` is on
`practice_sessions`: deleting the session that logged a drill's time is not a
reason to forget that a question was asked and answered.

**Structured and queryable (issue #32), not a JSON blob and not a free-text
note.** "Which positions am I weak on" is `WHERE correct = 0 GROUP BY
target_string, target_fret`, not something parsed out of a sentence. The
drill's own `practice_sessions` row still carries a human-readable summary in
its `note` - the same as every other activity - so a reader of the practice
page is not left with a blank line; the structured record lives here instead
of being the only place the time was spent.

**Counts, never a rate.** There is no accuracy percentage anywhere a query
over `trainer_attempts` could produce one, and no column or view counts a
run of consecutive `correct` rows - the same rule the rest of this document
holds to (no streaks, no best week, no average rating). A `correct = 0` row
is a fact worth finding, not a mark against anyone; a query is free to count
right and wrong separately and never free to divide one by the other.

### Asking questions

- `POST /api/trainer/attempts` - log one answered question.
- `GET /api/trainer/attempts?drill=&direction=&correct=&session_id=&limit=` -
  the raw, queryable record, newest first, with `total` and `truncated`
  beside it like every other list here. A client asking "which positions am I
  weak on" filters `correct=false` and groups the results itself, rather than
  this API keeping a bespoke aggregate endpoint in step with every future
  drill's own idea of what a weak spot is.

## Trainer chord attempts

The question this document's own "what is not here yet" section left open:
whether a chord drill's attempts fit `trainer_attempts` or need a table of
their own. *Chord flash cards* (issue #28) answered it - a chord does not
fit. `trainer_attempts`' `target_note`/`given_note` are `NOT NULL` columns
holding exactly one pitch class each, which is the right shape for a drill
whose unit is one note; a chord is a SET of them, and forcing its tone set
into a single-note column would either lose which chord was asked (keeping
only its root) or store something that plainly is not a pitch class. So this
is a sibling table, `trainer_chord_attempts`, built the same way
`trainer_attempts` is: `correct` computed once, server-side, never trusted
from a request body.

| Column | Meaning |
| --- | --- |
| `id` | Stable for the life of the row. |
| `owner` | `'local'`, same as everywhere else. |
| `session_id` | The `practice_sessions` row logging the surrounding drill's TIME, when there is one yet - `NULL` otherwise, the same ordinary-not-edge case `trainer_attempts.session_id` is. |
| `drill` | `'chord_flashcards'` today - a widened tuple, not a migration, is how a second chord-shaped drill would arrive, the same rule `trainer_attempts.drill` follows. |
| `direction` | `shape_to_name` (shown a real fingering, asked to name the chord) or `name_to_shape` (named a chord, asked to place it on the neck). |
| `target_root`, `target_quality` | The chord being tested, always set - a pitch class and one of `major`/`minor`/`dominant7`. |
| `target_shape` | The fingering actually SHOWN - a JSON array of `{string, fret}` - set only on `shape_to_name`; `NULL` on `name_to_shape`, which shows no shape at all. |
| `given_root`, `given_quality` | The chord chosen by name - set only on `shape_to_name`. |
| `given_notes` | The pitch classes a TAPPED shape actually sounded, a JSON array worked out client-side from the instrument's own tuning - never from which shape the player meant to play, mirroring `given_note`'s rule above. Set only on `name_to_shape`. |
| `given_shape` | The positions actually tapped, a JSON array of `{string, fret}` - set only on `name_to_shape`, kept beside `given_notes` so "which shapes get missed" stays a real question over this table. |
| `correct` | Whether the given tone SET equals the target chord's own tones (`chord_tones(target_root, target_quality)`, mirrored character-for-character between `server/fermata/trainer.py` and `web/src/lib/trainer/chord-theory.js`) - the same rule either direction, computed here and only here. There is more than one right way to play a given chord, so this is never a check against one canonical fingering. |
| `response_ms` | How long the question took, informational. |
| `created_at` | When the row was written. |

**One `correct` rule covers both directions, the same idea `trainer_attempts`
already applies to a single note, widened to a set.** `shape_to_name`
compares the CHOSEN chord's tones to the target's; `name_to_shape` compares
what was actually TAPPED (resolved to pitch classes client-side, the same
trust boundary `given_note` already crosses) to the target's. Tone-set
equality, not label equality and not fingering equality, either way.

### Asking questions

- `POST /api/trainer/chord-attempts` - log one answered chord question.
- `GET /api/trainer/chord-attempts?drill=&direction=&correct=&root=&quality=&session_id=&limit=` -
  the raw, queryable record, newest first - the same shape
  `GET /api/trainer/attempts` offers, with `root`/`quality` added so "which
  chords am I weak on" is a filter rather than a client-side group-by.

## Trainer scope presets

A **scope** is what narrows a drill to what somebody is actually working on:
which strings, which fret range, and optionally which key. Both fretboard
drills have always had one. Until issue #236 it lived only in the browser and
reset on every page load, and the only trace it left was an English sentence
in the session's `note` - which is exactly the free-text blob this document's
rules exist to keep facts out of. It is two tables now.

`trainer_scope_presets`:

| Column | Meaning |
| --- | --- |
| `id` | `AUTOINCREMENT`, so a deleted preset's id is never handed to the next one. |
| `owner` | `'local'`, as everywhere else here. |
| `name` | What the person called it. Unique per owner. |
| `start_fret`, `end_fret` | The fret range, 0-36, `start_fret` never past `end_fret`. |
| `key_root`, `key_quality` | The key, `major` or `minor` - both set or both `NULL`, and both `NULL` means every note. |
| `created_at` | UTC timestamp. |

`trainer_scope_preset_strings`, one row per string:

| Column | Meaning |
| --- | --- |
| `preset_id` | The preset. `ON DELETE CASCADE` - a row saying "(this preset) includes (string 3)" states nothing once the preset is gone. |
| `string_number` | 1-24. `PRIMARY KEY (preset_id, string_number)`, so a string is in a preset at most once. |

**A set is not a column, and it is not JSON.** "Which strings does this scope
allow" has to be answerable by the database, for the same reason
`trainer_attempts` has columns rather than a blob: a reader that must parse
`"[1,2,3]"` out of a text field cannot ask anything about it. That is also why
an empty string set is refused on the way in - "every string" is stored by
naming every string, so a saved scope can never be confused with one whose
strings failed to write.

**Shared, not per-drill.** There is no `drill` column and there will not be
one: a scope is a thing a person is working on, not a thing one question
format owns, so a scope saved while naming notes is the same row the chord
drill offers.

**What this does to `note`.** A drill run on a named scope logs
`practice_sessions.preset_id` and stops writing the scope sentence into
`note` - the fret range, the strings and the key are in a row to join on, and
repeating them as prose would put the same fact in two places, one of them
free text. The counts stay in `note`: they are about how the session went, not
about what it was scoped to. A drill run on an *unnamed* scope still writes
the whole sentence, because for that session it is the only trace there is,
and notes already stored are left exactly as they were.

`preset_id` is `ON DELETE SET NULL`, decided the way every other reference
here is - by asking whether the row still says anything once the thing it
names is gone. It does: the minutes were still practised, on that day, in that
activity. Deleting a preset is a tidy-up, never a statement that the practice
did not happen.

### Asking questions

- `GET /api/trainer/presets` - every named scope, newest first, each with its
  string set.
- `POST /api/trainer/presets` - save one. A duplicate name is `409`.
- `DELETE /api/trainer/presets/{id}` - delete one; `sessions_kept` counts the
  practice it leaves behind.
- "How much time went into this scope" is a filter over `practice_sessions` on
  `preset_id`, joined to the preset - not a bespoke aggregate endpoint, the
  same rule the rest of this surface holds to.

## What is not here yet

- **Achievements** - looking back at what has been accomplished, where a goal
  looks forward from an intention. Designed to share this surface.

Key, tempo and difficulty per score (issue #8) shipped as plain columns on
`scores`, not on this surface - see docs/api.md's note on `PATCH
/api/scores/{id}` for what each holds and why. They are metadata about the
piece rather than about the practice, which is why they live there rather
than here.
