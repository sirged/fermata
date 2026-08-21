"""Upgrading a database that predates the deepened practice model.

WHY THIS FILE IS LONGER THAN THE MIGRATION IT TESTS. practice_sessions rows are
the only thing in Fermata that cannot be regenerated. Rescanning rebuilds every
score row and re-extraction rebuilds every transcription, but nothing on disk
remembers that somebody sat down and practised for forty minutes on a Tuesday.
The migration rebuilds that table - creates a new one, copies the rows across,
drops the old - so "the copy is complete and correct" is not a detail of this
change, it is the change.

So the schema below is the real one, as it shipped, written out rather than
imagined: an upgrade test that builds its "old" database out of the CURRENT
schema tests nothing at all, because the shape it would be migrating from is
the shape it is migrating to.

Both predecessors are covered. Version 0 is a database from before schema
versions were stamped at all (no instruments table, no scores.instrument_id);
version 1 is one from after. Both have to arrive at version 2 with every
practice row intact.
"""

import sqlite3

import pytest

from fermata import api, db, practice

# ---------------------------------------------------------------------------
# The schema as it shipped, before this change. Only what the migration and
# the assertions below actually touch - scores, because practice_sessions
# references it, and practice_sessions itself, whose score_id was NOT NULL.
# ---------------------------------------------------------------------------

_V0_SCHEMA = """
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
CREATE TABLE IF NOT EXISTS practice_sessions (
    id INTEGER PRIMARY KEY,
    score_id INTEGER NOT NULL REFERENCES scores(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    seconds INTEGER NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_practice_score ON practice_sessions(score_id);
CREATE TABLE IF NOT EXISTS settings (
    owner TEXT NOT NULL DEFAULT 'local',
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (owner, key)
);
"""

# Version 1 added instruments and scores.instrument_id.
_V1_ADDITIONS = """
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
ALTER TABLE scores ADD COLUMN instrument_id INTEGER REFERENCES instruments(id) ON DELETE SET NULL;
"""

# Real practice history: several pieces, several days, notes somebody wrote.
# Ids are not contiguous and not in date order, because a real table's are not
# either - a copy that renumbered rows or reordered them would pass against a
# tidy fixture and lose the link between a session and whatever else ever
# refers to it.
_SCORES = [
    (1, "To Zanarkand", "Patreon/To Zanarkand.pdf"),
    (2, "Study in C", "Classical/Tarrega/Study in C.pdf"),
    (7, "Clair de Lune", "Favorites/ClairDeLune.pdf"),
]

_SESSIONS = [
    (3, 1, "2026-08-01 19:30:00", 1800, "bars 1-16 still muddy"),
    (4, 1, "2026-08-02 20:05:00", 2400, None),
    (9, 2, "2026-08-02 08:15:00", 600, "warm-up only"),
    (11, 7, "2026-08-05 21:40:00", 3600, "whole thing, twice"),
    (12, 1, "2026-08-06 18:00:00", 900, "left hand alone"),
]


