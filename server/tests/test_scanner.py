"""What a scan is allowed to conclude from a walk of the library.

These tests exist because of #95, where the answer was "anything at all". A
library folder that read as empty - a bind mount that did not appear, a drive
that did not come back - made the startup scan delete every score row, and
three tables cascaded from it: practice history, tags, and hand-corrected
transcriptions. The scores came back on the next good scan, because they are
files. Nothing else did.

So the subject of this file is not really the scanner. It is the four things
that must be true of it:

  1. an absence is recorded as an absence (scores.missing_since), never as a
     deletion, so the irreplaceable work hanging off a score row stays there;
  2. evidence that cannot be believed is acted on by nobody - a scan that sees
     nothing where there was something changes nothing and says why;
  3. a file coming back undoes the mark by itself, including in the case that
     looks like nothing happened (same size, same mtime);
  4. a scan that takes a lot of the library away says so where somebody will
     see it.

Written with .gp files throughout unless a test is about PDFs: the scanner does
no parsing for that type, so the content can be arbitrary bytes and these tests
stay about reconciliation rather than about PyMuPDF.
"""

import logging
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fermata import api, config, db, scanner, thumbs


@pytest.fixture
def library(app_env, tmp_path, monkeypatch):
    """A throwaway library the scanner will actually walk.

    app_env has already created tmp_path/library and pointed config at it; the
    scanner and thumbnailer hold their own module-level bindings (imported by
    value), so those are redirected here too.
    """
    root = tmp_path / "library"
    monkeypatch.setattr(scanner, "LIBRARY_DIR", root)
    monkeypatch.setattr(api, "LIBRARY_DIR", root)
    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "config" / "cache")
    return root


@pytest.fixture
def client(library):
    """The router alone against the same throwaway library and database."""
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def put(root, rel: str, content: bytes = b"a score") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def rows(conn=None):
    conn = conn or db.connect()
    return {
        r["path"]: dict(r) for r in conn.execute("SELECT * FROM scores")
    }


def attach_irreplaceable_work(score_id: int, tag: str = "wedding") -> None:
    """Everything about a score that no rescan could ever produce again."""
    conn = db.connect()
    conn.execute(
        """INSERT INTO practice_sessions(score_id, activity, started_at, local_date, seconds, note)
           VALUES (?, 'piece', '2026-08-01T19:00:00', '2026-08-01', 2400, 'felt rough')""",
        (score_id,),
    )
    # One goal per period per owner, so each score's goal gets its own week.
    conn.execute(
        """INSERT INTO practice_goals(period_start, period_end, target_days, scope, score_id, intent)
           VALUES (?, ?, 5, 'score', ?, 'get it to tempo')""",
        (f"2026-01-{score_id:02d}", f"2026-01-{score_id + 6:02d}", score_id),
    )
    conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (tag,))
    conn.execute(
        """INSERT OR IGNORE INTO score_tags(score_id, tag_id)
           SELECT ?, id FROM tags WHERE name = ?""",
        (score_id, tag),
    )
    conn.execute(
        """INSERT INTO transcriptions(score_id, format, content, source)
           VALUES (?, 'alphatex', 'three hours of hand correction', 'edited')""",
        (score_id,),
    )
    conn.commit()


def irreplaceable_counts() -> dict:
    conn = db.connect()
    return {
        "sessions_about_a_piece": conn.execute(
            "SELECT COUNT(*) FROM practice_sessions WHERE score_id IS NOT NULL"
        ).fetchone()[0],
        "goals_about_a_piece": conn.execute(
            "SELECT COUNT(*) FROM practice_goals WHERE score_id IS NOT NULL"
        ).fetchone()[0],
        "tag_links": conn.execute("SELECT COUNT(*) FROM score_tags").fetchone()[0],
        "edited_transcriptions": conn.execute(
            "SELECT COUNT(*) FROM transcriptions WHERE source = 'edited'"
        ).fetchone()[0],
    }


# ---------------------------------------------------------------------------
# The widest trigger: a library that reads as empty.
# ---------------------------------------------------------------------------


def test_an_empty_library_with_scores_on_record_changes_nothing(library):
    """#95, reproduced and then refused.

    The scenario in full: a library with scores in it, practice logged against
    them, tags applied, a transcription corrected by hand - and then a scan
    that sees an empty folder, which is what a missing bind mount looks like.
    """
    put(library, "Classical/Study in C.gp")
    put(library, "Classical/Prelude.gp", b"another score")
    scanner._scan()
    before = rows()
    assert len(before) == 2
    for row in before.values():
        attach_irreplaceable_work(row["id"])
    work = irreplaceable_counts()
    assert work == {
        "sessions_about_a_piece": 2,
        "goals_about_a_piece": 2,
        "tag_links": 2,
        "edited_transcriptions": 2,
    }

    for path in library.rglob("*.gp"):
        path.unlink()
    scanner._scan()

    status = scanner.scan_status()
    assert status["refused"] is True
    assert "no readable score files at all" in status["refused_reason"]
    assert str(library) in status["refused_reason"]
    # Nothing at all changed - not the rows, not the marks, not the work.
    assert rows() == before
    assert irreplaceable_counts() == work
    assert status["missing"] == 0


