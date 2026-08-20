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

CREATE TABLE IF NOT EXISTS practice_sessions (
    id INTEGER PRIMARY KEY,
    score_id INTEGER NOT NULL REFERENCES scores(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    seconds INTEGER NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_practice_score ON practice_sessions(score_id);

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
"""

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
SCHEMA_VERSION = 1

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
# Anything in that list needs a real runner, and SCHEMA_VERSION is what will let
# one be written.
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
                    f"{table}.{column} exists without its foreign key to "
                    f"{definition.split('REFERENCES')[1].strip()}. SQLite cannot add the "
                    f"constraint to an existing column: rebuild {table} with the definition "
                    "in db.SCHEMA plus COLUMN_ADDITIONS, or drop the column and restart."
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


def init_db() -> None:
    conn = connect()
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
