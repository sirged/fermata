import logging
import sqlite3
import threading
from contextlib import contextmanager

from .config import DB_PATH

_local = threading.local()

# Startup is the one place in this application that has something to say and no
# person watching, so it says it through the logger uvicorn has already
# configured rather than into a response nobody asked for.
log = logging.getLogger("fermata.db")

# The only owner that exists until real accounts do. Kept as a named constant
# rather than the literal 'local' scattered wherever a write means "this
# instance's one user", so the day accounts arrive, every such site is a grep
# away from being migrated to a real owner id instead of a schema redesign.
DEFAULT_OWNER = "local"

# The practice session table's columns, written once and used twice: SCHEMA
# below builds the table from it on a fresh install, and the version 2
# migration builds the same table under a temporary name to carry existing
# rows into. Two copies of this definition would be two chances for an
# upgraded database to end up with a subtly different table from a fresh one -
# see test_practice_migration.py, which compares the two and fails if they
# ever disagree.
#
# WHY score_id IS NULLABLE, and why that needed a migration rather than an
# ADD COLUMN. Practice is not only pieces. The exercises this schema has to
# carry next - fret-to-note, ear training, chord drills - produce practice
# with no score behind them at all, and so does simply sitting down and
# playing. The alternative was a second table per kind of practice, after
# which every history view and every goal calculation would need one more
# special case forever. So there is ONE session table, and `activity` says
# what kind of work a row was; `score_id` is present when the work was
# against something in the library and NULL when it was not.
#
# `local_date` is the calendar day the practice happened on IN THE
# PRACTISER'S OWN TIME, and it is stored rather than derived because it
# cannot be derived. `started_at` is UTC, so west of Greenwich an evening
# session falls on the next UTC day - and "how many days did I practise this
# week" is then wrong by one for the evenings, which is precisely the number
# a goal is counted against. Rows written before this column existed have
# NULL here and are attributed to date(started_at), because that is the only
# day they ever recorded; readers say so rather than presenting a guess as a
# fact (see practice.LOCAL_DATE_SQL and the local_date_source key on a
# session response).
#
# Whether a tempo ladder reached its target is NOT stored: it is
# tempo_bpm >= target_tempo_bpm, and a stored answer is one that can end up
# contradicting the two numbers it was computed from.
#
# ON DELETE SET NULL, AND WHY IT IS NOT CASCADE. Deleting a score means the
# file has left the library. It does not mean the hours were not spent. The
# practice happened, and the record of it is a property of the person's
# history rather than of a file on disk - which is the entire premise of this
# feature and the reason these rows are the one thing here that cannot be
# regenerated. Cascade also made this project inconsistent with itself: the
# scanner goes to real lengths to re-link a renamed file to its existing score
# row by content hash SPECIFICALLY so that a rename does not destroy practice
# history through this reference, and score management is an explicit upcoming
# feature (#56), so a person deleting a score while tidying up is not
# hypothetical. Protecting history from a rename while surrendering it to a
# delete is not a position worth holding.
#
# A session that outlives its score keeps activity='piece' with no score_id,
# and that pair is exactly what identifies it: every other activity may
# legitimately have no score, and a 'piece' session cannot be CREATED without
# one (see practice.normalise_session). Nothing filters these rows out - they
# carry their day, their length, their tempo, their bars and their note, which
# is enough to be practice that happened. See practice.session_dict's
# `score_missing`.
_PRACTICE_SESSIONS_COLUMNS = """(
    id INTEGER PRIMARY KEY,
    owner TEXT NOT NULL DEFAULT 'local',
    score_id INTEGER REFERENCES scores(id) ON DELETE SET NULL,
    activity TEXT NOT NULL DEFAULT 'piece',
    mode TEXT,
    started_at TEXT NOT NULL,
    local_date TEXT,
    seconds INTEGER NOT NULL,
    from_bar INTEGER,
    to_bar INTEGER,
    from_page INTEGER,
    to_page INTEGER,
    tempo_bpm INTEGER,
    target_tempo_bpm INTEGER,
    rating INTEGER,
    note TEXT
)"""

# Kept as separate statements rather than one script so SCHEMA can be
# assembled from them, and so a future migration that needs to build this table
# under another name has the index set to hand without restating it.
#
# idx_practice_day is an index on the EXPRESSION every day-based query filters
# and groups by, not on the column - because the column alone cannot serve
# them. A row written before local_date existed is attributed to its UTC day,
# so every such query reads COALESCE(local_date, date(started_at)) (see
# practice.LOCAL_DATE_SQL), and a plain index on local_date cannot be used for
# a wrapped column: SQLite would scan the owner's entire practice history to
# answer a question about seven days of it. The expression here must stay
# character-identical to LOCAL_DATE_SQL apart from the table alias, or the
# planner silently stops using it - test_practice_model.py checks the query
# plan rather than trusting that.
_PRACTICE_SESSIONS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_practice_score ON practice_sessions(score_id)",
    "CREATE INDEX IF NOT EXISTS idx_practice_day ON practice_sessions"
    "(owner, COALESCE(local_date, date(started_at)))",
    "CREATE INDEX IF NOT EXISTS idx_practice_started ON practice_sessions(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_practice_activity ON practice_sessions(owner, activity)",
)