def test_a_genuinely_empty_library_on_a_first_run_is_not_a_fault(library):
    """The guard is about losing scores, not about having none.

    A first run against an empty folder is the ordinary way to start, and it
    must not report a fault or refuse anything.
    """
    scanner._scan()
    status = scanner.scan_status()
    assert status["refused"] is False
    assert status["refused_reason"] is None
    assert status["total"] == 0
    assert rows() == {}


def test_a_library_folder_that_is_not_there_stops_the_scan_rather_than_emptying_it(
    library, monkeypatch
):
    """The library can go away while Fermata is running, and rglob does not say so.

    An unmount, a sleeping drive, a network share dropping - and a scan can be
    triggered by hand or by an upload long after startup checked the folder.
    rglob over a path that is not there returns nothing rather than raising, so
    without an explicit check this reads as an empty library.
    """
    put(library, "Classical/Study in C.gp")
    scanner._scan()
    before = rows()
    assert len(before) == 1

    monkeypatch.setattr(scanner, "LIBRARY_DIR", library / "gone")
    scanner._scan()

    status = scanner.scan_status()
    assert status["refused"] is True
    assert "is not there, or is not a folder" in status["refused_reason"]
    assert rows() == before


# ---------------------------------------------------------------------------
# An absence is a mark, not a deletion.
# ---------------------------------------------------------------------------


def test_a_vanished_file_is_marked_missing_and_keeps_everything_hanging_off_it(library):
    """One file goes, out of enough that no threshold is in play.

    This is the heart of the change. The row survives, it says when its file
    went, and the practice, the goal, the tag and the hand-corrected
    transcription are all still attached to it - which is precisely what a
    DELETE could not do, because three tables cascade from scores.
    """
    for n in range(4):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    gone_id = rows()["Classical/Study 0.gp"]["id"]
    attach_irreplaceable_work(gone_id)
    work = irreplaceable_counts()

    (library / "Classical" / "Study 0.gp").unlink()
    scanner._scan()

    status = scanner.scan_status()
    assert status["refused"] is False
    assert status["missing"] == 1
    after = rows()
    assert len(after) == 4, "the row must survive its file"
    assert after["Classical/Study 0.gp"]["id"] == gone_id
    assert after["Classical/Study 0.gp"]["missing_since"] is not None
    assert after["Classical/Study 1.gp"]["missing_since"] is None
    assert irreplaceable_counts() == work


def test_the_missing_mark_says_when_and_is_not_refreshed_by_later_scans(library):
    """missing_since is "since when", so a later scan must not move it.

    A row that has been missing since March is a different fact from one that
    went this morning, and it is the fact somebody needs to work out what
    happened. Re-stamping it on every scan would erase exactly that.
    """
    for n in range(4):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    (library / "Classical" / "Study 0.gp").unlink()
    scanner._scan()
    first = rows()["Classical/Study 0.gp"]["missing_since"]
    assert first is not None

    db.connect().execute(
        "UPDATE scores SET missing_since = '2020-01-01 00:00:00' WHERE path = ?",
        ("Classical/Study 0.gp",),
    )
    db.connect().commit()
    scanner._scan()

    assert rows()["Classical/Study 0.gp"]["missing_since"] == "2020-01-01 00:00:00"
    # And it is not counted again - it was not news this time.
    assert scanner.scan_status()["missing"] == 0


def test_nothing_in_the_scanner_deletes_a_score_row():
    """A guard on the whole point of the change, not on one path through it.

    Every test above checks an outcome of one scenario. This checks the
    property: after #95 there is no code path in the scanner that removes a
    score row, so no future scenario - including one nobody has thought of -
    can reach the cascade through a filesystem walk. If a delete is ever needed
    here again, it should be added deliberately, with a reason, and this test
    should fail while that happens.
    """
    source = (
        __import__("pathlib").Path(scanner.__file__).read_text(encoding="utf-8").upper()
    )
    assert "DELETE FROM SCORES" not in source


# ---------------------------------------------------------------------------
# A file coming back.
# ---------------------------------------------------------------------------


