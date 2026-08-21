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

### What is derived rather than stored

- `reached_target` is `tempo_bpm >= target_tempo_bpm`, computed on read. A
  stored answer is one that can end up contradicting the two numbers it came
  from. `null` means one of them is missing, which is not the same as "did not
  reach it".
- Totals, day counts and goal progress are all counted from sessions when
  asked. Nothing anywhere caches them.

### What deletes a session

`score_id` is `ON DELETE CASCADE`, so removing a score removes its practice.
That is deliberate and narrower than it sounds: the scanner re-links a renamed
or moved file to its existing score row by content hash precisely so a rename
never reaches this cascade, and a score row that genuinely goes means the file
has left the library. `DELETE /api/practice/sessions/{id}` is the only other
way, and exists because a timer left running by accident is otherwise
permanent.

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
met or missed, which is the point of setting one. There is **one goal per
period per owner** - setting another for the same week replaces it, because
changing your mind about the week is the ordinary case and a period with two
goals in it is a scorecard.

The period is stored as its two dates rather than as a week number, so a goal
keeps meaning exactly what it meant when it was set even after the
`week_starts_on` preference changes underneath it.

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
  *What did I actually do this week.*

### Aggregates

- `GET /api/practice/history?days=&today=` - per-day totals, per-piece totals
  and per-activity totals over a window of up to a year. *Where has the time
  gone over three months.*
- `GET /api/scores?practiced=recent|neglected` - the library's own views.
  *Which pieces have I neglected.* These use rolling windows over `started_at`
  rather than the practice day, because "in the last 14 days" is a question
  about elapsed time rather than about calendar days.
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

### Progress, as reported

`progress` on a goal carries `days_practised`, `minutes`, `seconds`,
`sessions`, a `days` array covering **every** day in the period including the
empty ones, `status` (`upcoming`, `running` or `past`), `days_left`, and
`met_days` / `met_minutes` / `met`.

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

Version 2 is the first step: it rebuilds `practice_sessions` so `score_id` can
be `NULL`, which SQLite cannot express as an alteration in place. Every
existing row is carried across with its id intact.
`server/tests/test_practice_migration.py` builds a real version 0 and version 1
database, with real practice rows in them, and checks the rows survive - and
compares the upgraded table against a freshly created one so the two
definitions cannot drift apart.

There is no down direction. A database written by a newer release is refused
outright at startup rather than written to blind; restoring a backup is the way
back. See the Backups section of [deployment.md](deployment.md).

## What is not here yet

- **Per-attempt trainer results** - which positions or intervals were missed,
  and response times. That belongs beside the trainer that produces it, and
  inventing its shape before one exists would be guessing. What the `activity`
  vocabulary guarantees is that when a trainer arrives, the time it accounts
  for lands in the same history and the same goals as everything else.
- **Key, tempo and difficulty per score** - musical metadata about the piece
  rather than about the practice.
- **Achievements** - looking back at what has been accomplished, where a goal
  looks forward from an intention. Designed to share this surface.