# A goal is a record of INTENT, and it is deliberately not a record of a
# verdict. There is no met/missed column and no status: everything about how a
# period actually went is counted from practice_sessions when asked (see
# practice.goal_progress), so a goal cannot drift out of agreement with the
# history it is about, and nothing here accumulates into a scorecard.
#
# `reflection` and `realistic` are the person's own words about their own
# week, written after it ends - the useful question after a missed week is
# whether the goal was unrealistic or the week was unusual, and only they can
# answer it. Nothing else writes these two columns.
#
# The period is stored as its two inclusive dates rather than as a week
# number, so a goal keeps meaning exactly what it meant when it was set even
# if the week-start preference changes underneath it. `period` is 'week' and
# nothing else today; it is here so a longer period arrives as a value rather
# than as a second table.
#
# One goal per period per owner (the unique index): "a goal for the week" is
# one intention with one focus. Several concurrent goals would turn the
# review into a scorecard with rows to lose on.
#
# AUTOINCREMENT for the same reason instruments have it: goals are routinely
# deleted, and a plain INTEGER PRIMARY KEY hands a deleted row's id to the
# next one, so a reflection typed into an open tab could land on a different
# goal than the one on screen.
#
# ON DELETE SET NULL on score_id, for the same reason the session table has it.
# A goal is a record of an intention, and tidying a file out of the library is
# not a reason to forget having formed one. What it CANNOT do is go on being
# counted: the sessions that were about that piece are still in the history but
# no longer identifiable as being about it, so the goal becomes uncountable
# rather than unmet - see practice.goal_progress's `countable`, which exists so
# that a goal already reached cannot be turned into a shortfall by a file being
# deleted afterwards.
_PRACTICE_GOALS_COLUMNS = """(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL DEFAULT 'local',
    period TEXT NOT NULL DEFAULT 'week',
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    target_days INTEGER,
    target_minutes INTEGER,
    scope TEXT NOT NULL DEFAULT 'all',
    score_id INTEGER REFERENCES scores(id) ON DELETE SET NULL,
    activity TEXT,
    intent TEXT,
    reflection TEXT,
    realistic TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""

_PRACTICE_GOALS_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_practice_goals_period"
    " ON practice_goals(owner, period_start)",
)

_PRACTICE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS practice_sessions " + _PRACTICE_SESSIONS_COLUMNS + ";\n"
    + "".join(f"{statement};\n" for statement in _PRACTICE_SESSIONS_INDEXES)
    + "CREATE TABLE IF NOT EXISTS practice_goals " + _PRACTICE_GOALS_COLUMNS + ";\n"
    + "".join(f"{statement};\n" for statement in _PRACTICE_GOALS_INDEXES)
)

# One row per QUESTION a fretboard drill asked - the per-attempt detail
# docs/practice-data.md's "What is not here yet" section always said would
# wait for a second trainer to decide its shape (issue #32). Issue #27, fret
# to note, is that second trainer, and this is the shape it decided on: not a
# JSON blob and not a free-text note, because "which positions are missed" has
# to be a WHERE clause, not a thing a client parses out of a sentence.
#
# WHY ONE 'correct' RULE COVERS BOTH DIRECTIONS OF THE DRILL. A question is
# asked one of two ways - shown a position and asked for its note, or shown a
# note and asked to find a position that sounds it - and the two look like
# they would need two different ways of grading. They do not: every attempt
# stores `target_note` (the note being tested) and `given_note` (the note the
# answer actually named), and `correct` is `given_note = target_note`,
# unconditionally, computed here rather than trusted from the client. For the
# position-to-note direction `given_note` is literally the note the player
# picked. For the note-to-position direction `given_note` is the note that
# SOUNDS at the position they tapped, worked out from the instrument's tuning
# by the same client that drew the neck - see web/src/lib/trainer/neck.js's
# noteAt. That mirrors ear-training.js's own rule ("the question is built from
# what was SOUNDED, never from what was asked for"): the server does not need
# to know a tuning to grade an attempt, only to compare two notes it was told.
#
# target_string/target_fret are the position a question NAMED, and are NULL
# on the note-to-position direction: a note prompt has no single expected
# position, since most notes sound in several places, so there is nothing
# there to record as "the" target position - only which note was asked for.
# given_string/given_fret are the position a TAP answered with, and are NULL
# on the position-to-note direction, where the answer was a note choice and no
# position was ever touched. Exactly one of the two position pairs is
# populated, decided by `direction`.
#
# `drill` is a free second axis on purpose, alongside `direction`: fret to
# note is the first trainer to write here, and is not assumed to be the last
# fretboard exercise this table will ever hold (#26, #29). Widening the
# CHECK in trainer.py's DRILLS is how a second one arrives; the table itself
# needs no migration for that.
#
# ON DELETE SET NULL on session_id, for the same reason score_id is on
# practice_sessions: an attempt is a fact about a question that was answered,
# and deleting the practice_sessions row that logged the surrounding drill's
# TIME (see api.log_session, activity='fretboard') does not undo the
# answering. session_id is nullable in the other direction too - an attempt is
# written the moment it is answered, mid-drill, before the session row that
# will eventually carry the drill's total time even exists, so most attempts
# are never linked to one at all. Querying "how did this session go" was
# never the point; querying "which positions get missed" is, and that needs
# no session_id at all.
_TRAINER_ATTEMPTS_COLUMNS = """(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL DEFAULT 'local',
    session_id INTEGER REFERENCES practice_sessions(id) ON DELETE SET NULL,
    drill TEXT NOT NULL,
    direction TEXT NOT NULL,
    target_string INTEGER,
    target_fret INTEGER,
    target_note TEXT NOT NULL,
    given_string INTEGER,
    given_fret INTEGER,
    given_note TEXT NOT NULL,
    correct INTEGER NOT NULL,
    response_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""

_TRAINER_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS trainer_attempts " + _TRAINER_ATTEMPTS_COLUMNS + ";\n"
    + "CREATE INDEX IF NOT EXISTS idx_trainer_attempts_owner_drill"
    " ON trainer_attempts(owner, drill, created_at);\n"
    + "CREATE INDEX IF NOT EXISTS idx_trainer_attempts_session"
    " ON trainer_attempts(session_id);\n"
    # The index a "which positions get missed" query actually needs: every
    # attempt this drill's position-to-note direction wrote, grouped by the
    # position it was about. Left off target_note/correct because SQLite can
    # apply those as a filter over this index's own rows without a second
    # lookup - narrowing the indexed columns to what the WHERE and GROUP BY
    # need is what keeps this from scanning a whole drill's history to find
    # one string's weak frets.
    + "CREATE INDEX IF NOT EXISTS idx_trainer_attempts_target"
    " ON trainer_attempts(owner, drill, target_string, target_fret);\n"
)