def test_a_file_that_comes_back_untouched_stops_being_missing(library):
    """The trap this whole design nearly fell into.

    _scan_file short-circuits on unchanged size and mtime, and a remounted file
    is unchanged by definition - so clearing the mark after that shortcut would
    leave it set for ever, on every subsequent scan, and "a remount recovers by
    itself" would be false in the one case that matters most.
    """
    for n in range(4):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    target = library / "Classical" / "Study 0.gp"
    content, stat = target.read_bytes(), target.stat()

    target.unlink()
    scanner._scan()
    assert rows()["Classical/Study 0.gp"]["missing_since"] is not None

    # Restored byte-for-byte, with its mtime, exactly as a remount presents it.
    target.write_bytes(content)
    import os

    os.utime(target, (stat.st_atime, stat.st_mtime))
    scanner._scan()

    row = rows()["Classical/Study 0.gp"]
    assert row["missing_since"] is None
    assert scanner.scan_status()["restored"] == 1


def test_a_file_that_comes_back_under_another_name_stops_being_missing(library):
    """The relink path has to clear the mark too.

    A drive that comes back with things reorganised reaches its rows through
    the content-hash relink rather than by path, and a row whose file this scan
    is looking at is not missing whichever way it was found.
    """
    for n in range(4):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    original = rows()["Classical/Study 0.gp"]
    content = (library / "Classical" / "Study 0.gp").read_bytes()
    attach_irreplaceable_work(original["id"])
    work = irreplaceable_counts()

    (library / "Classical" / "Study 0.gp").unlink()
    scanner._scan()
    assert rows()["Classical/Study 0.gp"]["missing_since"] is not None

    put(library, "Baroque/Study 0.gp", content)
    scanner._scan()

    after = rows()
    assert "Classical/Study 0.gp" not in after
    assert after["Baroque/Study 0.gp"]["id"] == original["id"]
    assert after["Baroque/Study 0.gp"]["missing_since"] is None
    assert scanner.scan_status()["restored"] == 1
    assert irreplaceable_counts() == work


# ---------------------------------------------------------------------------
# The proportional guard, and its floor.
# ---------------------------------------------------------------------------


def test_losing_more_than_half_the_library_in_one_pass_changes_nothing(library):
    """A partly readable folder is more likely than a library that halved itself."""
    for n in range(12):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    before = rows()
    assert len(before) == 12

    for n in range(7):
        (library / "Classical" / f"Study {n}.gp").unlink()
    scanner._scan()

    status = scanner.scan_status()
    assert status["refused"] is True
    assert "7 of the 12 score(s)" in status["refused_reason"]
    assert status["missing"] == 0
    assert rows() == before


def test_losing_less_than_half_the_library_is_believed(library):
    """Below the line this is ordinary use - a collection deleted, a series moved."""
    for n in range(12):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()

    for n in range(5):
        (library / "Classical" / f"Study {n}.gp").unlink()
    scanner._scan()

    status = scanner.scan_status()
    assert status["refused"] is False
    assert status["missing"] == 5
    marked = [r for r in rows().values() if r["missing_since"] is not None]
    assert len(marked) == 5


def test_exactly_half_is_on_the_refusing_side_of_the_line(library):
    """Where a boundary sits is a decision, so it is written down as a test.

    Half of twelve is six, and six is refused: the comparison is >=, because
    "half my library went missing between two startups" is already the
    unlikelier of the two explanations.
    """
    for n in range(12):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    before = rows()

    for n in range(6):
        (library / "Classical" / f"Study {n}.gp").unlink()
    scanner._scan()

    assert scanner.scan_status()["refused"] is True
    assert rows() == before


def test_a_proportion_of_a_tiny_library_is_not_evidence_of_anything(library):
    """Below the floor, only the categorical zero-files test applies.

    Deleting three of four scores is seventy-five per cent and completely
    unremarkable. A proportion needs enough rows behind it to mean something,
    and the exposure below the floor is small and, either way, not a deletion.
    """
    for n in range(4):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()

    for n in range(3):
        (library / "Classical" / f"Study {n}.gp").unlink()
    scanner._scan()

    status = scanner.scan_status()
    assert status["refused"] is False
    assert status["missing"] == 3
    # Marked, not deleted - the whole library is still on record.
    assert len(rows()) == 4


def test_rows_already_missing_do_not_dilute_the_proportion(library):
    """The denominator is the rows believed PRESENT, not every row on record.

    This is what keeps the proportional test sensitive as a mount degrades. Ten
    of the twenty still-present scores going is refused; ten out of the
    twenty-four on record would be forty-two per cent and would have gone
    through. A library that lost some of itself last month must not thereby buy
    permission to lose the rest this month.
    """
    for n in range(24):
        put(library, f"Classical/Study {n:02d}.gp", f"score {n}".encode())
    scanner._scan()
    # Four already known missing, from some earlier pass. Under the line.
    for n in range(4):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    scanner._scan()
    assert scanner.scan_status()["refused"] is False
    assert scanner.scan_status()["missing"] == 4

    for n in range(4, 14):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    scanner._scan()

    status = scanner.scan_status()
    assert status["refused"] is True
    assert "10 of the 20 score(s)" in status["refused_reason"]


