import sqlite3
import threading
from contextlib import contextmanager

from .config import DB_PATH

_local = threading.local()

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
_PRACTICE_SESSIONS_COLUMNS = """(
    id INTEGER PRIMARY KEY,
    owner TEXT NOT NULL DEFAULT 'local',
    score_id INTEGER REFERENCES scores(id) ON DELETE CASCADE,
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
_PRACTICE_SESSIONS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_practice_score ON practice_sessions(score_id)",
    "CREATE INDEX IF NOT EXISTS idx_practice_day ON practice_sessions(owner, local_date)",
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
_PRACTICE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS practice_sessions " + _PRACTICE_SESSIONS_COLUMNS + ";\n"
    + "".join(f"{statement};\n" for statement in _PRACTICE_SESSIONS_INDEXES)
    + """
CREATE TABLE IF NOT EXISTS practice_goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL DEFAULT 'local',
    period TEXT NOT NULL DEFAULT 'week',
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    target_days INTEGER,
    target_minutes INTEGER,
    scope TEXT NOT NULL DEFAULT 'all',
    score_id INTEGER REFERENCES scores(id) ON DELETE CASCADE,
    activity TEXT,
    intent TEXT,
    reflection TEXT,
    realistic TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_practice_goals_period
    ON practice_goals(owner, period_start);
"""
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
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
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scores_collection ON scores(collection);
CREATE INDEX IF NOT EXISTS idx_scores_title ON scores(title);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS score_tags (
    score_id INTEGER NOT NULL REFERENCES scores(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (score_id, tag_id)
);

-- One extracted row and (at most) one edited row per score, distinguished by
-- `source`. Kept as separate rows rather than one row with fields that get
-- overwritten in place, so a re-extraction can freely replace the extracted
-- row without ever touching an edited one - see api.py's transcribe().
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
""" + _PRACTICE_SCHEMA

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
SCHEMA_VERSION = 2

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
COLUMN_ADDITIONS = {
    "scores": {
        "instrument_id": "INTEGER REFERENCES instruments(id) ON DELETE SET NULL",
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


# The real migration runner the ADD COLUMN mechanism above deliberately is not:
# ordered steps, each taking a database from the version below its key to that
# key, run once and then stamped.
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
}


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
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
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Re-read inside the lock: another instance starting at the same moment
        # may have applied these already, and applying a rebuild twice would
        # not be a no-op if the step's own guard were the only thing stopping
        # it. Re-checked for a newer writer too, for the same reason init_db
        # checks twice.
        _check_schema_version(conn)
        stored = _stored_version(conn)
        for version in sorted(MIGRATIONS):
            if version <= stored:
                continue
            MIGRATIONS[version](conn)
            conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


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
    conn.executescript(SCHEMA)
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