# One row per QUESTION the chord flash card drill (issue #28) asked - a
# SIBLING of trainer_attempts, not a widening of it. trainer_attempts'
# target_note/given_note columns are NOT NULL and hold exactly one pitch
# class each, which is the correct shape for a drill whose unit is one
# note - see that table's own comment. A chord is not one note; it is a SET
# of them, and forcing target_root/target_quality's tone set into a single
# target_note column would either lose which chord was asked (storing only
# its root) or store something that is not a pitch class at all. So a
# chord attempt gets its own table, built the same way: `correct` computed
# once, server-side (trainer.normalise_chord_attempt), never trusted from a
# request body, and an index shaped for the query this drill's players
# actually want to ask.
#
# WHY ONE 'correct' RULE COVERS BOTH DIRECTIONS, mirroring trainer_attempts'
# own reasoning. A chord is its pitch-class SET (server/fermata/trainer.py's
# chord_tones, mirrored character-for-character from web/src/lib/trainer/
# chord-theory.js's chordTones), and every attempt reduces to comparing two
# such sets: the question's own tones (from target_root/target_quality) and
# what was actually given. For shape_to_name, "given" is a chosen chord
# (given_root/given_quality) - a chord NAME was picked, and its tones are
# compared directly. For name_to_shape, "given" is a JSON array of the
# pitch classes a tapped shape actually sounded (given_notes), worked out
# client-side from the instrument's own tuning the same way
# trainer_attempts' given_note is for a tapped position (see fret-to-
# note.js's checkTapAnswer) - the server does not need to know a tuning to
# grade a chord attempt, only to compare the tone sets it was told.
#
# target_shape (shape_to_name only) is the fingering that was actually
# SHOWN, and given_shape (name_to_shape only) is the positions actually
# TAPPED - both JSON arrays of {string, fret}, kept beside the graded tone
# sets so "which shapes get missed" stays a real question over this table
# rather than something only the tone sets could answer. Both are NULL on
# the direction that does not produce them, the same "exactly one side
# populated, decided by direction" rule trainer_attempts' target/given
# position pairs already follow.
_TRAINER_CHORD_ATTEMPTS_COLUMNS = """(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL DEFAULT 'local',
    session_id INTEGER REFERENCES practice_sessions(id) ON DELETE SET NULL,
    drill TEXT NOT NULL,
    direction TEXT NOT NULL,
    target_root TEXT NOT NULL,
    target_quality TEXT NOT NULL,
    target_shape TEXT,
    given_root TEXT,
    given_quality TEXT,
    given_notes TEXT,
    given_shape TEXT,
    correct INTEGER NOT NULL,
    response_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""

_TRAINER_CHORD_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS trainer_chord_attempts "
    + _TRAINER_CHORD_ATTEMPTS_COLUMNS + ";\n"
    + "CREATE INDEX IF NOT EXISTS idx_trainer_chord_attempts_owner_drill"
    " ON trainer_chord_attempts(owner, drill, created_at);\n"
    + "CREATE INDEX IF NOT EXISTS idx_trainer_chord_attempts_session"
    " ON trainer_chord_attempts(session_id);\n"
    # "Which chords get missed" - the query this table exists to answer -
    # groups by the chord that was ASKED, so that is what the index leads
    # with, correct left off for the same reason trainer_attempts' own
    # target index leaves it off: SQLite applies it as a filter over these
    # indexed rows rather than a second lookup.
    + "CREATE INDEX IF NOT EXISTS idx_trainer_chord_attempts_target"
    " ON trainer_chord_attempts(owner, drill, target_root, target_quality);\n"
)


# The scores table's columns, written once, for the same reason
# _PRACTICE_SESSIONS_COLUMNS is: SCHEMA builds the table from it, and any future
# step that has to REBUILD the table (SQLite cannot alter a constraint in place,
# so a rebuild is how any constraint change here will arrive) has the definition
# to hand instead of restating it. #94's lesson was that two copies of a
# definition are two chances for an upgraded database to differ from a fresh
# one; this is the same lesson applied before it can bite.
#
# THAT IS NOT A TIDINESS POINT, it is what stops a mark from being lost. Before
# this existed, `missing_since` lived only in COLUMN_ADDITIONS, so a rebuild
# driven from the schema text would have created a scores table without it -
# silently turning every missing score back into a present one, which is the
# bug this column exists to prevent, reintroduced by a future migration nobody
# would suspect. Anything added to scores from now on belongs HERE.
#
# deleted_at / deleted_from: what a person deleting a score through Fermata
# leaves behind (#56). A deletion here is a MARK and a move, never a DELETE: the
# file goes to a trash folder inside the library, `deleted_from` remembers the
# library-relative path it came from so restoring puts it back where it was, and
# `deleted_at` says when - which is the question actually asked of a trash
# ("what did I throw away this afternoon?"), and one a boolean could not answer.
# The row, and the practice history, tags, goals and transcription hanging off
# it, stay exactly where they were, which is the whole reason this is not a
# DELETE. `path` moves to the file's location inside the trash folder, because
# that column has to keep naming where the bytes actually are and is UNIQUE -
# leaving the old path on a deleted row would block a new file arriving at it.
#
# These two are deliberately NOT the same fact as missing_since, and the two are
# never confused for one another. missing_since is a fact about the filesystem
# that Fermata observed; deleted_at is a decision a person made. A missing score
# is still in the library and says so on its card; a deleted one is in the trash
# until it is restored or destroyed.
#
# missing_since: NULL means the file was there the last time a scan looked; a
# timestamp means it was not, and says since when - which is the question
# somebody actually asks about a row like this ("has that been gone since the
# drive died, or since I tidied up in March?"). A boolean could not answer it.
# Nothing filters on it: a missing row still appears in the library, still
# carries its tags, practice and transcription, and still answers to its id,
# because "your library is intact, these files are not reachable right now" is
# the true thing to show somebody whose drive has not come back - and an empty
# library is not. See scanner.py.
#
# instrument_id is deliberately NOT here and stays in COLUMN_ADDITIONS: it
# references instruments(id), and that table is created further down this same
# script, so a forward reference in the CREATE TABLE would depend on the order
# of two statements rather than on anything either one says.
_SCORES_COLUMNS = """(
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    composer TEXT,
    collection TEXT,
    series TEXT,
    source TEXT,
    path TEXT NOT NULL UNIQUE,
    file_type TEXT NOT NULL,
    content_kind TEXT NOT NULL DEFAULT 'unknown',
    pages INTEGER,
    favorite INTEGER NOT NULL DEFAULT 0,
    hash TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    last_page INTEGER NOT NULL DEFAULT 1,
    missing_since TEXT,
    deleted_at TEXT,
    deleted_from TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS scores " + _SCORES_COLUMNS + ";\n"
) + """
CREATE INDEX IF NOT EXISTS idx_scores_collection ON scores(collection);
CREATE INDEX IF NOT EXISTS idx_scores_title ON scores(title);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- ON DELETE CASCADE HERE IS DELIBERATE, and it was reconsidered rather than
-- inherited - see the same note over `transcriptions`, and the paragraph below
-- it explaining what actually protects these rows.
CREATE TABLE IF NOT EXISTS score_tags (
    score_id INTEGER NOT NULL REFERENCES scores(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (score_id, tag_id)
);

-- One extracted row and (at most) one edited row per score, distinguished by
-- `source`. Kept as separate rows rather than one row with fields that get
-- overwritten in place, so a re-extraction can freely replace the extracted
-- row without ever touching an edited one - see api.py's transcribe().
--
-- ON DELETE CASCADE, AND WHY THESE TWO TABLES KEEP IT WHILE THE PRACTICE
-- TABLES DID NOT. #95 asked the question for score_tags and transcriptions
-- that #94 answered for practice_sessions and practice_goals, and the answer
-- comes out the other way. The test is whether a row still SAYS anything once
-- the score it names is gone.
--
-- A practice session says "forty minutes on Tuesday, at 92bpm, bars 1-16, felt
-- rough". A goal says "practise five days this week". Those are complete
-- statements about somebody's week; they read perfectly well with no piece
-- named, which is exactly why SET NULL keeps something worth keeping there.
--
-- A score_tags row says "(this score) (this tag)" and nothing else - it is
-- purely the association. A transcriptions row says "here is the music of
-- (this score)". Take the score away and neither has a statement left: an
-- orphaned tag link is a count against a name nobody can see, and orphaned
-- alphaTex or MusicXML is notation nothing can title, list, search or open.
-- Worse, both would accumulate silently and for ever. SQLite treats NULLs as
-- distinct in a unique index, so PRIMARY KEY (score_id, tag_id) and
-- idx_transcriptions_score_source would both stop constraining the orphans -
-- unlimited duplicate rows nothing can reach, and transcribe()'s one-row-per-
-- source invariant quietly false for them.
--
-- WHAT ACTUALLY PROTECTS THIS WORK, then, is that the scanner no longer
-- deletes a score row when its file goes: it sets scores.missing_since and
-- leaves everything hanging off the row exactly where it was (see
-- scanner.py). A hand-corrected transcription is real work, and the way to not
-- lose it is to keep the row it hangs from - which a NULL link could not do,
-- because nothing would then record what the notation had been about.
--
-- HOW FAR THAT GOES, stated exactly, because the easy version of this sentence
-- is wrong. A file that comes back UNCHANGED finds its transcription again:
-- either at the path it left from, or under a new name through the content-hash
-- relink, and in both cases the row is the same row and the work is still on
-- it. A file that was EDITED AND MOVED does not. Its content hash no longer
-- matches anything on disk, so the relink has nothing to match; the new path
-- becomes a new row with no transcription, and the old row keeps the
-- transcription while matching no file. The work is preserved and readable
-- against a score that is marked missing, which is better than the delete this
-- replaced, but it is permanently detached from the file it describes and
-- nothing can reunite them automatically. That is a real limit of this design
-- and not an argument for nulling the link, which would lose the work outright
-- instead of detaching it.
--
-- So the cascade now fires only for a deliberate, explicit deletion of a score
-- by a person (#56) - never as a side effect of a filesystem walk coming back
-- short. Whoever builds that feature should say plainly in the interface that
-- tags and transcriptions go with it.
CREATE TABLE IF NOT EXISTS transcriptions (
    id INTEGER PRIMARY KEY,
    score_id INTEGER NOT NULL REFERENCES scores(id) ON DELETE CASCADE,
    format TEXT NOT NULL DEFAULT 'alphatex',
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'extracted',
    confidence TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_transcriptions_score_source ON transcriptions(score_id, source);

-- Key/value rather than a column per setting, so adding the next preference
-- needs no migration. `owner` is unused today - every row is written with
-- DEFAULT_OWNER - but it is here from the start so introducing real accounts
-- later is a data migration (giving rows real owner ids) rather than a
-- schema redesign.
CREATE TABLE IF NOT EXISTS settings (
    owner TEXT NOT NULL DEFAULT 'local',
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (owner, key)
);

-- An instrument as a player has it in hand, in one tuning: the same guitar in
-- standard and in dropped D is two rows, because the tuning is what anything
-- downstream needs. `fretted` is not decoration - a fret is a discrete
-- position, so position reasoning and tablature only apply when it is 1, and
-- fret_count/capo are rejected outright on an unfretted row rather than stored
-- and ignored (see fermata/instruments.py).
--
-- `string_pitches` is a JSON array of pitch names ("E2", "F#2"), ordered
-- highest string NUMBER first, which is how tuning already travels through
-- this codebase (tabextract.DEFAULT_TUNING, musicxml.open_string_midi). One
-- column rather than a row per string because a tuning is only ever read and
-- written whole, and its order is part of its meaning - a child table would
-- need an explicit ordinal column to say the same thing.
--
-- `reference_pitch` is in Hz. Not everyone tunes to A440 and period
-- instruments generally do not, so a string's sounding frequency is only
-- determined once this is known.
--
-- `owner` mirrors the settings table: unused today, every row written with
-- DEFAULT_OWNER, present from the start so real accounts arrive as a data
-- migration rather than a schema redesign.
-- AUTOINCREMENT, unlike every other table here: a plain INTEGER PRIMARY KEY
-- reuses the largest rowid once its row is deleted, so deleting an instrument
-- and defining another would hand the new one the old one's id. Any id held
-- outside the database - an open settings tab, a request already in flight,
-- scores.instrument_id in a backup being restored - would then silently name a
-- different instrument. Instruments are the first table here that is routinely
-- deleted from, and this is free to add now and a migration later.
--
-- `kind` is 'string' and nothing else today - string_count, string_pitches and
-- `fretted` all presuppose it - but it is here from the start because the
-- intent is to describe any instrument eventually. Note that fretted=0 means an
-- unfretted STRING instrument (a violin), which is not the same thing as "not a
-- string instrument", and must not be pressed into service as though it were:
-- that is what this column is for. Having it now means a piano or a voice
-- changes what a definition may CONTAIN rather than changing the shape of every
-- response, once there is data to migrate.
CREATE TABLE IF NOT EXISTS instruments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL DEFAULT 'local',
    kind TEXT NOT NULL DEFAULT 'string',
    name TEXT NOT NULL,
    fretted INTEGER NOT NULL DEFAULT 1,
    string_count INTEGER NOT NULL,
    string_pitches TEXT NOT NULL,
    fret_count INTEGER,
    capo INTEGER,
    reference_pitch REAL NOT NULL DEFAULT 440.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_instruments_owner ON instruments(owner);

-- A setlist is an ordered collection of scores a player works through - a gig
-- set, a lesson plan, a practice rotation (#6). It is the one thing here that a
-- person arranges deliberately by hand, so the order it holds is a STORED FACT
-- (setlist_scores.position below), never insertion-order or rowid luck that a
-- reorder could not actually change.
--
-- AUTOINCREMENT, for the same reason instruments and goals have it: a setlist
-- is routinely deleted, and a plain INTEGER PRIMARY KEY hands a deleted row's
-- id to the next one - so a rename typed into an open tab, or an id held by a
-- second reader (the planned MCP server #31, a bookmarked gig-mode URL), could
-- silently act on a different setlist than the one it meant.
--
-- `owner` mirrors settings, instruments and the practice tables: unused today,
-- every row written with DEFAULT_OWNER, present from the start so real accounts
-- arrive as a data migration rather than a schema redesign.
CREATE TABLE IF NOT EXISTS setlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL DEFAULT 'local',
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_setlists_owner ON setlists(owner);

-- The membership: which scores are in a setlist, and in what order. One row per
-- (setlist, score) - a score appears in a given setlist AT MOST ONCE (the
-- PRIMARY KEY below), because a setlist is an arrangement of distinct pieces
-- and letting one appear twice makes "remove it" and "move it" ambiguous about
-- which copy is meant. A score MAY be in any number of DIFFERENT setlists -
-- that is the whole point of setlists, and nothing here constrains it.
--
-- `position` is the explicit order, and it is exactly what a reorder writes:
-- rows are read ORDER BY position, and adding a score appends it at
-- MAX(position)+1. There is deliberately NO unique constraint on position, so a
-- reorder can rewrite the whole column row by row without a transient collision
-- (two rows momentarily sharing a value) aborting it; the final values are
-- distinct because the API writes a permutation.
--
-- TWO ON DELETE ACTIONS, each chosen the way #94/#95 taught for score_tags and
-- transcriptions - the test is whether the row still SAYS anything once the
-- thing it names is gone:
--
--   setlist_id ON DELETE CASCADE. A membership row is purely the association -
--   "(this setlist) holds (this score) at (this position)" - exactly like a
--   score_tags row. Delete the setlist and it has no statement left, and would
--   otherwise accumulate silently forever. So deleting a setlist removes its
--   membership rows and NOTHING ELSE: the scores, and every score's practice
--   history, tags and transcriptions, are untouched, because this table is the
--   only place a setlist ever reached.
--
--   score_id ON DELETE CASCADE, and this one needs #56 spelled out because a
--   naive reading looks like it would drop a trashed score from its setlists.
--   It does not. A score deleted through Fermata is SOFT-deleted: the row stays,
--   marked deleted_at, sitting in the trash. Its membership rows therefore stay
--   too, and a setlist holding a trashed score still lists it - rendered MARKED
--   as deleted rather than as a broken link (see api.setlist_dict, and the
--   invariant in #6). The cascade fires only when a score is PURGED from the
--   trash (DELETE FROM scores - #56's second, deliberate step), at which point
--   the score is gone for good and a membership row naming it says nothing: the
--   same fate, for the same reason, as that score's tags and transcriptions.
--   SET NULL is NOT the alternative it is for a practice session: a session
--   with no score is still "forty minutes that happened", but a membership row
--   with no score is an empty slot in a list, which is nothing.
CREATE TABLE IF NOT EXISTS setlist_scores (
    setlist_id INTEGER NOT NULL REFERENCES setlists(id) ON DELETE CASCADE,
    score_id INTEGER NOT NULL REFERENCES scores(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (setlist_id, score_id)
);
CREATE INDEX IF NOT EXISTS idx_setlist_scores_order ON setlist_scores(setlist_id, position);
""" + _PRACTICE_SCHEMA + _TRAINER_SCHEMA + _TRAINER_CHORD_SCHEMA