def test_a_library_that_drains_slowly_below_the_floor_is_marked_and_not_refused(
    library,
):
    """An honest statement of what the floor costs, so it is a choice and not a
    surprise.

    A library small enough to sit under the floor can lose all of itself a
    little at a time without the proportional test ever firing - only the
    categorical zero-files test stands at the end of that road. That is
    accepted, because every step of it is a MARK: nothing is deleted, the
    practice, tags and transcriptions stay attached, and one good scan puts it
    all back. The floor is there because a proportion of a handful of rows is
    noise, and refusing on noise is its own kind of broken.
    """
    for n in range(6):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    for n in range(6):
        attach_irreplaceable_work(rows()[f"Classical/Study {n}.gp"]["id"], tag=f"t{n}")
    work = irreplaceable_counts()

    for n in range(5):
        (library / "Classical" / f"Study {n}.gp").unlink()
        scanner._scan()
        assert scanner.scan_status()["refused"] is False

    # Five of six marked, none deleted, and not one piece of unrepeatable work
    # lost along the way.
    assert len(rows()) == 6
    assert sum(1 for r in rows().values() if r["missing_since"]) == 5
    assert irreplaceable_counts() == work

    # And the last one cannot go quietly: zero files with scores on record is
    # categorical, floor or no floor.
    (library / "Classical" / "Study 5.gp").unlink()
    scanner._scan()
    assert scanner.scan_status()["refused"] is True
    assert sum(1 for r in rows().values() if r["missing_since"]) == 5


# ---------------------------------------------------------------------------
# The two triggers no threshold can catch, because the file is right there.
# ---------------------------------------------------------------------------


def test_a_file_edited_and_moved_in_the_same_pass_loses_nothing(library):
    """Editing a file and reorganising it in the same window is ordinary.

    The content hash changed, so the rename relink has no candidate to match,
    and the file's new path is a stranger. No count of files can detect this -
    the file IS there. Under the old code the old row was deleted, and its
    practice, tag and hand-corrected transcription went with it.
    """
    for n in range(4):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    edited_id = rows()["Classical/Study 0.gp"]["id"]
    attach_irreplaceable_work(edited_id)
    work = irreplaceable_counts()

    (library / "Classical" / "Study 0.gp").unlink()
    put(library, "Baroque/Study Zero.gp", b"score 0 with a correction")
    scanner._scan()

    assert scanner.scan_status()["refused"] is False
    after = rows()
    # Fermata does not claim to know these are the same piece - and says so by
    # keeping both rows rather than by destroying one of them.
    assert after["Classical/Study 0.gp"]["id"] == edited_id
    assert after["Classical/Study 0.gp"]["missing_since"] is not None
    assert after["Baroque/Study Zero.gp"]["missing_since"] is None
    assert irreplaceable_counts() == work


def test_duplicate_content_that_moves_together_loses_nothing(library):
    """Two copies of the same arrangement, and the folder they share is renamed.

    Now each new path has two off-disk hash candidates, so the relink rightly
    declines to guess which one moved. Under the old code both rows were then
    deleted and re-inserted, taking both sets of tags, practice and hand
    corrections with them. (Renaming only ONE of two copies does not reach
    this: the still-present sibling is filtered out by path, leaving exactly
    one candidate, and the relink works. It takes both moving in the same pass
    - which is what renaming their folder does.)
    """
    same = b"the very same arrangement"
    put(library, "Classical/Prelude.gp", same)
    put(library, "Classical/Prelude copy.gp", same)
    for n in range(2):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    before = rows()
    for path in ("Classical/Prelude.gp", "Classical/Prelude copy.gp"):
        attach_irreplaceable_work(before[path]["id"], tag=f"tag for {path}")
    work = irreplaceable_counts()
    assert work["edited_transcriptions"] == 2

    (library / "Classical").rename(library / "Baroque")
    scanner._scan()

    assert scanner.scan_status()["refused"] is False
    after = rows()
    for path in ("Classical/Prelude.gp", "Classical/Prelude copy.gp"):
        assert after[path]["id"] == before[path]["id"]
        assert after[path]["missing_since"] is not None
    assert irreplaceable_counts() == work


# ---------------------------------------------------------------------------
# Saying so.
# ---------------------------------------------------------------------------