def _legacy_database(path, version: int) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_V0_SCHEMA)
    if version >= 1:
        conn.executescript(_V1_ADDITIONS)
    for score_id, title, rel in _SCORES:
        conn.execute(
            """INSERT INTO scores(id, title, path, file_type, hash, size, mtime)
               VALUES (?, ?, ?, 'pdf', 'deadbeef', 1, 0.0)""",
            (score_id, title, rel),
        )
    for session_id, score_id, started_at, seconds, note in _SESSIONS:
        conn.execute(
            """INSERT INTO practice_sessions(id, score_id, started_at, seconds, note)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, score_id, started_at, seconds, note),
        )
    conn.execute("INSERT INTO settings(owner, key, value) VALUES ('local', 'staff_theme', 'noir')")
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()


@pytest.fixture
def upgraded(tmp_path, monkeypatch):
    """A legacy database of a given version, brought up to date by init_db."""

    def _upgrade(version: int = 1):
        path = tmp_path / f"legacy_v{version}.db"
        _legacy_database(path, version)
        monkeypatch.setattr(db, "DB_PATH", path)
        db._local.conn = None
        db.init_db()
        return path

    yield _upgrade
    db._local.conn = None


def _fresh_schema(tmp_path, monkeypatch, name="fresh.db"):
    """A database created from scratch by the current code."""
    path = tmp_path / name
    monkeypatch.setattr(db, "DB_PATH", path)
    db._local.conn = None
    db.init_db()
    conn = db.connect()
    shape = _table_shape(conn)
    db._local.conn = None
    return shape


def _table_shape(conn) -> dict:
    columns = [
        (r["name"], r["type"].upper(), r["notnull"], r["dflt_value"], r["pk"])
        for r in conn.execute("PRAGMA table_info(practice_sessions)")
    ]
    indexes = {}
    for row in conn.execute("PRAGMA index_list(practice_sessions)"):
        indexes[row["name"]] = [
            r["name"] for r in conn.execute(f"PRAGMA index_info({row['name']})")
        ]
    keys = [
        (r["table"], r["from"], r["to"], r["on_delete"])
        for r in conn.execute("PRAGMA foreign_key_list(practice_sessions)")
    ]
    return {"columns": columns, "indexes": indexes, "foreign_keys": keys}


# ---------------------------------------------------------------------------
# The history survives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", [0, 1])
def test_every_practice_session_survives_the_upgrade(upgraded, version):
    """Row for row, id for id, value for value.

    Compared as a whole set rather than by counting: a count survives a copy
    that carried five rows across and put the notes in the wrong ones, which is
    exactly what a positional INSERT ... SELECT * does the day the column
    orders differ.
    """
    upgraded(version)
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, score_id, started_at, seconds, note FROM practice_sessions ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in rows] == sorted(_SESSIONS)


@pytest.mark.parametrize("version", [0, 1])
def test_the_upgrade_stamps_the_new_version(upgraded, version):
    upgraded(version)
    conn = db.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 2


def test_carried_rows_are_marked_as_the_piece_practice_they_were(upgraded):
    """Every session that could be recorded before this change was against a
    score, so 'piece' is a statement of fact about these rows rather than a
    convenient default."""
    upgraded()
    conn = db.connect()
    rows = conn.execute("SELECT activity, owner FROM practice_sessions").fetchall()
    assert {(r["activity"], r["owner"]) for r in rows} == {("piece", db.DEFAULT_OWNER)}


def test_a_carried_row_has_no_recorded_practice_day_and_says_so(upgraded):
    """Nobody recorded which calendar day these happened on in the practiser's
    own time. The column stays NULL rather than being backfilled from the UTC
    timestamp, and a reader is told the day it got was inferred."""
    upgraded()
    conn = db.connect()
    stored = conn.execute(
        "SELECT local_date FROM practice_sessions WHERE id = 3"
    ).fetchone()["local_date"]
    assert stored is None

    presented = practice.session_dict(
        conn.execute("SELECT * FROM practice_sessions WHERE id = 3").fetchone()
    )
    assert presented["local_date"] == "2026-08-01"
    assert presented["local_date_source"] == "utc_date"


def test_carried_history_still_counts_towards_a_goal(upgraded):
    """The point of keeping these rows is that they are somebody's record of
    their own work - so they have to be visible to what reads practice, not
    merely present in the table."""
    upgraded()
    conn = db.connect()
    facts = practice.period_facts(conn, "2026-08-01", "2026-08-07")
    assert facts["days_practised"] == 4  # 1st, 2nd, 5th, 6th
    assert facts["seconds"] == sum(s[3] for s in _SESSIONS)
    assert facts["sessions"] == len(_SESSIONS)


def test_nothing_else_in_the_database_is_disturbed(upgraded):
    """The migration touches one table. A rebuild that dropped the wrong thing,
    or a stray cascade, would show up here first."""
    upgraded()
    conn = db.connect()
    titles = [r["title"] for r in conn.execute("SELECT title FROM scores ORDER BY id")]
    assert titles == [s[1] for s in _SCORES]
    assert api.get_settings()["staff_theme"] == "noir"


# ---------------------------------------------------------------------------
# The rebuilt table is the table the current code would have created
# ---------------------------------------------------------------------------


def test_an_upgraded_table_is_identical_to_a_freshly_created_one(tmp_path, monkeypatch):
    """The migration builds practice_sessions from the same column definition
    SCHEMA does, and this is what keeps that true. If the two ever diverge -
    a column added to one and not the other, an index left off - an upgraded
    install would run on a subtly different table from a fresh one, and every
    other test here would pass on both."""
    fresh = _fresh_schema(tmp_path, monkeypatch)

    legacy = tmp_path / "legacy.db"
    _legacy_database(legacy, 1)
    monkeypatch.setattr(db, "DB_PATH", legacy)
    db._local.conn = None
    db.init_db()
    migrated = _table_shape(db.connect())
    db._local.conn = None

    assert migrated == fresh


def test_the_rebuilt_column_still_carries_its_foreign_key(upgraded):
    """A rebuild is where a REFERENCES clause gets quietly dropped, and a
    score_id pointing at nothing would then be accepted for ever."""
    upgraded()
    conn = db.connect()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO practice_sessions(score_id, activity, started_at, seconds)
               VALUES (99999, 'piece', datetime('now'), 60)"""
        )
    conn.rollback()


def test_deleting_a_score_still_takes_its_sessions_with_it(upgraded):
    """ON DELETE CASCADE, unchanged. The scanner relies on it: it re-links a
    renamed file to its existing score row by content hash precisely so that
    a rename never reaches this cascade, and a row that genuinely goes means
    the file is gone from the library."""
    upgraded()
    conn = db.connect()
    conn.execute("DELETE FROM scores WHERE id = 1")
    conn.commit()
    remaining = {r["id"] for r in conn.execute("SELECT id FROM practice_sessions")}
    assert remaining == {9, 11}