# The schema this code expects. Stamped into PRAGMA user_version once a startup
# has finished bringing a database up to date.
#
# Recorded rather than inferred, and that distinction is the whole point. The
# mechanism below works out what to do by LOOKING at the live schema, which is
# fine while every change is "add a column if it is missing" and hopeless the
# moment one is not: the first non-additive change would have to guess each
# deployed database's history from its column set. Stamping now, while every
# deployed database is unambiguously either pre-instruments or version 1, costs
# one PRAGMA; stamping later costs that guesswork.
#
# Version 2 is the first change this file's ADD COLUMN mechanism could not
# express, which is what the stamp was recorded for - see MIGRATIONS.
#
# VERSION 4 HAS NO MIGRATIONS STEP, AND THAT IS THE POINT OF IT. Every version
# so far existed because a change could not be expressed as an ADD COLUMN. This
# one exists for the OTHER half of the stamp's job, which is easy to forget:
# _check_schema_version compares in both directions, and the number is what
# makes an older release refuse to open a newer database.
#
# `missing_since` is nullable, carries no foreign key and needs no backfill, so
# as an upgrade it is only an ADD COLUMN and COLUMN_ADDITIONS carries it - that
# mechanism is right for it and it stays there. But the release BEFORE this one
# still deletes every score row it did not see and still creates a missing
# library folder. Pointing it at a database this release has written destroys
# precisely what this change protects: it does not fail on the unknown column,
# it ignores it, treats every marked row as present, finds no file, and deletes
# the lot with the practice history, tags and transcriptions behind them.
#
# And this release makes a rollback MORE likely rather than less. Its new
# failure mode is a container that refuses to start when the library folder is
# absent, and the reflex answer to "the new version will not start" is to put
# the old tag back - so the change actively steers people toward the one action
# that would destroy their data. The stamp is what stops that: the old release
# meets a version it does not understand and refuses, which is what
# docs/deployment.md has always promised it would do.
#
# THE RULE THIS ESTABLISHES, since it is the first version of its kind: bump
# when an older release running against the new database would do harm, not only
# when the new schema cannot be expressed the old way. A version therefore need
# not have a MIGRATIONS entry - SCHEMA_VERSION is stamped by init_db at the end
# of startup regardless of which mechanism did the work.
#
# VERSION 5 HAS NO MIGRATIONS STEP EITHER, and is the rule above applied a
# second time. `deleted_at` and `deleted_from` (#56) are nullable, carry no
# foreign key and need no backfill, so as an upgrade they are only ADD COLUMNs
# and COLUMN_ADDITIONS carries them. The stamp moves because of what the
# PREVIOUS release does when pointed at a database this one has written.
#
# It does not know the columns, so it does not know a deleted score is deleted.
# Two consequences, and the second is the one that decided this:
#
#   IT HANDS SOMEBODY THEIR DELETED SCORES BACK. Its scan walks the trash folder
#   like any other, finds each deleted file at the path its row already names,
#   and lists it in the library as an ordinary score. Somebody who deleted a
#   score sees it return, with no statement that anything was undone.
#
#   AND ITS SCAN CAN ADOPT A DELETED ROW FOR A LIVE FILE. The content-hash
#   relink picks candidates without filtering on `deleted_at`, which this
#   release's does - so a fresh copy of a deleted score's content, arriving
#   anywhere in the library, re-links onto the DELETED row and moves its path
#   out of the trash. Upgrade again and that row is still marked deleted, so the
#   file the person just added is hidden from their library and sitting in the
#   trash view. That is this release hiding a file somebody has on disk, on the
#   strength of a decision the older release could not see, and it is the kind
#   of harm the stamp exists to make impossible.
#
# SETLISTS (#6) DELIBERATELY DO NOT BUMP THIS, and it is worth stating why,
# because the rule above ("bump when an older release running against the new
# database would do harm") is what decides it - not "a new table is a schema
# change, so bump". The setlists and setlist_scores tables are pure additions:
# they arrive through SCHEMA's own CREATE TABLE IF NOT EXISTS on the next
# startup of any release that has them, need no migration and no backfill, and
# NOTHING the previous release does touches them. Walk the two harms v5 guarded
# against and neither has an analogue here. The older release does not scan,
# list, resurrect or hide anything on the strength of a setlist - it has no
# setlist code at all - so it cannot act on data it cannot see. The one
# cross-table path is that release's own purge (DELETE FROM scores, #56): the
# setlist_scores.score_id ON DELETE CASCADE is read from the schema STORED IN
# THE FILE, not from the running code, so an old release purging a score still
# removes that score's membership rows correctly and leaves no dangling
# reference. A setlist created on the new release therefore survives a rollback
# to the old one frozen but wholly intact, and comes back on the next upgrade.
# With no harm to guard against, bumping would only make the old release REFUSE
# a database it can open safely - stranding a rollback over data that was never
# at risk, which is the exact cost the v5 note above warns a bump carries. So
# the honest version number is unchanged. (If a later setlist change DOES make
# the old release misbehave - say a scanner that reconciles setlist membership -
# that change is what bumps this, with the forward-path test the bump then
# needs; adding these tables is not that change.)
SCHEMA_VERSION = 5

