"""Pins docs/practice-data.md's hand-written enumerations and column lists to
the server they describe (issue #259).

#254's review found the chord-quality row three qualities stale after the API
had grown to accept five, and it passed CI because nothing read the doc - the
same gap test_api_docs.py closed for docs/api.md's routes, never opened here.
This module is that guard for practice-data.md: it parses the doc's own
markdown tables (locating each row by its backticked COLUMN NAME, never by
line number, so a paragraph inserted above a table cannot silently point this
at the wrong row) and asserts the values it documents equal the tuples and
dict keys `server/fermata/trainer.py` actually enforces, and that each
table's documented column list matches the real column list `server/fermata/
db.py`'s schema creates - the same "read the real thing back, do not trust a
parallel description of it" discipline test_export_table_names_matches_
every_table_the_schema_creates (test_portability_api.py) already applies to
the export catalog.

Deliberately does NOT check every table this doc describes - practice_goals
and its prose are out of scope for #259 (see that issue's no-gos), and adding
a table here later is a decision for whoever adds it, not something this
guard should invent an opinion about by omission.
"""

import re
from pathlib import Path

import pytest

from fermata import trainer

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "practice-data.md"

_BACKTICK = re.compile(r"`([^`]+)`")


@pytest.fixture(scope="module")
def doc_text():
    return DOC_PATH.read_text(encoding="utf-8")