def test_a_session_with_no_score_is_now_storable(upgraded):
    """The whole reason this needed a migration rather than an ADD COLUMN.
    Practice that is not against a piece - a trainer, unstructured playing -
    had nowhere to go while score_id was NOT NULL."""
    upgraded()
    conn = db.connect()
    conn.execute(
        """INSERT INTO practice_sessions(score_id, activity, started_at, seconds)
           VALUES (NULL, 'ear_training', datetime('now'), 600)"""
    )
    conn.commit()
    row = conn.execute(
        "SELECT score_id, activity FROM practice_sessions WHERE activity = 'ear_training'"
    ).fetchone()
    assert row["score_id"] is None


# ---------------------------------------------------------------------------
# Running it again, and resuming it
# ---------------------------------------------------------------------------


def test_starting_up_again_changes_nothing(upgraded):
    """init_db runs on every start. A migration that is not stamped, or whose
    guard does not hold, would rebuild the table a second time - and the second
    rebuild is the one that copies from an already-copied table."""
    upgraded()
    db.init_db()
    db.init_db()
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, score_id, started_at, seconds, note FROM practice_sessions ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in rows] == sorted(_SESSIONS)


def test_an_upgrade_interrupted_before_its_stamp_landed_resumes_safely(upgraded):
    """The one case a migration step has to survive being re-run: the process
    died after the work committed and before the stamp did. The step's own
    guard has to see its work already there and do nothing.

    A session with the NEW columns filled in is what makes this bite. A second
    rebuild would copy across only the columns the old table had - id, score,
    timestamp, length, note - and silently drop the practice day, the rating
    and the tempo out of every row it touched. With only carried-over rows in
    the table there is nothing in those columns to lose, and a missing guard
    looks exactly like a working one.
    """
    upgraded()
    conn = db.connect()
    conn.execute(
        """INSERT INTO practice_sessions
               (owner, score_id, activity, mode, started_at, local_date, seconds,
                from_bar, to_bar, tempo_bpm, target_tempo_bpm, rating, note)
           VALUES (?, 1, 'piece', 'section', '2026-08-20 19:00:00', '2026-08-20', 1500,
                   17, 32, 76, 120, 4, 'logged after the upgrade')""",
        (db.DEFAULT_OWNER,),
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()

    db.init_db()

    rows = conn.execute(
        "SELECT id, score_id, started_at, seconds, note FROM practice_sessions ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in rows][: len(_SESSIONS)] == sorted(_SESSIONS)
    detailed = conn.execute(
        """SELECT mode, local_date, from_bar, to_bar, tempo_bpm, target_tempo_bpm, rating
             FROM practice_sessions WHERE note = 'logged after the upgrade'"""
    ).fetchone()
    assert tuple(detailed) == ("section", "2026-08-20", 17, 32, 76, 120, 4)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_a_failing_step_leaves_the_old_table_untouched(tmp_path, monkeypatch):
    """The rebuild runs inside one transaction, so an upgrade that dies partway
    has to leave the history exactly as it was rather than half-copied. Nothing
    in the real step fails, so the failure is injected."""
    legacy = tmp_path / "doomed.db"
    _legacy_database(legacy, 1)
    monkeypatch.setattr(db, "DB_PATH", legacy)
    db._local.conn = None

    def explode(conn):
        db._migrate_to_2_any_practice(conn)
        raise RuntimeError("simulated crash partway through the upgrade")

    monkeypatch.setitem(db.MIGRATIONS, 2, explode)
    with pytest.raises(RuntimeError):
        db.init_db()

    conn = db.connect()
    rows = conn.execute(
        "SELECT id, score_id, started_at, seconds, note FROM practice_sessions ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in rows] == sorted(_SESSIONS)
    # Still on the old version, and still the old table - so the next startup
    # tries the whole step again rather than trusting a half-applied one.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    notnull = {
        r["name"]: r["notnull"] for r in conn.execute("PRAGMA table_info(practice_sessions)")
    }
    assert notnull["score_id"] == 1
    db._local.conn = None


def test_a_fresh_install_needs_no_migration_and_still_lands_on_the_new_table(
    tmp_path, monkeypatch
):
    """The step has to be a no-op against a database that never had the old
    table - SCHEMA creates it in its current shape, and a step that ran anyway
    would be operating on a table it did not build."""
    path = tmp_path / "brand_new.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db._local.conn = None
    db.init_db()
    conn = db.connect()
    notnull = {
        r["name"]: r["notnull"] for r in conn.execute("PRAGMA table_info(practice_sessions)")
    }
    assert notnull["score_id"] == 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM practice_sessions").fetchone()[0] == 0
    db._local.conn = None