# Columns added to a table that had already shipped. CREATE TABLE IF NOT EXISTS
# does nothing at all to a table that exists, so SCHEMA alone reaches only fresh
# installs - an upgraded one would keep the old columns and 500 the moment
# anything wrote the new one.
#
# THIS EXPRESSES ADD COLUMN AND NOTHING ELSE, and is not the beginning of a
# migration framework. There is no ordering and no down direction, so it cannot
# rename, drop, change a constraint, or add an index. In particular it cannot
# BACKFILL: idempotence is this mechanism's only safety property - it runs on
# every startup - and a backfill is not idempotent, so putting one here would
# rewrite rows a user had since edited. And SQLite requires that an added
# column's default be NULL when it carries a foreign key, so a future column
# needing both a foreign key and NOT NULL cannot come through here at all.
# Anything in that list needs a real runner - that runner is MIGRATIONS below,
# and this mechanism stays as narrow as it is. A change that CAN be an ADD
# COLUMN still belongs here, because a migration step runs once and is then
# trusted forever, whereas this runs on every startup and repairs a database
# that half-upgraded.
#
# Applied by name AND, where the definition carries one, by checking the foreign
# key is really there: PRAGMA table_info reports only names, so a column that
# existed without its REFERENCES clause would be matched, skipped, and left with
# no foreign key - ON DELETE SET NULL would never fire and a dangling reference
# would be accepted. Nothing in this repo's history produces that state, but the
# check is what makes the schema's guarantee true rather than assumed.
#
# Which score an instrument is for lives on the score rather than in a join
# table: it is one instrument per score, and a score row is what every reader
# already has in hand. ON DELETE SET NULL because deleting an instrument means
# the player no longer has it, not that the scores written for it are gone.
#
# NOTHING READS THIS COLUMN YET - see issue #72. Two things a first consumer
# has to know. Transcription still opens every extraction with
# tabextract.DEFAULT_TUNING, so a drop-D or seven-string score is read as a
# standard six-string whatever instrument it names; and nothing revalidates a
# score when its instrument is edited underneath it, so a reference can outlive
# the shape it was chosen for (see api.update_instrument).
# missing_since arrives this way for an existing database and from
# _SCORES_COLUMNS for a fresh one, which is two statements of one column and so
# needs saying: _SCORES_COLUMNS IS THE DEFINITION OF RECORD, and this entry
# exists only because CREATE TABLE IF NOT EXISTS cannot reach a table that is
# already there. It is exactly the kind of change this mechanism is for -
# nullable, no foreign key, and needing no backfill, because NULL is already
# true of every row that exists: they were all written by a scanner that would
# have deleted them rather than mark them, so every one of them was present the
# last time anything looked. The two definitions cannot drift unnoticed -
# test_scanner.py compares an upgraded install's scores table against a fresh
# one's, column for column, which is the check #94 wished it had had.
COLUMN_ADDITIONS = {
    "scores": {
        "instrument_id": "INTEGER REFERENCES instruments(id) ON DELETE SET NULL",
        "missing_since": "TEXT",
        # #56's two, arriving the same way and for the same reasons: nullable,
        # no foreign key, and needing no backfill, because NULL is already true
        # of every row that exists - nothing could have been deleted through
        # Fermata before there was a way to delete anything.
        "deleted_at": "TEXT",
        "deleted_from": "TEXT",
    },
}


