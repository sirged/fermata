"""Shared fixtures for the transcription tests.

Real sample PDFs from the user's library are used as fixtures (they exercise
paths synthetic PDFs can't: real Finale/Sibelius engraving output). They're
read-only and never bundled with the repo - point FERMATA_TEST_LIBRARY at a
copy of the library root to run these; tests skip themselves otherwise.
"""

import os
from pathlib import Path

import pytest

_FIXTURE_RELATIVE_PATHS = {
    "zanarkand": "Patreon/John Oeth/Final Fantasy/FF X/To Zanarkand (Final Fantasy X).pdf",
    "tarrega": "Classical/Tarrega/Tarrega-Study-in-C-Guitar-Free.pdf",
    "claire_de_lune": "Favorites/ClairDeLuneGuitar.pdf",
}


def _library_root() -> Path | None:
    root = os.environ.get("FERMATA_TEST_LIBRARY")
    if not root:
        return None
    p = Path(root)
    return p if p.is_dir() else None


def _fixture_path(name: str) -> Path | None:
    root = _library_root()
    if root is None:
        return None
    p = root / _FIXTURE_RELATIVE_PATHS[name]
    return p if p.is_file() else None


@pytest.fixture
def zanarkand_pdf() -> Path:
    p = _fixture_path("zanarkand")
    if p is None:
        pytest.skip("FERMATA_TEST_LIBRARY not set (or missing 'To Zanarkand' fixture)")
    return p


@pytest.fixture
def tarrega_pdf() -> Path:
    p = _fixture_path("tarrega")
    if p is None:
        pytest.skip("FERMATA_TEST_LIBRARY not set (or missing Tarrega fixture)")
    return p


@pytest.fixture
def claire_de_lune_pdf() -> Path:
    p = _fixture_path("claire_de_lune")
    if p is None:
        pytest.skip("FERMATA_TEST_LIBRARY not set (or missing Clair de Lune fixture)")
    return p


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Point the db module at a throwaway sqlite file for one test, and reset
    the per-thread cached connection so the swap actually takes effect."""
    from fermata import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fermata_test.db")
    db._local.conn = None
    db.init_db()
    yield
    db._local.conn = None


@pytest.fixture
def insert_score():
    def _insert(conn, rel_path: str, title: str = "Test Score") -> int:
        cur = conn.execute(
            """INSERT INTO scores(title, path, file_type, hash, size, mtime)
               VALUES (?, ?, 'pdf', 'deadbeef', 1, 0.0)""",
            (title, rel_path),
        )
        conn.commit()
        return cur.lastrowid

    return _insert