def test_a_scan_that_marks_scores_missing_says_so_at_warning_level(library, caplog):
    """The other half of #95: the count existed and nothing ever raised it.

    A scan runs at every startup with nobody watching, so the log is the only
    record that survives the container going away. It has to name how many, out
    of how many, where, and enough paths to recognise which part of the library
    it was.
    """
    for n in range(12):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()

    for n in range(5):
        (library / "Classical" / f"Study {n}.gp").unlink()
    with caplog.at_level(logging.WARNING, logger="fermata.scanner"):
        scanner._scan()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "marked 5 of 12 score(s) missing" in message
    assert "Nothing has been deleted" in message
    assert "Classical/Study 0.gp" in message


def test_a_quiet_scan_does_not_cry_wolf(library, caplog):
    """A scan that took nothing away must not log a warning.

    Without this, the warning above is worthless: an alarm that fires on every
    startup is one nobody reads.
    """
    for n in range(4):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    with caplog.at_level(logging.WARNING, logger="fermata.scanner"):
        scanner._scan()
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_a_refused_scan_is_an_error_in_the_log_as_well_as_a_flag(library, caplog):
    for n in range(4):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    for n in range(4):
        (library / "Classical" / f"Study {n}.gp").unlink()
    with caplog.at_level(logging.ERROR, logger="fermata.scanner"):
        scanner._scan()

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "did not reconcile the library" in errors[0].getMessage()


def test_the_scan_status_endpoint_carries_the_refusal(client, library):
    """Whatever the interface does with it, the reason has to reach the client."""
    put(library, "Classical/Study in C.gp")
    scanner._scan()
    (library / "Classical" / "Study in C.gp").unlink()
    scanner._scan()

    body = client.get("/api/scan/status").json()
    assert body["refused"] is True
    assert "no readable score files at all" in body["refused_reason"]


# ---------------------------------------------------------------------------
# What the rest of the application makes of a missing score.
# ---------------------------------------------------------------------------


def test_a_missing_score_still_appears_in_the_library_and_answers_to_its_id(
    client, library
):
    """"Your library is intact, these files are not reachable" is the true thing.

    Hiding the rows would show somebody whose drive has not come back an empty
    library, which is the very picture #95 painted by deleting them. So nothing
    filters on missing_since; the row is listed, flagged, and still carries its
    tags and its practice.
    """
    for n in range(4):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    gone_id = rows()["Classical/Study 0.gp"]["id"]
    attach_irreplaceable_work(gone_id)
    (library / "Classical" / "Study 0.gp").unlink()
    scanner._scan()

    listed = client.get("/api/scores").json()
    assert len(listed) == 4
    entry = next(s for s in listed if s["id"] == gone_id)
    assert entry["missing_since"] is not None
    assert entry["tags"] == ["wedding"]
    assert entry["practice_seconds"] == 2400
    assert entry["has_transcription"] is True

    one = client.get(f"/api/scores/{gone_id}").json()
    assert one["missing_since"] == entry["missing_since"]
    # The file itself is genuinely not there, and that is a 404 on the file -
    # not on the score.
    assert client.get(f"/api/scores/{gone_id}/file").status_code == 404
    assert client.get(f"/api/scores/{gone_id}").status_code == 200


def test_the_library_views_still_answer_with_missing_scores_present(client, library):
    """The #94 trap, checked against the column this change introduces.

    #94 found a query using NOT IN against a column that had become nullable,
    which returns nothing at all once any row holds a NULL - it emptied the
    neglected view with nothing failing. missing_since is a new nullable column
    and no query reads it, so nothing should be affected; that is a claim worth
    checking rather than asserting, because it was exactly the shape of the
    last one.
    """
    for n in range(4):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    practised_id = rows()["Classical/Study 1.gp"]["id"]
    attach_irreplaceable_work(practised_id)
    (library / "Classical" / "Study 0.gp").unlink()
    scanner._scan()

    neglected = client.get("/api/scores?practiced=neglected").json()
    assert {s["path"] for s in neglected} == {
        "Classical/Study 0.gp",
        "Classical/Study 2.gp",
        "Classical/Study 3.gp",
    }
    assert client.get("/api/collections").json() == [
        {"collection": "Classical", "count": 4}
    ]
    assert client.get("/api/tags").json() == [{"name": "wedding", "count": 1}]


# ---------------------------------------------------------------------------
# The library folder is not ours to create.
# ---------------------------------------------------------------------------


