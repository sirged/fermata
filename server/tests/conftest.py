"""Shared fixtures for the transcription tests.

There are two sources of engraved PDFs here and they do different jobs.

ENGRAVED FIXTURES (fixtures/engraved) are committed, so they run everywhere
including CI. They are engraved from MusicXML in the repository by
server/tools/tab_extract/engrave_fixtures.py, which means their licence is
not in question, their content is known exactly, and what the extractor
reports can be compared against what was asked for. A missing engraved
fixture is a FAILURE, never a skip - the whole point is that these always
run.

REAL LIBRARY FIXTURES come from the maintainer's own sheet music (real
Finale/Sibelius output, exercising paths nothing generated here reaches: the
Maestro glyph-ID fingerprint, Opus's PUA names, two-voice fingerstyle
writing at scale). They cannot be committed, so point FERMATA_TEST_LIBRARY
at a library root to run them; they skip otherwise. That skip is now
counted and announced at the end of the run - see
pytest_terminal_summary - because a green run that quietly skipped a third
of the extraction suite is exactly how this gap went unnoticed.
"""

import os
from pathlib import Path

import pytest

ENGRAVED_DIR = Path(__file__).resolve().parent / "fixtures" / "engraved"

_FIXTURE_RELATIVE_PATHS = {
    "zanarkand": "Patreon/John Oeth/Final Fantasy/FF X/To Zanarkand (Final Fantasy X).pdf",
    "tarrega": "Classical/Tarrega/Tarrega-Study-in-C-Guitar-Free.pdf",
    "claire_de_lune": "Favorites/ClairDeLuneGuitar.pdf",
}

# Skips for want of a library are COUNTED HERE as they happen, rather than
# recognised afterwards by matching text in the skip reason. Matching text
# would quietly stop counting the day someone worded a new skip differently,
# and an undercount here reads as "everything ran" - the exact failure this
# summary exists to prevent.
_library_skips = []


def skip_without_library(reason: str):
    _library_skips.append(reason)
    pytest.skip(reason)


def engraved_pdf(name: str) -> Path:
    """One committed engraved fixture. Absent means the repository is broken,
    not that the test cannot run, so this fails rather than skipping."""
    path = ENGRAVED_DIR / f"{name}.pdf"
    if not path.is_file():
        raise AssertionError(
            f"engraved fixture {name}.pdf is missing from {ENGRAVED_DIR} - it is "
            "committed on purpose; regenerate with "
            "server/tools/tab_extract/engrave_fixtures.py"
        )
    return path


@pytest.fixture
def engraved():
    return engraved_pdf


@pytest.fixture
def extractable_pdf() -> Path:
    """A committed engraved score with notation over tablature - for tests
    that need SOME extractable PDF and are not about whose engraver made it."""
    return engraved_pdf("notation_and_tab")


@pytest.fixture
def non_extractable_pdf() -> Path:
    """A committed engraved score with no tablature staff at all."""
    return engraved_pdf("notation_only")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say out loud how much of the suite did not run for want of a library.

    Without this the only way to notice was to compare a CI log against a
    local run by hand, which is why 36 skipped extraction tests sat
    unnoticed. A count that has to be read off the screen is not a
    guarantee, but silence was not one either."""
    if not _library_skips:
        if _library_root() is not None:
            terminalreporter.write_sep(
                "=", "real-library tests all ran (FERMATA_TEST_LIBRARY is set)", green=True)
        return
    terminalreporter.write_sep(
        "=",
        f"{len(_library_skips)} test(s) skipped for want of a sheet music library - this "
        "run did NOT exercise extraction against real engraved scores; set "
        "FERMATA_TEST_LIBRARY to a library root to run them",
        yellow=True,
        bold=True,
    )


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
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing 'To Zanarkand' fixture)")
    return p


@pytest.fixture
def tarrega_pdf() -> Path:
    p = _fixture_path("tarrega")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing Tarrega fixture)")
    return p


@pytest.fixture
def claire_de_lune_pdf() -> Path:
    p = _fixture_path("claire_de_lune")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing Clair de Lune fixture)")
    return p


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Point the db module at a throwaway sqlite file for one test, and reset
    the per-thread cached connection so the swap actually takes effect.

    The config module's three directories are redirected here as well, and the
    library one is CREATED. Fermata refuses to start without a library folder
    on purpose (see config.ensure_dirs and #95), so a test that goes through
    main.py's lifespan needs one that exists - and pointing it at tmp_path
    rather than making the repository's own ./library is what keeps a test run
    from writing into the checkout, which the config directory previously did.
    """
    from fermata import config, db

    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(config, "LIBRARY_DIR", library)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "config" / "cache")
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
