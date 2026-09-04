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

Every predecessor is covered. Version 0 is a database from before schema
versions were stamped at all (no instruments table, no scores.instrument_id);
version 1 is one from after; version 2 is one that ran the intermediate state
of the branch that introduced these tables, and so already has the deepened
session table but still cascades a score deletion into it. All three have to
arrive at the current version with every practice row intact.
"""

import sqlite3
from datetime import date

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

# Version 2's practice tables, as the intermediate state of this branch created
# them: score_id already nullable and every deepened column present, but still
# ON DELETE CASCADE - so deleting one score erased every session against it.
# Written out rather than derived from db.py for the same reason as above: a
# fixture built from the current definition is not the shape being migrated
# from.
_V2_PRACTICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS practice_sessions (
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
);
CREATE INDEX IF NOT EXISTS idx_practice_score ON practice_sessions(score_id);
CREATE INDEX IF NOT EXISTS idx_practice_day ON practice_sessions(owner, local_date);
CREATE INDEX IF NOT EXISTS idx_practice_started ON practice_sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_practice_activity ON practice_sessions(owner, activity);
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

# Real practice history: several pieces, several days, notes somebody wrote.
# Ids are not contiguous and not in date order, because a real table's are not
# either - a copy that renumbered rows or reordered them would pass against a
# tidy fixture and lose the link between a session and whatever else ever
# refers to it.
_SCORES = [
    (1, "Second Score", "Patreon/SecondScore.pdf"),
    (2, "Study in C", "Classical/Study in C.pdf"),
    (7, "Clair de Lune", "Favorites/ClairDeLune.pdf"),
]

_SESSIONS = [
    (3, 1, "2026-08-01 19:30:00", 1800, "bars 1-16 still muddy"),
    (4, 1, "2026-08-02 20:05:00", 2400, None),
    (9, 2, "2026-08-02 08:15:00", 600, "warm-up only"),
    (11, 7, "2026-08-05 21:40:00", 3600, "whole thing, twice"),
    (12, 1, "2026-08-06 18:00:00", 900, "left hand alone"),
]


# A version 2 database also has goals in it, and a session carrying the
# deepened columns - which is what makes the version 3 rebuild's copy worth
# checking rather than assuming.
_V2_DETAIL = (
    20,
    2,
    "section",
    "2026-08-07 19:00:00",
    "2026-08-07",
    1500,
    17,
    32,
    2,
    3,
    76,
    120,
    4,
    "middle section, hands separately",
)

_V2_GOALS = [
    # (id, period_start, period_end, target_days, target_minutes, scope, score_id,
    #  activity, intent, reflection, realistic)
    (1, "2026-08-03", "2026-08-09", 4, 120, "all", None, None, "steady week", None, None),
    (2, "2026-07-27", "2026-08-02", 3, None, "score", 2, None, "the awkward bars",
     "away for three days", "no"),
]


def _legacy_database(path, version: int) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_V0_SCHEMA)
    if version >= 1:
        conn.executescript(_V1_ADDITIONS)
    if version >= 2:
        # The v0 script created the OLD practice_sessions, so it has to go
        # before the v2 shape can take its place - this fixture is standing in
        # for a database that had already been through migration 2.
        conn.execute("DROP TABLE practice_sessions")
        conn.executescript(_V2_PRACTICE_SCHEMA)
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
    if version >= 2:
        conn.execute(
            """INSERT INTO practice_sessions
                   (id, score_id, mode, started_at, local_date, seconds, from_bar, to_bar,
                    from_page, to_page, tempo_bpm, target_tempo_bpm, rating, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            _V2_DETAIL,
        )
        for goal in _V2_GOALS:
            conn.execute(
                """INSERT INTO practice_goals
                       (id, period_start, period_end, target_days, target_minutes, scope,
                        score_id, activity, intent, reflection, realistic)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                goal,
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


PRACTICE_TABLES = ("practice_sessions", "practice_goals")


def _fresh_schema(tmp_path, monkeypatch, name="fresh.db"):
    """Every practice table as created from scratch by the current code."""
    path = tmp_path / name
    monkeypatch.setattr(db, "DB_PATH", path)
    db._local.conn = None
    db.init_db()
    conn = db.connect()
    shape = {table: _table_shape(conn, table) for table in PRACTICE_TABLES}
    db._local.conn = None
    return shape


def _table_shape(conn, table="practice_sessions") -> dict:
    columns = [
        (r["name"], r["type"].upper(), r["notnull"], r["dflt_value"], r["pk"])
        for r in conn.execute(f"PRAGMA table_info({table})")
    ]
    indexes = {}
    for row in conn.execute(f"PRAGMA index_list({table})"):
        indexes[row["name"]] = [
            r["name"] for r in conn.execute(f"PRAGMA index_info({row['name']})")
        ]
    keys = [
        (r["table"], r["from"], r["to"], r["on_delete"])
        for r in conn.execute(f"PRAGMA foreign_key_list({table})")
    ]
    return {"columns": columns, "indexes": indexes, "foreign_keys": keys}


# ---------------------------------------------------------------------------
# The history survives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", [0, 1, 2])
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
        """SELECT id, score_id, started_at, seconds, note FROM practice_sessions
            WHERE id IN (SELECT id FROM practice_sessions ORDER BY id LIMIT ?)
         ORDER BY id""",
        (len(_SESSIONS),),
    ).fetchall()
    assert [tuple(r) for r in rows] == sorted(_SESSIONS)


@pytest.mark.parametrize("version", [0, 1, 2])
def test_the_upgrade_stamps_the_new_version(upgraded, version):
    upgraded(version)
    conn = db.connect()
    # 5 since issue #56 - see db.SCHEMA_VERSION. The literal is half the
    # point of this assertion: an upgrade landing on the version this code
    # believes in is only interesting if that version is written down here.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 5


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

    for version in (0, 1, 2):
        legacy = tmp_path / f"legacy_shape_v{version}.db"
        _legacy_database(legacy, version)
        monkeypatch.setattr(db, "DB_PATH", legacy)
        db._local.conn = None
        db.init_db()
        conn = db.connect()
        migrated = {table: _table_shape(conn, table) for table in PRACTICE_TABLES}
        db._local.conn = None
        assert migrated == fresh, f"upgraded from version {version}"


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


@pytest.mark.parametrize("version", [0, 1, 2])
def test_deleting_a_score_keeps_the_practice_and_only_forgets_the_piece(upgraded, version):
    """The point of version 3, and the thing the cascade used to destroy.

    Three sessions here were against score 1. After the score row goes they
    are all still present, with every other column untouched, naming no piece.
    The hours were spent whether or not the file is still on disk.
    """
    upgraded(version)
    conn = db.connect()
    before = conn.execute(
        """SELECT id, started_at, seconds, note FROM practice_sessions
            WHERE score_id = 1 ORDER BY id"""
    ).fetchall()
    assert len(before) == 3

    conn.execute("DELETE FROM scores WHERE id = 1")
    conn.commit()

    after = conn.execute(
        """SELECT id, started_at, seconds, note FROM practice_sessions
            WHERE score_id IS NULL ORDER BY id"""
    ).fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    # Nothing was lost from the table at all, not merely "the right number
    # remains" - a count is survived by a delete that took the wrong rows.
    total = conn.execute("SELECT COUNT(*) FROM practice_sessions").fetchone()[0]
    assert total == len(_SESSIONS) + (1 if version >= 2 else 0)


@pytest.mark.parametrize("version", [0, 1, 2])
def test_an_orphaned_session_is_identifiable_as_piece_practice(upgraded, version):
    """A session that outlives its score keeps activity='piece' with no
    score_id, and that pair is what tells it apart from practice that never
    had a piece: a 'piece' session cannot be created without one."""
    upgraded(version)
    conn = db.connect()
    conn.execute("DELETE FROM scores WHERE id = 1")
    conn.commit()
    row = conn.execute("SELECT * FROM practice_sessions WHERE id = 3").fetchone()
    presented = practice.session_dict(row)
    assert presented["score_id"] is None
    assert presented["activity"] == "piece"
    assert presented["score_missing"] is True


@pytest.mark.parametrize("version", [0, 1, 2])
def test_a_goal_already_reached_is_not_unmade_by_deleting_a_score(upgraded, version):
    """The specific failure this has to rule out. A goal counted over any
    practice must not go from met to unmet because a file was tidied away
    afterwards - the days were practised either way, and a record that changes
    its verdict retrospectively is worse than no record."""
    upgraded(version)
    conn = db.connect()
    goal = conn.execute(
        """INSERT INTO practice_goals(owner, period_start, period_end, target_days, scope)
           VALUES (?, '2026-08-01', '2026-08-07', 4, 'all') RETURNING *""",
        (db.DEFAULT_OWNER,),
    ).fetchone()
    conn.commit()
    today = date(2026, 8, 10)
    before = practice.goal_progress(conn, goal, today)
    assert before["met"] is True
    assert before["days_practised"] >= 4

    conn.execute("DELETE FROM scores WHERE id = 1")
    conn.commit()
    after = practice.goal_progress(conn, goal, today)
    assert after["met"] is True
    assert after["countable"] is True
    # Not merely still met - the counts themselves are untouched, because the
    # practice is untouched. A goal that stayed met on fewer days would mean
    # this only held for goals with slack in them.
    assert after["days_practised"] == before["days_practised"]
    assert after["seconds"] == before["seconds"]


def test_a_goal_about_a_deleted_piece_becomes_uncountable_not_unmet(upgraded):
    """A goal scoped to one piece cannot be counted once the piece is gone: the
    sessions are still in the history but no longer identifiable as being about
    it. So it reports that it cannot be counted rather than reporting a
    shortfall - and it is not deleted, because the intention was still formed.
    """
    upgraded(2)
    conn = db.connect()
    goal = conn.execute("SELECT * FROM practice_goals WHERE id = 2").fetchone()
    assert goal["scope"] == "score" and goal["score_id"] == 2

    conn.execute("DELETE FROM scores WHERE id = 2")
    conn.commit()

    survivor = conn.execute("SELECT * FROM practice_goals WHERE id = 2").fetchone()
    assert survivor is not None, "the goal was deleted with the score"
    assert survivor["score_id"] is None
    assert survivor["intent"] == "the awkward bars"
    assert survivor["reflection"] == "away for three days"

    progress = practice.goal_progress(conn, survivor, date(2026, 8, 10))
    assert progress["countable"] is False
    assert progress["met"] is None
    assert progress["met_days"] is None


def test_every_goal_survives_the_upgrade(upgraded):
    """Version 3 rebuilds practice_goals too, so its rows have to be carried
    the same way the sessions are - including the reflection somebody wrote."""
    upgraded(2)
    conn = db.connect()
    rows = conn.execute(
        """SELECT id, period_start, period_end, target_days, target_minutes, scope,
                  score_id, activity, intent, reflection, realistic
             FROM practice_goals ORDER BY id"""
    ).fetchall()
    assert [tuple(r) for r in rows] == _V2_GOALS


def test_the_deepened_columns_survive_the_upgrade_from_version_2(upgraded):
    """Version 2 is the first predecessor whose rows have anything in the
    columns this feature added, so it is the only one that can prove the
    version 3 rebuild carries them."""
    upgraded(2)
    conn = db.connect()
    row = conn.execute(
        """SELECT id, score_id, mode, started_at, local_date, seconds, from_bar, to_bar,
                  from_page, to_page, tempo_bpm, target_tempo_bpm, rating, note
             FROM practice_sessions WHERE id = ?""",
        (_V2_DETAIL[0],),
    ).fetchone()
    assert tuple(row) == _V2_DETAIL


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
# A database somebody has opened by hand
# ---------------------------------------------------------------------------

# A session pointing at a score that is not in the table. Fermata cannot
# produce this - the reference has always been enforced - but the sqlite3
# command line produces it trivially, because ITS default is foreign_keys OFF.
# Before this schema such a row was inert. Now that a rebuild has to copy it,
# it decides whether the application starts.
_DANGLING = (30, 404, "2026-08-04 18:00:00", 1200, "the one on the missing score")


def _with_a_dangling_reference(path, version: int) -> None:
    _legacy_database(path, version)
    conn = sqlite3.connect(path)  # foreign_keys defaults OFF, like the CLI
    conn.execute(
        """INSERT INTO practice_sessions(id, score_id, started_at, seconds, note)
           VALUES (?, ?, ?, ?, ?)""",
        _DANGLING,
    )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("version", [1, 2])
def test_a_dangling_reference_does_not_stop_the_application_starting(
    tmp_path, monkeypatch, caplog, version
):
    """The rebuild copies every row, and with foreign keys ON one row whose
    score is missing is REJECTED on the copy - so the upgrade fails, and fails
    again on every subsequent boot, with the only exits being hand SQL or
    deleting the very rows the migration exists to protect. SQLite's own
    table-rebuild recipe opens by saying to turn them off for this reason.
    """
    path = tmp_path / f"hand_edited_v{version}.db"
    _with_a_dangling_reference(path, version)
    monkeypatch.setattr(db, "DB_PATH", path)
    db._local.conn = None

    with caplog.at_level("WARNING"):
        db.init_db()  # must not raise

    conn = db.connect()
    row = conn.execute(
        "SELECT score_id, seconds, note FROM practice_sessions WHERE id = ?", (_DANGLING[0],)
    ).fetchone()
    assert row is not None, "the row the migration exists to protect was dropped"
    # Put right rather than carried across still broken: NULL is exactly what
    # the reference's own ON DELETE SET NULL would have done had the deletion
    # gone through the database.
    assert row["score_id"] is None
    assert (row["seconds"], row["note"]) == (_DANGLING[3], _DANGLING[4])
    # And the database is left consistent, not merely startable.
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # Said out loud. It is somebody's history, and a row that stops naming a
    # piece is a visible difference.
    assert any("no longer in the database" in r.message for r in caplog.records), caplog.text
    db._local.conn = None


def test_the_rest_of_a_hand_edited_database_is_still_carried(tmp_path, monkeypatch):
    upgraded_path = tmp_path / "hand_edited_rest.db"
    _with_a_dangling_reference(upgraded_path, 1)
    monkeypatch.setattr(db, "DB_PATH", upgraded_path)
    db._local.conn = None
    db.init_db()
    conn = db.connect()
    rows = conn.execute(
        """SELECT id, score_id, started_at, seconds, note FROM practice_sessions
            WHERE id != ? ORDER BY id""",
        (_DANGLING[0],),
    ).fetchall()
    assert [tuple(r) for r in rows] == sorted(_SESSIONS)
    db._local.conn = None


def test_foreign_keys_are_on_again_after_an_upgrade(upgraded):
    """They are turned off for the rebuild. Left off, every write afterwards
    would accept a reference to nothing - the guarantee the rest of the code
    assumes, quietly withdrawn by a successful upgrade."""
    upgraded()
    conn = db.connect()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO practice_sessions(score_id, activity, started_at, seconds)
               VALUES (777, 'piece', datetime('now'), 60)"""
        )
    conn.rollback()