def test_a_missing_library_folder_is_refused_rather_than_created(tmp_path, monkeypatch):
    """ensure_dirs used to mkdir this, which is the first link in #95's chain.

    A folder that is not there is a mount that did not appear far more often
    than it is a first run, and creating an empty one manufactures the evidence
    that used to destroy the database.
    """
    absent = tmp_path / "library"
    monkeypatch.setattr(config, "LIBRARY_DIR", absent)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "config" / "cache")

    with pytest.raises(RuntimeError) as raised:
        config.ensure_dirs()

    assert "is not there" in str(raised.value)
    assert str(absent) in str(raised.value)
    assert not absent.exists(), "the folder must not have been created"


def test_a_library_path_that_is_a_file_is_refused_and_says_which_it_is(
    tmp_path, monkeypatch
):
    """A typo'd FERMATA_LIBRARY, or a volume mounted onto the wrong target."""
    not_a_folder = tmp_path / "library"
    not_a_folder.write_text("not a folder")
    monkeypatch.setattr(config, "LIBRARY_DIR", not_a_folder)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "config" / "cache")

    with pytest.raises(RuntimeError) as raised:
        config.ensure_dirs()
    assert "is a file, not a folder" in str(raised.value)


def test_the_config_folder_is_still_ours_to_create(tmp_path, monkeypatch):
    """The asymmetry is the point, so it is asserted rather than implied.

    The config folder is Fermata's own storage: an empty one is a genuine first
    run and there is no data it could be shadowing. The library folder is the
    user's, and Fermata only ever reads it.
    """
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(config, "LIBRARY_DIR", library)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "config" / "cache")

    config.ensure_dirs()

    assert (tmp_path / "config").is_dir()
    assert (tmp_path / "config" / "cache").is_dir()


# ---------------------------------------------------------------------------
# The upgrade path, and the cascades that were reconsidered.
# ---------------------------------------------------------------------------


def _previous_release_database(path, monkeypatch):
    """A database exactly as the released code before this change made it.

    Built by running the real init_db with missing_since removed from
    COLUMN_ADDITIONS rather than by restating a schema by hand: a hand-written
    copy is a second definition that can quietly stop matching, and what has to
    be tested is the upgrade from what the previous release actually produced.
    """
    previous = {
        table: {
            column: definition
            for column, definition in columns.items()
            if column != "missing_since"
        }
        for table, columns in db.COLUMN_ADDITIONS.items()
    }
    monkeypatch.setattr(db, "COLUMN_ADDITIONS", previous)
    monkeypatch.setattr(db, "DB_PATH", path)
    db._local.conn = None
    db.init_db()
    conn = db.connect()
    assert "missing_since" not in {
        r["name"] for r in conn.execute("PRAGMA table_info(scores)")
    }
    return conn


def test_an_existing_database_with_real_rows_gains_the_column_and_keeps_everything(
    tmp_path, monkeypatch
):
    """Practice history is irreplaceable, so the upgrade carrying it is tested.

    Real rows in every table that hangs off a score, written by the previous
    release's schema, then brought forward by the current init_db.
    """
    path = tmp_path / "existing.db"
    conn = _previous_release_database(path, monkeypatch)
    conn.execute(
        """INSERT INTO scores(id, title, path, file_type, hash, size, mtime, favorite, last_page)
           VALUES (1, 'Study in C', 'Classical/Study in C.pdf', 'pdf', 'abc', 11, 1.5, 1, 4)"""
    )
    conn.execute(
        """INSERT INTO scores(id, title, path, file_type, hash, size, mtime)
           VALUES (2, 'Prelude', 'Classical/Prelude.pdf', 'pdf', 'def', 22, 2.5)"""
    )
    conn.execute(
        """INSERT INTO practice_sessions(id, score_id, activity, started_at, local_date, seconds, note)
           VALUES (7, 1, 'piece', '2026-08-01T19:00:00', '2026-08-01', 2400, 'felt rough')"""
    )
    conn.execute(
        """INSERT INTO practice_goals(id, period_start, period_end, target_days, scope, score_id, intent)
           VALUES (3, '2026-07-27', '2026-08-02', 5, 'score', 1, 'get it to tempo')"""
    )
    conn.execute("INSERT INTO tags(id, name) VALUES (5, 'wedding')")
    conn.execute("INSERT INTO score_tags(score_id, tag_id) VALUES (1, 5)")
    conn.execute(
        """INSERT INTO transcriptions(id, score_id, format, content, source)
           VALUES (9, 1, 'alphatex', 'three hours of hand correction', 'edited')"""
    )
    conn.commit()
    db._local.conn = None

    # The current release opens the same file.
    monkeypatch.undo()
    monkeypatch.setattr(db, "DB_PATH", path)
    db._local.conn = None
    db.init_db()
    conn = db.connect()

    scores = {r["path"]: dict(r) for r in conn.execute("SELECT * FROM scores")}
    assert set(scores) == {"Classical/Study in C.pdf", "Classical/Prelude.pdf"}
    study = scores["Classical/Study in C.pdf"]
    # The column arrived, and NULL is the right value for a row written before
    # it existed: those rows were all written by a scanner that would have
    # deleted them rather than mark them, so every one of them was present.
    assert study["missing_since"] is None
    # Nothing else moved.
    assert (study["id"], study["hash"], study["size"], study["mtime"]) == (1, "abc", 11, 1.5)
    assert (study["favorite"], study["last_page"]) == (1, 4)
    session = conn.execute("SELECT * FROM practice_sessions WHERE id = 7").fetchone()
    assert (session["score_id"], session["seconds"], session["note"]) == (
        1,
        2400,
        "felt rough",
    )
    assert conn.execute("SELECT * FROM practice_goals WHERE id = 3").fetchone()["score_id"] == 1
    assert conn.execute("SELECT COUNT(*) FROM score_tags").fetchone()[0] == 1
    transcription = conn.execute("SELECT * FROM transcriptions WHERE id = 9").fetchone()
    assert transcription["content"] == "three hours of hand correction"
    db._local.conn = None


