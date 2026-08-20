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
"""


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
