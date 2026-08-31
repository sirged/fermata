"""The setlist tables themselves (issue #6): that a fresh install builds them,
that an existing database without them gains them on the next startup (the
auto-upgrade path), and that the two ON DELETE actions the feature relies on
are actually the ones stored - the properties db.py's comments claim, checked
rather than trusted.

WHY NO SCHEMA_VERSION FORWARD-PATH TEST HERE. The brief's forward-path test
("v-old DB opened by new code -> clean upgrade; new DB by old code -> clean
refuse") is the test a SCHEMA_VERSION bump needs. Setlists deliberately do not
bump it - see the long note above SCHEMA_VERSION in db.py: the tables are a
pure addition with no harmful interaction with an older release, so the stamp
stays 5 and there is no version step to test. What IS load-bearing about the
no-bump decision is the two claims tested here: the tables auto-create on an
existing database (so a newer release upgrades in place), and the score_id
cascade is stored in the file (so an older release purging a score still
removes membership rows correctly, which is the whole reason no bump is safe).
"""

from fermata import db


def _table_names(conn) -> set[str]:
    return {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _on_delete(conn, table: str, column: str) -> str | None:
    for r in conn.execute(f"PRAGMA foreign_key_list({table})"):
        if r["from"] == column:
            return r["on_delete"]
    return None


def test_fresh_install_has_both_setlist_tables(app_env):
    conn = db.connect()
    names = _table_names(conn)
    assert "setlists" in names
    assert "setlist_scores" in names


def test_setlist_scores_foreign_keys_cascade_both_ways(app_env):
    """Both references are ON DELETE CASCADE - the setlist so deleting a
    setlist takes its membership rows (and only those), the score so a purge
    takes them too. Flip either to SET NULL / NO ACTION in db.SCHEMA and this
    goes red, which is the guard on the two claims the feature's behaviour
    rests on."""
    conn = db.connect()
    assert _on_delete(conn, "setlist_scores", "setlist_id") == "CASCADE"
    assert _on_delete(conn, "setlist_scores", "score_id") == "CASCADE"


def test_deleting_a_setlist_row_cascades_only_its_membership(app_env, insert_score):
    """The cascade is real, not just declared: a raw DELETE of the setlist row
    removes its membership rows and leaves the scores. This is the row-level
    proof under the API-level test in test_setlists_api.py."""
    conn = db.connect()
    score_id = insert_score(conn, "piece.pdf")
    setlist_id = conn.execute(
        "INSERT INTO setlists(owner, name) VALUES ('local', 'Set')"
    ).lastrowid
    conn.execute(
        "INSERT INTO setlist_scores(setlist_id, score_id, position) VALUES (?, ?, 1)",
        (setlist_id, score_id),
    )
    conn.commit()

    conn.execute("DELETE FROM setlists WHERE id = ?", (setlist_id,))
    conn.commit()

    assert conn.execute("SELECT COUNT(*) AS n FROM setlist_scores").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM scores").fetchone()["n"] == 1


def test_purging_a_score_row_cascades_its_membership(app_env, insert_score):
    """A DELETE FROM scores (a purge, #56) removes the score's membership rows
    through the stored cascade - with PRAGMA foreign_keys=ON, which
    db.connect() sets. This is the property that makes the no-SCHEMA_VERSION-
    bump decision safe: the cascade lives in the file, so even a release with
    no setlist code purges cleanly."""
    conn = db.connect()
    score_id = insert_score(conn, "piece.pdf")
    setlist_id = conn.execute(
        "INSERT INTO setlists(owner, name) VALUES ('local', 'Set')"
    ).lastrowid
    conn.execute(
        "INSERT INTO setlist_scores(setlist_id, score_id, position) VALUES (?, ?, 1)",
        (setlist_id, score_id),
    )
    conn.commit()

    conn.execute("DELETE FROM scores WHERE id = ?", (score_id,))
    conn.commit()

    assert conn.execute("SELECT COUNT(*) AS n FROM setlist_scores").fetchone()["n"] == 0
    # The setlist itself is untouched by a score going.
    assert conn.execute("SELECT COUNT(*) AS n FROM setlists").fetchone()["n"] == 1


def test_tables_are_created_on_an_existing_database(app_env):
    """The auto-upgrade path: a database that predates setlists gains the two
    tables the next time init_db runs, through SCHEMA's own CREATE TABLE IF NOT
    EXISTS - no migration, no bump. Simulated by dropping the tables from an
    up-to-date database (the shape a pre-setlists file has) and re-running
    init_db."""
    conn = db.connect()
    conn.execute("DROP TABLE setlist_scores")
    conn.execute("DROP TABLE setlists")
    conn.commit()
    assert "setlists" not in _table_names(conn)

    db.init_db()

    names = _table_names(conn)
    assert "setlists" in names
    assert "setlist_scores" in names
    # And still usable, not just present.
    conn.execute("INSERT INTO setlists(owner, name) VALUES ('local', 'After upgrade')")
    conn.commit()
    assert conn.execute("SELECT name FROM setlists").fetchone()["name"] == "After upgrade"


def test_schema_version_is_unchanged_by_setlists(app_env):
    """Setlists did not bump the stamp - the deliberate decision documented at
    SCHEMA_VERSION. Pinned so a later change cannot quietly move it without a
    reviewer seeing this assertion and the reasoning it points at."""
    conn = db.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert db.SCHEMA_VERSION == 5