def test_an_upgraded_database_ends_up_with_the_same_scores_table_as_a_fresh_one(
    tmp_path, monkeypatch
):
    """The #94 lesson, applied to this change: two ways to get here, one shape.

    An upgraded install and a fresh one must not end up with subtly different
    tables, because every later change is then written against whichever one
    the author happens to have.
    """

    def shape(conn):
        return [
            (r["name"], r["type"].upper(), r["notnull"], r["dflt_value"], r["pk"])
            for r in conn.execute("PRAGMA table_info(scores)")
        ]

    upgraded_path = tmp_path / "upgraded.db"
    _previous_release_database(upgraded_path, monkeypatch)
    db._local.conn = None
    monkeypatch.undo()
    monkeypatch.setattr(db, "DB_PATH", upgraded_path)
    db._local.conn = None
    db.init_db()
    upgraded = shape(db.connect())

    db._local.conn = None
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fresh.db")
    db._local.conn = None
    db.init_db()
    fresh = shape(db.connect())
    db._local.conn = None

    assert upgraded == fresh
    assert ("missing_since", "TEXT", 0, None, 0) in fresh


def test_the_cascades_are_what_this_change_decided_they_should_be(app_env):
    """#95 asked whether tags and transcriptions should stop being owned by the
    score row, the way #94 did for practice. The answer came out the other way,
    and a decision that reads as an oversight in a year is worth pinning down.

    The test is whether a row still SAYS anything once its score is gone. A
    practice session says "forty minutes on Tuesday at 92bpm"; a goal says
    "practise five days this week". A score_tags row says only "(this score)
    (this tag)", and a transcriptions row says "here is the music of (this
    score)" - neither has a statement left without it, and both would
    accumulate unreachable rows for ever, because SQLite treats NULLs as
    distinct in a unique index. What protects that work instead is that a scan
    no longer deletes the row they hang from; see db.py's schema comments.
    """
    conn = db.connect()
    actions = {}
    for table in ("practice_sessions", "practice_goals", "score_tags", "transcriptions"):
        for row in conn.execute(f"PRAGMA foreign_key_list({table})"):
            if row["table"] == "scores":
                actions[table] = row["on_delete"]
    assert actions == {
        "practice_sessions": "SET NULL",
        "practice_goals": "SET NULL",
        "score_tags": "CASCADE",
        "transcriptions": "CASCADE",
    }


def test_a_deliberate_deletion_is_the_only_thing_left_that_reaches_the_cascade(app_env):
    """Explicit score deletion does not exist yet (#56), so this is what it will
    do when it arrives - stated now, while the reasoning is in front of us.

    Fermata itself no longer has any code path that deletes a score row (see
    test_nothing_in_the_scanner_deletes_a_score_row and the absence of a DELETE
    /scores endpoint), so this exercises the constraint directly.
    """
    conn = db.connect()
    conn.execute(
        """INSERT INTO scores(id, title, path, file_type, hash, size, mtime)
           VALUES (1, 'Study in C', 'Classical/Study in C.pdf', 'pdf', 'abc', 1, 0.0)"""
    )
    conn.execute(
        """INSERT INTO practice_sessions(id, score_id, activity, started_at, seconds)
           VALUES (7, 1, 'piece', '2026-08-01T19:00:00', 2400)"""
    )
    conn.execute("INSERT INTO tags(id, name) VALUES (5, 'wedding')")
    conn.execute("INSERT INTO score_tags(score_id, tag_id) VALUES (1, 5)")
    conn.execute(
        """INSERT INTO transcriptions(id, score_id, content, source)
           VALUES (9, 1, 'hand corrected', 'edited')"""
    )
    conn.commit()

    conn.execute("DELETE FROM scores WHERE id = 1")
    conn.commit()

    # The practice is kept, and stops naming a piece.
    session = conn.execute("SELECT * FROM practice_sessions WHERE id = 7").fetchone()
    assert session is not None
    assert session["score_id"] is None
    assert session["seconds"] == 2400
    # The association and the notation go, because neither says anything alone.
    assert conn.execute("SELECT COUNT(*) FROM score_tags").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM transcriptions").fetchone()[0] == 0