def _migrate_to_2_any_practice(conn) -> None:
    """Let a practice session exist without a score behind it.

    practice_sessions shipped with `score_id INTEGER NOT NULL`, and SQLite
    cannot relax a NOT NULL in place, so this is the table-rebuild recipe: a
    new table under a temporary name, every existing row carried across by id,
    the old table dropped, the new one renamed into its place. See
    _PRACTICE_SESSIONS_COLUMNS for why the column has to become nullable at
    all.

    THE ROWS THIS MOVES ARE THE ONE THING IN FERMATA THAT CANNOT BE
    REGENERATED. A score row can be rebuilt by rescanning the library and a
    transcription by re-extracting, but nothing on disk remembers that someone
    sat down and practised for forty minutes. So every existing row is carried
    across with its id intact, and the whole rebuild runs inside the caller's
    transaction: it either lands completely or not at all, and a crash halfway
    leaves the old table exactly as it was.

    Nothing in the schema references practice_sessions, so dropping it cannot
    cascade into anything else, and the rename cannot leave another table
    pointing at a name that has moved.

    Written to be safe to run against a database it has already run against,
    and against a fresh one: a missing table means SCHEMA is about to create
    it in its current shape, and an already-nullable score_id means this has
    run before. Neither is an error - init_db() re-runs a step whose stamp
    never landed.
    """
    info = conn.execute("PRAGMA table_info(practice_sessions)").fetchall()
    if not info:
        return
    score_id = next((row for row in info if row["name"] == "score_id"), None)
    if score_id is None or not score_id["notnull"]:
        return

    conn.execute("CREATE TABLE practice_sessions_rebuilt " + _PRACTICE_SESSIONS_COLUMNS)
    # Named columns on both sides, never SELECT *: the source table's column
    # order is whatever the release that created it chose, and a positional
    # copy would happily put a note into a tempo the day they differ.
    #
    # `activity` is set to 'piece' rather than left to the column default
    # because that is what these rows actually are - every session that could
    # be recorded before this version was against a score. `local_date` is
    # deliberately NOT backfilled from started_at: nobody recorded which
    # calendar day these happened on in the practiser's own time, and writing
    # a guess into the column readers treat as recorded fact is how the
    # guess stops being visible as one.
    conn.execute(
        """INSERT INTO practice_sessions_rebuilt
               (id, owner, score_id, activity, started_at, seconds, note)
           SELECT id, ?, score_id, 'piece', started_at, seconds, note
             FROM practice_sessions""",
        (DEFAULT_OWNER,),
    )
    conn.execute("DROP TABLE practice_sessions")
    conn.execute("ALTER TABLE practice_sessions_rebuilt RENAME TO practice_sessions")
    # The old table's indexes went with it. They are NOT recreated here:
    # init_db runs SCHEMA immediately after this, and SCHEMA's CREATE INDEX IF
    # NOT EXISTS statements are the one place they are defined. Restating them
    # here would be a second copy that could quietly stop matching, and the
    # index set an upgraded install ends up with is asserted against a fresh
    # one's in test_practice_migration.py either way.