def test_the_rebuild_helper_carries_columns_by_name_not_by_position(app_env):
    """Called directly, on a shape no released version ever had.

    This helper exists for the steps that come after these two as much as for
    them, and a positional copy - INSERT INTO new SELECT * FROM old - is
    correct only while the two column orders happen to agree. The day they do
    not, it silently puts a note into a tempo. Every shape a released version
    produced happens to agree, so the only honest way to test the rule is to
    hand the helper a table that does not.
    """
    conn = db.connect()
    conn.execute("CREATE TABLE oldish (id INTEGER PRIMARY KEY, note TEXT, seconds INTEGER)")
    conn.execute("INSERT INTO oldish(id, note, seconds) VALUES (5, 'kept', 900)")
    conn.commit()

    db._rebuild_carrying_rows(
        conn,
        "oldish",
        "(id INTEGER PRIMARY KEY, seconds INTEGER, added TEXT DEFAULT 'new', note TEXT)",
    )

    row = conn.execute("SELECT id, seconds, added, note FROM oldish").fetchone()
    assert tuple(row) == (5, 900, "new", "kept")


def test_the_rebuild_helper_drops_a_column_the_new_shape_does_not_have(app_env):
    """The only way a rebuild can remove a column, and the reason the carried
    list is the INTERSECTION rather than the new table's columns."""
    conn = db.connect()
    conn.execute("CREATE TABLE oldish (id INTEGER PRIMARY KEY, keep TEXT, retired TEXT)")
    conn.execute("INSERT INTO oldish(id, keep, retired) VALUES (1, 'yes', 'no')")
    conn.commit()

    db._rebuild_carrying_rows(conn, "oldish", "(id INTEGER PRIMARY KEY, keep TEXT)")

    columns = {r["name"] for r in conn.execute("PRAGMA table_info(oldish)")}
    assert columns == {"id", "keep"}
    assert conn.execute("SELECT keep FROM oldish").fetchone()["keep"] == "yes"


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


@pytest.mark.parametrize("rewind_to", [1, 2])
def test_an_upgrade_interrupted_before_its_stamp_landed_resumes_safely(upgraded, rewind_to):
    """The one case a migration step has to survive being re-run: the process
    died after the work committed and before the stamp did. Each step's own
    guard has to see its work already there and do nothing.

    A session with the NEW columns filled in is what makes this bite. A second
    rebuild through step 2 would copy across only the columns the OLD table
    had - id, score, timestamp, length, note - and silently drop the practice
    day, the rating and the tempo out of every row it touched. With only
    carried-over rows in the table there is nothing in those columns to lose,
    and a missing guard looks exactly like a working one.

    Rewound to both stamps: to 1, where every step is offered again, and to 2,
    where only the last one is.
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
    conn.execute(f"PRAGMA user_version = {rewind_to}")
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
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


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
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM practice_sessions").fetchone()[0] == 0
    db._local.conn = None