def test_the_missing_column_is_not_a_foreign_key_and_needs_no_migration(app_env):
    """Why this change is an ADD COLUMN and not a MIGRATIONS step.

    db.py's COLUMN_ADDITIONS comment is explicit that it expresses ADD COLUMN
    and nothing else - nullable, no foreign key, no backfill - and that a change
    that CAN go there should, because it re-runs on every startup and repairs a
    half-upgraded database. This asserts missing_since really is that kind of
    change, and that SCHEMA_VERSION was therefore left where it was.
    """
    assert db.COLUMN_ADDITIONS["scores"]["missing_since"] == "TEXT"
    assert db.SCHEMA_VERSION == max(db.MIGRATIONS)
    conn = db.connect()
    keyed = {r["from"] for r in conn.execute("PRAGMA foreign_key_list(scores)")}
    assert "missing_since" not in keyed
    column = next(
        r for r in conn.execute("PRAGMA table_info(scores)") if r["name"] == "missing_since"
    )
    assert (column["notnull"], column["dflt_value"]) == (0, None)


def test_the_scan_survives_a_file_that_cannot_be_read_and_still_reconciles(library):
    """An unreadable file must not take the reconciliation down with it.

    The per-file OSError handler exists so one locked or vanishing file does
    not abandon the rest of the pass. Worth a test now that what follows the
    loop marks rows rather than deleting them: a scan that bailed out early
    would leave the marks half-applied.
    """
    for n in range(4):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    (library / "Classical" / "Study 0.gp").unlink()

    real_stat = scanner._hash_file

    def explode(path):
        if path.name == "Study 1.gp":
            raise OSError("locked by something else")
        return real_stat(path)

    import unittest.mock

    with unittest.mock.patch.object(scanner, "_hash_file", explode):
        # Force every file down the hashing path by clearing the cached stats.
        db.connect().execute("UPDATE scores SET size = -1")
        db.connect().commit()
        scanner._scan()

    status = scanner.scan_status()
    assert status["errors"] == 1
    assert "locked by something else" in status["last_error"]
    # The reconciliation still ran, and the file that really went is marked.
    assert status["missing"] == 1
    assert rows()["Classical/Study 0.gp"]["missing_since"] is not None
    # And the file that merely failed to be read is NOT marked - it was seen.
    assert rows()["Classical/Study 1.gp"]["missing_since"] is None


def test_a_score_row_written_before_the_column_existed_is_treated_as_present(
    library,
):
    """A row whose missing_since is NULL must behave as present, not as unknown.

    An upgraded database is full of these, and the first scan after an upgrade
    is the one that decides what happens to them.
    """
    conn = db.connect()
    conn.execute(
        """INSERT INTO scores(title, path, file_type, hash, size, mtime)
           VALUES ('Study in C', 'Classical/Study in C.gp', 'gp', 'abc', 1, 0.0)"""
    )
    conn.commit()
    put(library, "Classical/Study in C.gp")
    scanner._scan()

    row = rows()["Classical/Study in C.gp"]
    assert row["missing_since"] is None
    # Its stats were stale, so it was updated rather than added or restored.
    assert scanner.scan_status()["updated"] == 1
    assert scanner.scan_status()["restored"] == 0


def test_sqlite_treats_nulls_as_distinct_in_the_unique_indexes_the_cascade_decision_rests_on(
    app_env,
):
    """The load-bearing SQLite behaviour behind keeping those two cascades.

    The argument against nulling score_id on score_tags and transcriptions is
    partly that the unique indexes would stop constraining the orphans, letting
    them pile up unreachably. That is a claim about SQLite, so it is checked
    against SQLite rather than remembered.
    """
    conn = db.connect()
    conn.execute("CREATE TABLE probe (a INTEGER, b INTEGER, UNIQUE (a, b))")
    conn.execute("INSERT INTO probe VALUES (NULL, 1)")
    conn.execute("INSERT INTO probe VALUES (NULL, 1)")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO probe VALUES (1, 1)")
        conn.execute("INSERT INTO probe VALUES (1, 1)")
    conn.rollback()