def _table_exists(conn, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def _on_delete_action(conn, table: str, column: str) -> str | None:
    """What SQLite will do to this column when the row it points at is deleted.

    PRAGMA foreign_key_list is the only way to read it back - the action is not
    in table_info - and reading it is what lets a migration check its own work
    instead of assuming it. None means the column carries no foreign key at all.
    """
    for row in conn.execute(f"PRAGMA foreign_key_list({table})"):
        if row["from"] == column:
            return row["on_delete"]
    return None


def _rebuild_carrying_rows(conn, table: str, columns: str) -> None:
    """Rebuild one table from `columns`, carrying every existing row across.

    SQLite cannot alter a constraint in place, so this is the documented
    table-rebuild recipe: a new table under a temporary name, the rows copied,
    the old table dropped, the new one renamed into its place. It runs inside
    the caller's transaction, so it either lands completely or not at all.

    The columns carried are the ones the two tables have IN COMMON, worked out
    by name from PRAGMA table_info on both - never a positional copy, and never
    SELECT *. A column the old table did not have takes its default; a column
    it had and the new definition does not is dropped, which is the only way a
    rebuild can remove one. Discovering the list rather than restating it is
    what stops this from silently dropping a column that was added between the
    step being written and the step being run.

    Indexes are NOT recreated here. init_db runs SCHEMA immediately after the
    migrations, and SCHEMA's CREATE INDEX IF NOT EXISTS statements are the one
    place they are defined; the index set an upgraded install ends up with is
    asserted against a fresh one's in test_practice_migration.py.

    CALLERS MUST HAVE FOREIGN KEYS DISABLED. _run_migrations does that, and it
    is not optional: with them on, a single practice row whose score_id no
    longer matches a score - which the sqlite3 command line, whose default is
    foreign_keys OFF, will happily leave behind - is rejected on the copy, and
    the application then fails to start on every boot with no way out but hand
    SQL or deleting the rows this migration exists to protect. SQLite's own
    table-rebuild recipe opens by saying to turn them off for this reason.
    _repair_dangling_practice_references is what then puts such a row right
    instead of refusing to carry it.
    """
    temp = f"{table}_rebuilt"
    conn.execute(f"CREATE TABLE {temp} {columns}")
    old = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    carried = [
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({temp})")
        if row["name"] in old
    ]
    names = ", ".join(carried)
    conn.execute(f"INSERT INTO {temp} ({names}) SELECT {names} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {temp} RENAME TO {table}")


def _repair_dangling_practice_references(conn) -> int:
    """Point a practice row at nothing rather than at a score that is not there.

    A row whose score_id names a missing score is not something this
    application can produce - the reference has always been enforced - but it
    is something the sqlite3 command line produces trivially, because its
    default is foreign_keys OFF. Before this schema such a row was inert. Now
    that a rebuild has to copy it, it has to be dealt with, and there are only
    three options: refuse to start, drop the row, or put it right.

    Putting it right means NULL, which is exactly what the reference's own ON
    DELETE SET NULL would have done had the deletion gone through the database
    in the first place. The practice is kept, the database is left consistent,
    and the change is announced rather than made quietly - it is somebody's
    history, and a row that stops naming a piece is a visible difference.
    """
    repaired = 0
    for table in ("practice_sessions", "practice_goals"):
        if not _table_exists(conn, table):
            # A fresh install, where SCHEMA has not run yet. Nothing to repair,
            # and nothing to raise about either.
            continue
        cur = conn.execute(
            f"""UPDATE {table} SET score_id = NULL
                 WHERE score_id IS NOT NULL
                   AND score_id NOT IN (SELECT id FROM scores)"""
        )
        if cur.rowcount > 0:
            repaired += cur.rowcount
            log.warning(
                "%s row(s) in %s referred to a score that is no longer in the database, "
                "which normal use cannot produce - most likely the file was edited by "
                "hand with foreign keys off. The practice itself is kept; those rows now "
                "record practice with no piece named, which is what deleting the score "
                "through Fermata would have done.",
                cur.rowcount,
                table,
            )
    return repaired


def _migrate_to_3_keep_practice_when_a_score_goes(conn) -> None:
    """Stop deleting a score from deleting the evidence somebody practised it.

    Both practice tables referenced scores(id) ON DELETE CASCADE. Removing one
    score therefore removed every session against it - hours of somebody's own
    record of their own work, and the one thing in this application that cannot
    be rebuilt from the files on disk. This makes both references ON DELETE SET
    NULL. See _PRACTICE_SESSIONS_COLUMNS and _PRACTICE_GOALS_COLUMNS for why
    that is the right trade and what an orphaned row then means.

    WHY THIS IS A SEPARATE STEP FROM 2, when 2 has never shipped. Migration 2
    builds practice_sessions from the shared column definition, which now says
    SET NULL - so a database arriving from version 0 or 1 gets the right table
    from that step alone and this one finds nothing to do. What this step is
    for is a database already stamped 2: the branch that introduced both ran in
    other people's working copies before the cascade was reversed, and a
    database stamped 2 can never be re-offered step 2. Folding the fix into
    step 2 silently would leave those databases cascading for ever.

    That is also the general rule for a step here: a step brings a database to
    the CURRENT definition, and its guard checks the one property it owns. So
    an earlier step may well already satisfy a later one, and every step is
    safe to reach in any order that respects the stamps.
    """
    for table, columns in (
        ("practice_sessions", _PRACTICE_SESSIONS_COLUMNS),
        ("practice_goals", _PRACTICE_GOALS_COLUMNS),
    ):
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not info:
            continue  # SCHEMA is about to create it in its current shape
        if _on_delete_action(conn, table, "score_id") == "SET NULL":
            continue  # already carries the reference this step is here to fix
        _rebuild_carrying_rows(conn, table, columns)


# The real migration runner the ADD COLUMN mechanism above deliberately is not:
# ordered steps, each taking a database from the version below its key to that
# key, run once and then stamped.
#
# ORDERING, WHICH A STEP AUTHOR HAS TO KNOW. init_db runs these BEFORE the
# SCHEMA script and before _add_missing_columns, so a step sees the database
# exactly as the previous version left it: no table SCHEMA would have created,
# and NO COLUMN COLUMN_ADDITIONS WOULD HAVE ADDED. On a version 0 database,
# step 2 therefore runs against a `scores` table with no instrument_id at all -
# which is fine because it does not touch it, and would not be fine for a step
# that did. A step needing a column from COLUMN_ADDITIONS has to add it itself,
# or be written not to need it. This is exactly the trap the version stamp
# exists to prevent: such a step works on the author's already-upgraded
# database and fails on a genuinely old one.
#
# A step may do anything SQLite can do - rebuild a table, backfill, drop a
# column - because unlike COLUMN_ADDITIONS it is NOT re-run on every startup,
# and so does not have to be idempotent to be safe. It has to be safe to
# re-run only in the one case init_db() can produce: a process that died after
# a step's work committed but before its stamp did. Each step above therefore
# begins by checking whether its work is already there and returning if so,
# which is cheap and removes the only window in which "run once" is a
# statement about hope rather than about the code.
#
# There is no down direction and there will not be one. A downgrade is already
# refused outright by _check_schema_version, because a newer release's schema
# may hold columns this code knows nothing about and writing to it blind is
# how a downgrade loses data. Restoring a backup is the way back; see the
# Backups section of docs/deployment.md.
MIGRATIONS = {
    2: _migrate_to_2_any_practice,
    3: _migrate_to_3_keep_practice_when_a_score_goes,
}


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            # WAL is a write to the database header, so this is the first thing
            # that touches a read-only file - and "attempt to write a readonly
            # database" from a pragma is not a diagnosis anybody can act on.
            # Read-only is a real deployment mistake: a config volume mounted
            # :ro, or a file owned by another user after a container's user id
            # changed.
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                f"Fermata cannot start: its database at {DB_PATH} cannot be written to "
                f"({exc}).\n"
                "\n"
                "Fermata needs write access to the config folder - it keeps your library "
                "index, your practice history and your settings there. Check that the "
                "folder is not mounted read-only, and that the user Fermata runs as owns "
                "it. In Docker that is the volume mapped to /config; see "
                "docs/deployment.md.\n"
                "\n"
                "Your sheet music is not affected: Fermata has not started, so nothing has "
                "touched your library folder."
            ) from None
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def _add_missing_columns(conn) -> None:
    for table, columns in COLUMN_ADDITIONS.items():
        present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        keyed = {row["from"] for row in conn.execute(f"PRAGMA foreign_key_list({table})")}
        for column, definition in columns.items():
            if column not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            elif "REFERENCES" in definition.upper() and column not in keyed:
                # Present by name but carrying no foreign key, so ON DELETE SET
                # NULL can never fire and a dangling id would be accepted.
                # SQLite cannot add a constraint to an existing column, so this
                # is not repairable here - and continuing would mean running
                # with an integrity guarantee the rest of the code assumes and
                # the database does not provide.
                raise RuntimeError(
                    f"Fermata cannot start: the '{column}' column of the '{table}' table is "
                    "missing the link that keeps it pointing at real rows.\n"
                    "\n"
                    "This cannot happen through normal use or through any upgrade - if you "
                    "have not edited the database by hand, it is a bug in Fermata and we "
                    "would like to hear about it. Please open an issue with the lines above "
                    "and the version you upgraded from.\n"
                    "\n"
                    "Your sheet music is not affected either way; only the database in the "
                    "config folder is. If you have a backup of that folder, restoring it is "
                    "the safe way back - see the Backups section of docs/deployment.md.\n"
                    "\n"
                    f"(If you are comfortable with SQLite: rebuilding '{table}' with the "
                    "definition in db.SCHEMA, carrying the existing rows across, repairs this "
                    f"with nothing lost. Dropping the '{column}' column also lets Fermata "
                    "start, but it PERMANENTLY DISCARDS which instrument each score was for, "
                    "for every score - so take a copy of the config folder first.)"
                )