def _table_rows(doc_text: str, anchor: str) -> list[tuple[str, str]]:
    """Every data row of the markdown table that follows `anchor` in the doc -
    `anchor` is a piece of literal text (a heading like "## Trainer attempts",
    or an inline table title like "`trainer_scope_presets`:") that appears
    exactly once before the table this function is meant to read, so locating
    the table is finding the next "| Column | Meaning |" header after that
    text rather than counting lines to it.

    Returns (first_cell, second_cell) pairs - a combined row like
    "`from_bar`, `to_bar`" | "The bars worked." comes back as one pair, the
    same row a person reading the doc sees, so a caller that wants either
    column's name checks the first cell for its own backticked name rather
    than assuming one row names exactly one column.
    """
    start = doc_text.index(anchor)
    header = "| Column | Meaning |"
    header_at = doc_text.index(header, start)
    lines = doc_text[header_at:].splitlines()
    rows = []
    # lines[0] is the header itself, lines[1] the "| --- | --- |" separator -
    # both skipped; the table ends at the first line that is not a table row,
    # exactly where the blank line after every table in this doc falls.
    for line in lines[2:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        rows.append((cells[0], cells[1] if len(cells) > 1 else ""))
    return rows


def _row_for(rows: list[tuple[str, str]], column_name: str) -> str:
    """The Meaning cell of the row naming `column_name` - found by searching
    every row's first cell for that exact backticked name, so a row that
    documents two columns together (`"`key_root`, `key_quality`"`) is found
    by either name."""
    needle = f"`{column_name}`"
    for first_cell, meaning in rows:
        if needle in first_cell:
            return meaning
    raise AssertionError(
        f"no row documents a `{column_name}` column under this table - renamed, "
        "or the table this test reads from changed shape?"
    )


def _documented_column_names(rows: list[tuple[str, str]]) -> set[str]:
    """Every column name a table's rows document - a row's first cell may
    name more than one column (a combined row like "`from_bar`, `to_bar`"),
    so this is every backticked token in every first cell, not one per row."""
    names: set[str] = set()
    for first_cell, _ in rows:
        names.update(_BACKTICK.findall(first_cell))
    return names


def _enum_values(meaning_cell: str) -> set[str]:
    """The enumeration values a Meaning cell names, out of every backticked
    token in it - filtering out the two shapes of backticked token that
    appear in these cells but never name a stored value:

    - a file reference (`` `chord-theory.js` ``, `` `trainer.py` ``) - always
      contains a '.', which no enumeration value here ever does;
    - the SQL `NULL` literal, used throughout this doc to mean "unset", never
      as a stored enum value.

    A value quoted as `` `'fret_to_note'` `` (single quotes inside the
    backticks, matching how db.py's own DEFAULT/comparison literals read) has
    those quotes stripped, so it compares equal to trainer.py's own bare
    string.
    """
    values = set()
    for token in _BACKTICK.findall(meaning_cell):
        if "." in token or token == "NULL":
            continue
        values.add(token.strip("'"))
    return values


# ---------------------------------------------------------------------------
# Enumerations: docs/practice-data.md's prose values against trainer.py's
# real tuples and dict keys.
# ---------------------------------------------------------------------------


def test_trainer_attempts_drill_enumeration_matches_trainer_py(doc_text):
    rows = _table_rows(doc_text, "## Trainer attempts")
    documented = _enum_values(_row_for(rows, "drill"))
    assert documented == set(trainer.DRILLS)


def test_trainer_attempts_direction_enumeration_matches_trainer_py(doc_text):
    rows = _table_rows(doc_text, "## Trainer attempts")
    documented = _enum_values(_row_for(rows, "direction"))
    assert documented == set(trainer.DIRECTIONS)


def test_trainer_chord_attempts_drill_enumeration_matches_trainer_py(doc_text):
    rows = _table_rows(doc_text, "## Trainer chord attempts")
    documented = _enum_values(_row_for(rows, "drill"))
    assert documented == set(trainer.CHORD_DRILLS)


def test_trainer_chord_attempts_direction_enumeration_matches_trainer_py(doc_text):
    rows = _table_rows(doc_text, "## Trainer chord attempts")
    documented = _enum_values(_row_for(rows, "direction"))
    assert documented == set(trainer.CHORD_DIRECTIONS)


def test_chord_quality_enumeration_matches_trainer_py(doc_text):
    """target_root/target_quality's own row names the five qualities in a
    parenthetical list alongside two FILE references (`chord-theory.js`,
    `trainer.py`) that _enum_values must not mistake for a sixth and seventh
    quality - see that helper's own docstring for the '.' filter this row is
    exactly why."""
    rows = _table_rows(doc_text, "## Trainer chord attempts")
    documented = _enum_values(_row_for(rows, "target_quality"))
    assert documented == set(trainer.CHORD_QUALITIES)


def test_key_quality_enumeration_matches_trainer_py(doc_text):
    rows = _table_rows(doc_text, "`trainer_scope_presets`:")
    documented = _enum_values(_row_for(rows, "key_quality"))
    assert documented == set(trainer.KEY_QUALITIES)


# ---------------------------------------------------------------------------
# Column lists: docs/practice-data.md's tables against db.py's real schema -
# read back from a freshly initialised database, the same way
# test_export_table_names_matches_every_table_the_schema_creates
# (test_portability_api.py) reads the export catalog against sqlite_master
# rather than against db.py's CREATE TABLE text, so a column added by a
# migration rather than by the CREATE TABLE statement itself still counts.
# ---------------------------------------------------------------------------

_TABLE_ANCHORS = {
    "practice_sessions": "## Sessions",
    "trainer_attempts": "## Trainer attempts",
    "trainer_chord_attempts": "## Trainer chord attempts",
    "trainer_scope_presets": "`trainer_scope_presets`:",
    "trainer_scope_preset_strings": "`trainer_scope_preset_strings`, one row per string:",
}


@pytest.mark.parametrize("table_name", sorted(_TABLE_ANCHORS))
def test_documented_columns_match_the_real_table(doc_text, table_name, app_env):
    from fermata import db

    rows = _table_rows(doc_text, _TABLE_ANCHORS[table_name])
    documented = _documented_column_names(rows)

    conn = db.connect()
    try:
        actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    finally:
        conn.close()
    # Sanity floor on the read itself - PRAGMA table_info silently returns
    # zero rows for a table name that does not exist (a typo in
    # _TABLE_ANCHORS's key, say), which would otherwise pass this test for
    # the wrong reason: an empty `actual` trivially equal to an empty
    # `missing_from_doc`.
    assert actual, f"{table_name}: PRAGMA table_info returned no columns - does this table exist?"

    missing_from_doc = actual - documented
    extra_in_doc = documented - actual
    assert not missing_from_doc, (
        f"{table_name}: column(s) {sorted(missing_from_doc)} exist in db.py's schema but are "
        "not documented in docs/practice-data.md"
    )
    assert not extra_in_doc, (
        f"{table_name}: docs/practice-data.md documents column(s) {sorted(extra_in_doc)} that "
        f"{table_name} does not have"
    )
