"""The named-scope tables themselves (issue #236): that a fresh install builds
them, that an existing database gains them and the practice_sessions column on
the next startup, that COLUMN_ADDITIONS really is idempotent across repeated
startups, and that the two ON DELETE actions the feature relies on are the ones
actually stored - the properties db.py's comments claim, checked rather than
trusted.

WHY NO SCHEMA_VERSION FORWARD-PATH TEST HERE, the same reasoning
test_setlists_schema.py sets out: the stamp is not bumped (see the long note
above SCHEMA_VERSION in db.py), so there is no version step to test. What IS
load-bearing about that decision is checked below - the tables and the column
arrive on an existing database with no migration, and the SET NULL that keeps
practice history intact when a preset goes is stored in the file rather than
enforced by code that an older release would not be running.
"""

import pytest

from fermata import db


def _table_names(conn) -> set[str]:
    return {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _on_delete(conn, table: str, column: str) -> str | None:
    for r in conn.execute(f"PRAGMA foreign_key_list({table})"):
        if r["from"] == column:
            return r["on_delete"]
    return None


def _a_session(conn, preset_id=None) -> int:
    return conn.execute(
        """INSERT INTO practice_sessions(owner, activity, started_at, seconds, preset_id)
           VALUES ('local', 'fretboard', datetime('now'), 600, ?)""",
        (preset_id,),
    ).lastrowid


def _a_preset(conn, name="Fifth position", strings=(1, 2)) -> int:
    preset_id = conn.execute(
        """INSERT INTO trainer_scope_presets(owner, name, start_fret, end_fret)
           VALUES ('local', ?, 5, 9)""",
        (name,),
    ).lastrowid
    conn.executemany(
        "INSERT INTO trainer_scope_preset_strings(preset_id, string_number) VALUES (?, ?)",
        [(preset_id, n) for n in strings],
    )
    return preset_id


def test_fresh_install_has_both_preset_tables_and_the_session_column(app_env):
    conn = db.connect()
    names = _table_names(conn)
    assert "trainer_scope_presets" in names
    assert "trainer_scope_preset_strings" in names
    assert "preset_id" in _columns(conn, "practice_sessions")


def test_a_name_is_unique_per_owner(app_env):
    """The unique index is real, not merely intended - api.create_trainer_preset
    checks for a clash and answers 409, and this is the guard underneath that
    check for anything writing rows another way."""
    conn = db.connect()
    _a_preset(conn, name="Fifth position")
    conn.commit()
    with pytest.raises(Exception) as caught:
        _a_preset(conn, name="Fifth position")
    assert "UNIQUE" in str(caught.value).upper()
    conn.rollback()


def test_a_string_is_in_a_preset_at_most_once(app_env):
    conn = db.connect()
    preset_id = _a_preset(conn, strings=(1,))
    conn.commit()
    with pytest.raises(Exception) as caught:
        conn.execute(
            "INSERT INTO trainer_scope_preset_strings(preset_id, string_number) VALUES (?, 1)",
            (preset_id,),
        )
    assert "UNIQUE" in str(caught.value).upper()
    conn.rollback()


def test_the_two_foreign_keys_are_the_actions_the_feature_depends_on(app_env):
    """Flip either in db.SCHEMA and this goes red. The string set CASCADEs
    because a row saying "(this preset) includes (string 3)" states nothing
    once the preset is gone; the session SET NULLs because "forty minutes of
    fretboard practice on Tuesday" is still true when the scope it was named
    under is deleted."""
    conn = db.connect()
    assert _on_delete(conn, "trainer_scope_preset_strings", "preset_id") == "CASCADE"
    assert _on_delete(conn, "practice_sessions", "preset_id") == "SET NULL"


def test_deleting_a_preset_row_takes_its_strings_and_spares_the_practice(app_env):
    """The row-level proof under the API-level test in
    test_trainer_presets_api.py: with PRAGMA foreign_keys=ON (which
    db.connect() sets), a raw DELETE removes the string set and clears the
    session's reference while leaving the session itself whole."""
    conn = db.connect()
    preset_id = _a_preset(conn)
    session_id = _a_session(conn, preset_id)
    conn.commit()

    conn.execute("DELETE FROM trainer_scope_presets WHERE id = ?", (preset_id,))
    conn.commit()

    assert (
        conn.execute("SELECT COUNT(*) AS n FROM trainer_scope_preset_strings").fetchone()["n"]
        == 0
    )
    row = conn.execute(
        "SELECT * FROM practice_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    assert row is not None
    assert row["preset_id"] is None
    assert row["seconds"] == 600
    assert row["activity"] == "fretboard"


def test_tables_and_column_are_created_on_an_existing_database(app_env):
    """The auto-upgrade path, both halves of it. The two tables arrive through
    SCHEMA's own CREATE TABLE IF NOT EXISTS; the practice_sessions column
    arrives through COLUMN_ADDITIONS, which is the mechanism that reaches a
    table CREATE TABLE IF NOT EXISTS cannot touch. Simulated by taking an
    up-to-date database back to the shape a pre-#236 file has - drop the
    tables, and rebuild practice_sessions without the column - and re-running
    init_db."""
    conn = db.connect()
    conn.execute("DROP TABLE trainer_scope_preset_strings")
    conn.execute("DROP TABLE trainer_scope_presets")
    # A practice_sessions with no preset_id, carrying a row across, which is
    # exactly what an older install has.
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """CREATE TABLE old_sessions (
               id INTEGER PRIMARY KEY,
               owner TEXT NOT NULL DEFAULT 'local',
               score_id INTEGER REFERENCES scores(id) ON DELETE SET NULL,
               activity TEXT NOT NULL DEFAULT 'piece',
               mode TEXT, started_at TEXT NOT NULL, local_date TEXT,
               seconds INTEGER NOT NULL, from_bar INTEGER, to_bar INTEGER,
               from_page INTEGER, to_page INTEGER, tempo_bpm INTEGER,
               target_tempo_bpm INTEGER, rating INTEGER, note TEXT
           )"""
    )
    conn.execute(
        """INSERT INTO old_sessions(owner, activity, started_at, seconds, note)
           VALUES ('local', 'fretboard', datetime('now'), 900, 'Fret to note. Frets 0-5.')"""
    )
    conn.execute("DROP TABLE practice_sessions")
    conn.execute("ALTER TABLE old_sessions RENAME TO practice_sessions")
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    assert "trainer_scope_presets" not in _table_names(conn)
    assert "preset_id" not in _columns(conn, "practice_sessions")

    db.init_db()

    names = _table_names(conn)
    assert "trainer_scope_presets" in names
    assert "trainer_scope_preset_strings" in names
    assert "preset_id" in _columns(conn, "practice_sessions")
    # The pre-existing row survived with its column added as NULL, which is
    # what "no backfill needed" means here: nothing was practised under a
    # named scope before there were any.
    old = conn.execute("SELECT * FROM practice_sessions").fetchone()
    assert old["seconds"] == 900
    assert old["preset_id"] is None
    # And the new column is usable, not merely present.
    preset_id = _a_preset(conn)
    new_id = _a_session(conn, preset_id)
    conn.commit()
    stored = conn.execute(
        "SELECT preset_id FROM practice_sessions WHERE id = ?", (new_id,)
    ).fetchone()
    assert stored["preset_id"] == preset_id


def test_the_added_column_survives_repeated_startups_unchanged(app_env):
    """COLUMN_ADDITIONS runs on EVERY startup, which is the only reason it is
    safe to express an upgrade through it - so running it again must be a
    no-op, not a second ALTER TABLE (an error) and not anything that touches a
    row. Three more init_db calls, with data in the table."""
    conn = db.connect()
    preset_id = _a_preset(conn)
    session_id = _a_session(conn, preset_id)
    conn.commit()
    before = _columns(conn, "practice_sessions")

    for _ in range(3):
        db.init_db()

    assert _columns(conn, "practice_sessions") == before
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM pragma_table_info('practice_sessions') "
            "WHERE name = 'preset_id'"
        ).fetchone()["n"]
        == 1
    )
    row = conn.execute(
        "SELECT * FROM practice_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    assert row["preset_id"] == preset_id
    assert row["seconds"] == 600
    # The foreign key is still there after the repeat runs - the check
    # _add_missing_columns makes on an existing column, seen to hold.
    assert _on_delete(conn, "practice_sessions", "preset_id") == "SET NULL"


def test_schema_version_is_unchanged_by_named_scopes(app_env):
    """#236 did not bump the stamp - the deliberate decision documented at
    SCHEMA_VERSION. Pinned so a later change cannot quietly move it without a
    reviewer seeing this assertion and the reasoning it points at."""
    conn = db.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert db.SCHEMA_VERSION == 5