def _check_schema_version(conn) -> None:
    stored = conn.execute("PRAGMA user_version").fetchone()[0]
    if stored > SCHEMA_VERSION:
        # Written by a newer Fermata. Its schema may hold columns and
        # constraints this code knows nothing about, and writing to it blind is
        # how a downgrade silently loses data.
        raise RuntimeError(
            f"this database is at schema version {stored}, but this version of Fermata "
            f"understands {SCHEMA_VERSION}. It was written by a newer release - upgrade, "
            "or restore a backup taken before it."
        )


def _stored_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _run_migrations(conn) -> None:
    """Apply every MIGRATIONS step this database has not been stamped for.

    All pending steps share one transaction, and each stamps its own version
    as it completes - so an interrupted upgrade resumes from the last step
    that actually landed rather than from the beginning. PRAGMA user_version
    is part of the database header and is rolled back with the transaction,
    which is what makes "the work and its stamp cannot disagree" true.

    The version is read once outside the lock so an already-current database
    never takes the write lock at all; startup is otherwise serialised behind
    every other instance's startup for nothing.
    """
    if not any(version > _stored_version(conn) for version in MIGRATIONS):
        return
    # Off for the duration, and restored afterwards whatever happens. A step
    # may rebuild a table, and a rebuild copies rows - so an existing row whose
    # reference is already dangling would be REJECTED with them on, taking
    # startup down permanently rather than being carried across and put right.
    # The pragma is a no-op inside a transaction, so it has to be set here,
    # outside the BEGIN. Consistency is not taken on trust in exchange: it is
    # checked below, before the commit.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Re-read inside the lock: another instance starting at the same
            # moment may have applied these already, and applying a rebuild
            # twice would not be a no-op if the step's own guard were the only
            # thing stopping it. Re-checked for a newer writer too, for the
            # same reason init_db checks twice.
            _check_schema_version(conn)
            stored = _stored_version(conn)
            for version in sorted(MIGRATIONS):
                if version <= stored:
                    continue
                MIGRATIONS[version](conn)
                conn.execute(f"PRAGMA user_version = {version}")
            _repair_dangling_practice_references(conn)
            _report_remaining_violations(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _report_remaining_violations(conn) -> None:
    """Say so if the database is still inconsistent after a migration.

    The rebuilds above run with foreign keys off, so nothing is checking them
    while they work. This is the check - not to refuse the upgrade, which would
    strand somebody on an unstartable application over rows they cannot see,
    but so that a real inconsistency is stated once, plainly, in the log rather
    than surfacing later as a query that quietly returns the wrong thing.
    """
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if not violations:
        return
    tables = sorted({row[0] for row in violations})
    log.warning(
        "after the schema upgrade, %s reference(s) in %s still point at rows that are "
        "not there. Fermata will start and your practice history is intact, but this is "
        "not a state normal use produces - please report it, with the version you "
        "upgraded from.",
        len(violations),
        ", ".join(tables),
    )


def init_db() -> None:
    conn = connect()
    # Before anything writes, not after. executescript() commits as it goes, so
    # checking afterwards means a database written by a newer release has
    # already been altered by this one - and the check exists precisely because
    # writing blind is how a downgrade loses data. SCHEMA is written blind.
    _check_schema_version(conn)
    # Before SCHEMA, not after, and that order is load-bearing. SCHEMA creates
    # indexes over practice_sessions columns that only exist once migration 2
    # has rebuilt that table, and CREATE INDEX IF NOT EXISTS skips only when
    # the INDEX exists - against the pre-migration table it would not skip, it
    # would fail on an unknown column and take startup down with it.
    _run_migrations(conn)
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError as exc:
        # Reachable only if the recorded version and the actual tables disagree
        # - a database stamped as upgraded whose tables were not, which is what
        # a hand-edited PRAGMA user_version or a restored mismatched pair of
        # files produces. The migrations above are skipped on the strength of
        # the stamp, and SCHEMA then meets a table it does not fit. Without
        # this, that is "no such column: local_date" on every boot.
        raise RuntimeError(
            f"Fermata cannot start: its database says it is at schema version "
            f"{_stored_version(conn)}, but its tables are not the ones that version has "
            f"({exc}).\n"
            "\n"
            "This cannot happen through normal use or through any upgrade. It means the "
            "recorded version and the actual tables have come apart - by a hand-edited "
            "PRAGMA user_version, or by restoring a database file over a different one.\n"
            "\n"
            "Restoring your config folder from a backup is the reliable way back - see "
            "the Backups section of docs/deployment.md. Your sheet music is not affected; "
            "only the database in the config folder is.\n"
            "\n"
            "If you have no backup, we would like to hear about it either way: please open "
            "an issue with the lines above and the version you upgraded from."
        ) from None
    conn.commit()
    # BEGIN IMMEDIATE takes the write lock before the read below rather than on
    # the first write after it. Two processes starting at once - a second
    # `docker run` against the same config volume is a one-liner - could
    # otherwise both read a schema without the column and both try to add it,
    # and the loser would abort startup on an unexplained "duplicate column
    # name". Serialised, the second one reads a schema that already has it and
    # does nothing. The connection's 30s timeout is what it waits with.
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Checked again inside the lock: a newer release could have upgraded and
        # re-stamped the database between the read above and this point.
        _check_schema_version(conn)
        _add_missing_columns(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@contextmanager
def write_tx():
    """A transaction holding the write lock from the start.

    tx() relies on the driver opening a transaction at the first DML statement,
    so any SELECT made before that - an existence check, a count - reads
    OUTSIDE the transaction and can be invalidated before the write lands. A
    concurrent delete arriving between "does this row exist" and the update that
    assumes it does turns an intended 404 into an unhandled IntegrityError and a
    500, and makes a count reported alongside the write untrustworthy.

    Use this for any mutation that reads before it writes. init_db() does the
    same thing for the same reason.
    """
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
