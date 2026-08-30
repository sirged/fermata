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
import time

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


def _wait_for_scan(timeout: float = 10.0) -> None:
    """Wait out a scan started through the HTTP layer.

    start_scan runs on a background thread. Most tests here call scanner._scan
    directly and need none of this; the ones that go through an endpoint do.
    """
    deadline = time.monotonic() + timeout
    while scanner.scan_status()["scanning"]:
        if time.monotonic() > deadline:
            raise AssertionError("the scan did not finish")
        time.sleep(0.02)


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
    """The relink path has to clear the mark too - but must not claim a restore.

    A drive that comes back with things reorganised reaches its rows through
    the content-hash relink rather than by path, and a row whose file this scan
    is looking at is not missing whichever way it was found.

    It is NOT counted in `restored`, and that distinction is the point of the
    second half of this test. `restored` is presented as evidence that a remount
    really did recover, and only a file reappearing at the path it left from
    supports that. This branch matches on content alone: a row marked missing
    stays a relink candidate indefinitely, so any later file with the same bytes
    lands here and inherits that score's history. That is usually the right
    answer, and it is a guess about identity either way - it must not be
    reported as proof a drive came back.
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
    assert scanner.scan_status()["restored"] == 0, "a content-hash relink is not a remount"
    assert scanner.scan_status()["updated"] == 1
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
    assert "account for 5 of the 12 score(s)" in status["refused_reason"]
    assert "7 more would be marked missing" in status["refused_reason"]
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
    assert "account for 10 of the 24 score(s)" in status["refused_reason"]


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


def test_a_mount_that_lands_on_the_wrong_folder_is_refused(library):
    """The case the guard exists for, and the one it could not originally see.

    A mount that resolves to a DIFFERENT directory - the host path renamed and
    something else now at the old one, a volume pointed at the wrong target - is
    not an empty library. It is full of files, just not these files. Every
    stored path is absent and every file on disk is new.

    This was invisible while the guard read its numbers after the file loop: the
    thirteen new rows counted towards "scores on record", inflating the
    denominator while contributing nothing to the numerator, so twelve missing
    out of twenty-five sat just under half and the guard said nothing at all.
    The decision is now taken before a single row is touched.
    """
    for n in range(12):
        put(library, f"Classical/Study {n:02d}.gp", f"score {n}".encode())
    scanner._scan()
    before = rows()
    assert len(before) == 12

    for path in library.rglob("*.gp"):
        path.unlink()
    for n in range(13):
        put(library, f"Someone Elses Music/Thing {n:02d}.gp", f"unrelated {n}".encode())
    scanner._scan()

    status = scanner.scan_status()
    assert status["refused"] is True
    assert "account for 0 of the 12 score(s)" in status["refused_reason"]
    # A refusal is a no-op, and that includes the files it DID see.
    assert rows() == before
    assert (status["added"], status["updated"], status["missing"]) == (0, 0, 0)


def test_a_refused_scan_adds_nothing_even_though_it_saw_new_files(library):
    """A refusal must be the no-op its own message claims.

    The message says "nothing has been changed", and it used to be false: the
    file loop inserted, updated and relinked with a commit per file, and only
    then was the walk judged. So `refused: true` arrived beside `added: 1` in
    the same dictionary. The composite case was worse - a library re-exported
    and reorganised in one pass gave twenty-four rows for twelve pieces,
    permanently doubled.
    """
    for n in range(12):
        put(library, f"Classical/Study {n:02d}.gp", f"score {n}".encode())
    scanner._scan()
    before = rows()

    # Six of twelve go (over the line), and two genuinely new files arrive at
    # the same time.
    for n in range(6):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    put(library, "Classical/Brand New.gp", b"never seen before")
    put(library, "Classical/Also New.gp", b"nor this")
    scanner._scan()

    status = scanner.scan_status()
    assert status["refused"] is True
    assert (status["added"], status["updated"], status["missing"]) == (0, 0, 0)
    assert rows() == before, "a refused scan wrote something"


def test_a_whole_library_re_export_is_refused_rather_than_doubled(library):
    """Every file edited AND moved in one pass - "I re-exported everything".

    Under the old order this produced twelve new rows beside twelve marked ones,
    reported as a refusal, and by the fixed-point defect it then refused for
    ever afterwards. Now it is caught before anything is written, and the person
    is asked.
    """
    for n in range(12):
        put(library, f"Classical/Study {n:02d}.gp", f"score {n}".encode())
    scanner._scan()
    before = rows()

    for path in sorted(library.rglob("*.gp")):
        path.unlink()
    for n in range(12):
        put(library, f"Exports/Study {n:02d}.gp", f"score {n} re-engraved".encode())
    scanner._scan()

    assert scanner.scan_status()["refused"] is True
    assert rows() == before
    assert len(rows()) == 12, "no doubling"


# ---------------------------------------------------------------------------
# A refusal has to have a way out, or it is worse than what it prevents.
# ---------------------------------------------------------------------------


def test_a_refusal_can_be_acknowledged_and_then_stops_refusing(library):
    """The fixed point, which was the worst part of the guard as first written.

    `unmatched` and `believed_present` recompute identically on every pass, so a
    refusal was permanent: somebody who deliberately archived most of their
    library was refused for ever, an error logged on every startup, the rows
    sitting in the library failing to open, and - with no way to delete a score -
    no way out at all. The only escape was to add roughly twice as many new
    files as had gone.
    """
    for n in range(20):
        put(library, f"Classical/Study {n:02d}.gp", f"score {n}".encode())
    scanner._scan()
    for n in range(20):
        attach_irreplaceable_work(rows()[f"Classical/Study {n:02d}.gp"]["id"], tag=f"t{n}")
    work = irreplaceable_counts()

    # A deliberate archive: fifteen of twenty put away.
    for n in range(15):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    scanner._scan()
    refused = scanner.scan_status()
    assert refused["refused"] is True
    assert refused["acknowledge_token"]
    assert refused["unmatched_count"] == 15
    assert len(refused["unmatched_paths"]) == 15

    # It stays refused however many times it is asked - which is the defect, and
    # the reason an acknowledgement has to exist.
    scanner._scan()
    assert scanner.scan_status()["refused"] is True

    scanner._scan(acknowledge=refused["acknowledge_token"])
    acknowledged = scanner.scan_status()
    assert acknowledged["refused"] is False
    assert acknowledged["missing"] == 15
    assert len(rows()) == 20, "acknowledging marks; it never deletes"
    assert irreplaceable_counts() == work

    # And it does not start refusing all over again on the next ordinary pass.
    scanner._scan()
    assert scanner.scan_status()["refused"] is False
    assert scanner.scan_status()["missing"] == 0


def test_an_acknowledgement_for_different_evidence_does_not_apply(library):
    """Consent is about something specific, or it is not consent.

    The token names the exact set of unmatched paths. If the library changed
    again between the message being read and the button being pressed, the old
    token does not describe what is there now, and the safe way for stale
    consent to fail is not to apply.
    """
    for n in range(20):
        put(library, f"Classical/Study {n:02d}.gp", f"score {n}".encode())
    scanner._scan()
    for n in range(11):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    scanner._scan()
    stale = scanner.scan_status()["acknowledge_token"]
    assert stale

    # Two more disappear before anybody presses anything.
    for n in range(11, 13):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    before = rows()
    scanner._scan(acknowledge=stale)

    status = scanner.scan_status()
    assert status["refused"] is True
    assert status["acknowledge_token"] != stale
    assert rows() == before
    # The new token does work, because it describes what is actually there.
    scanner._scan(acknowledge=status["acknowledge_token"])
    assert scanner.scan_status()["refused"] is False
    assert scanner.scan_status()["missing"] == 13


def test_a_token_is_the_same_across_restarts_for_the_same_evidence(library):
    """A refusal is usually produced by a startup scan, and will be produced
    again by the next one. A token that changed each time could never be acted
    on: the interface would be offering consent for something already stale."""
    for n in range(20):
        put(library, f"Classical/Study {n:02d}.gp", f"score {n}".encode())
    scanner._scan()
    for n in range(15):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    scanner._scan()
    first = scanner.scan_status()["acknowledge_token"]
    scanner._scan()
    assert scanner.scan_status()["acknowledge_token"] == first


def test_the_endpoints_carry_a_refusal_and_take_the_acknowledgement(client, library):
    for n in range(20):
        put(library, f"Classical/Study {n:02d}.gp", f"score {n}".encode())
    scanner._scan()
    for n in range(15):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    scanner._scan()

    status = client.get("/api/scan/status").json()
    assert status["refused"] is True
    assert status["unmatched_count"] == 15
    assert "confirm this message" in status["refused_reason"]

    # A token for other evidence is refused rather than applied.
    wrong = client.post("/api/scan/acknowledge", json={"token": "0" * 40})
    assert wrong.status_code == 409
    assert "different set of missing files" in wrong.json()["detail"]

    accepted = client.post(
        "/api/scan/acknowledge", json={"token": status["acknowledge_token"]}
    )
    assert accepted.status_code == 200
    _wait_for_scan()
    assert scanner.scan_status()["refused"] is False
    assert scanner.scan_status()["missing"] == 15


def test_acknowledging_when_nothing_was_refused_is_a_conflict(client, library):
    put(library, "Classical/Study in C.gp")
    scanner._scan()
    assert scanner.scan_status()["refused"] is False
    res = client.post("/api/scan/acknowledge", json={"token": "0" * 40})
    assert res.status_code == 409
    assert "no refused scan" in res.json()["detail"]


# ---------------------------------------------------------------------------
# The ladder: many small permitted losses adding up to the whole library.
# ---------------------------------------------------------------------------


def test_losing_the_library_a_half_at_a_time_is_refused_on_the_second_pass(library):
    """The single-pass test can be walked past a step at a time.

    Each permitted pass shrinks the rows believed present, which shrinks the
    next pass's denominator - so 24 scores can go 12, 6, 3, 1 and never trip a
    test measured against the remainder. A flapping mount exposing a shrinking
    subset walks that ladder unaided. Measured against the high-water mark
    instead, the second rung is refused, because cumulatively more than half of
    the library is gone however it got that way.
    """
    for n in range(24):
        put(library, f"Classical/Study {n:02d}.gp", f"score {n}".encode())
    scanner._scan()

    # Rung one: eleven of twenty-four, just under half. Permitted.
    for n in range(11):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    scanner._scan()
    assert scanner.scan_status()["refused"] is False
    assert scanner.scan_status()["missing"] == 11

    # Rung two: six of the thirteen that remain - under half of the REMAINDER,
    # and the old guard permitted exactly this.
    for n in range(11, 17):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    scanner._scan()

    status = scanner.scan_status()
    assert status["refused"] is True
    assert "account for 7 of the 24 score(s)" in status["refused_reason"]
    assert status["missing"] == 0
    assert sum(1 for r in rows().values() if r["missing_since"]) == 11


def test_acknowledging_a_smaller_library_stops_it_being_asked_about_again(library):
    """The high-water mark comes down when somebody says the library really is
    smaller now. Without that, an acknowledged pruning would trip the cumulative
    test on every subsequent scan for ever, against a library that no longer
    exists."""
    for n in range(24):
        put(library, f"Classical/Study {n:02d}.gp", f"score {n}".encode())
    scanner._scan()
    for n in range(18):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    scanner._scan()
    token = scanner.scan_status()["acknowledge_token"]
    scanner._scan(acknowledge=token)
    assert scanner.scan_status()["missing"] == 18

    # Now a further, ordinary loss: one of the six that are left. It must not be
    # judged against the library as it was before the pruning was accepted.
    (library / "Classical" / "Study 18.gp").unlink()
    scanner._scan()
    assert scanner.scan_status()["refused"] is False
    assert scanner.scan_status()["missing"] == 1


def test_an_acknowledged_pruning_is_not_asked_about_again_at_any_size(library):
    """The fixed point, in the one band where the floor does not hide it.

    A first attempt at the cumulative test counted everything ABSENT rather than
    what remained, and that quietly put the permanent-refusal defect straight
    back: archive most of a large library, confirm it, and those rows stay absent
    for ever - so the test fires on every later scan, against a library that no
    longer exists. It went unnoticed because the only test covering
    acknowledgement used a library below the floor, where the proportional test
    is switched off entirely and any formula passes.
    """
    for n in range(40):
        put(library, f"Classical/Study {n:02d}.gp", f"score {n}".encode())
    scanner._scan()
    for n in range(30):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    scanner._scan()
    token = scanner.scan_status()["acknowledge_token"]
    assert token
    scanner._scan(acknowledge=token)
    assert scanner.scan_status()["missing"] == 30

    # Ten scores left, thirty rows absent for ever, and a high-water mark that
    # is still well above the floor. Three ordinary further losses in a row must
    # each be accepted rather than met with a refusal about a library that was
    # already dealt with.
    for n in range(30, 33):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
        scanner._scan()
        status = scanner.scan_status()
        assert status["refused"] is False, status["refused_reason"]
        assert status["missing"] == 1


def test_a_library_that_is_already_mostly_marked_is_not_refused_every_scan(library):
    """A guard has to be quiet when it has nothing to say.

    Once the marks are made - whether they were under the line or confirmed - a
    later scan that would change nothing must not keep raising the alarm about
    them. An error logged on every startup for ever is how somebody learns to
    stop reading the log.
    """
    for n in range(20):
        put(library, f"Classical/Study {n:02d}.gp", f"score {n}".encode())
    scanner._scan()
    for n in range(15):
        (library / "Classical" / f"Study {n:02d}.gp").unlink()
    scanner._scan()
    scanner._scan(acknowledge=scanner.scan_status()["acknowledge_token"])
    assert sum(1 for r in rows().values() if r["missing_since"]) == 15

    for _ in range(3):
        scanner._scan()
        status = scanner.scan_status()
        assert status["refused"] is False, status["refused_reason"]
        assert status["missing"] == 0
        assert status["refused_reason"] is None


def test_the_guard_is_quiet_whenever_a_pass_would_change_nothing():
    """_implausible's contract, checked directly rather than through a scan.

    This one branch cannot be reached by driving the scanner, and that is worth
    saying out loud rather than leaving as a gap. The proportional test refuses
    any pass that would take the library below half of its high-water mark, and
    an acknowledgement moves the mark down to the new size - so a database can
    never actually arrive in the state "well below the mark, with nothing to
    change". The guard is kept anyway, because it is the difference between that
    being an invariant somebody has to preserve and it not mattering: if the
    reset were ever removed or changed, this is what stops every scan for the
    rest of the library's life from being an error in the log.

    Tested at the function boundary because that is the only place the state is
    constructible. A system-level test could not fail here, and a test that
    cannot fail is worse than no test.
    """
    # Nothing to mark: quiet, whatever the proportions look like.
    assert scanner._implausible(found=5, believed_present=5, unmatched=0, high_water=200) is None
    assert scanner._implausible(found=0, believed_present=0, unmatched=0, high_water=200) is None
    # One thing to mark, and the library still mostly there: also quiet.
    assert scanner._implausible(found=99, believed_present=100, unmatched=1, high_water=100) is None
    # The same figures with something to mark DO refuse - which is what makes
    # the two assertions above about `unmatched` and not about anything else.
    assert scanner._implausible(found=5, believed_present=5, unmatched=1, high_water=200) is not None


def test_the_guard_below_its_floor_only_refuses_an_empty_library():
    """The floor's exact effect, stated at the boundary.

    A small library is exempt from the proportion entirely - losing three of
    four scores is unremarkable - but never from the categorical test.
    """
    assert scanner._implausible(found=1, believed_present=4, unmatched=3, high_water=4) is None
    assert scanner._implausible(found=0, believed_present=4, unmatched=4, high_water=4) is not None
    # And one above the floor, for contrast: the same proportion now refuses.
    assert scanner._implausible(found=3, believed_present=12, unmatched=9, high_water=12) is not None


def test_the_high_water_mark_is_not_a_user_setting(client, library):
    """It lives in the settings table because it has to outlive the process - the
    ladder is walked by a sequence of STARTUPS - but it is Fermata's bookkeeping
    and not a preference, so it must be neither readable nor writable as one."""
    for n in range(4):
        put(library, f"Classical/Study {n}.gp", f"score {n}".encode())
    scanner._scan()
    stored = db.connect().execute(
        "SELECT value FROM settings WHERE key = ?", (scanner.HIGH_WATER_KEY,)
    ).fetchone()
    assert stored["value"] == "4"

    assert scanner.HIGH_WATER_KEY not in client.get("/api/settings").json()
    rejected = client.put("/api/settings", json={scanner.HIGH_WATER_KEY: "1"})
    assert rejected.status_code == 422


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

    # A missing score is NOT offered as the next thing to practise. It cannot be
    # opened, so it can never be practised, so it would sit at the top of this
    # list for ever - the one view whose entire job is to suggest what to play
    # next, headed permanently by the one row that cannot be played.
    neglected = client.get("/api/scores?practiced=neglected").json()
    assert {s["path"] for s in neglected} == {
        "Classical/Study 2.gp",
        "Classical/Study 3.gp",
    }
    # But it is still in the library, and still on record as practised - the
    # exclusion above is one view's answer to one question, not a soft delete.
    assert "Classical/Study 0.gp" in {s["path"] for s in client.get("/api/scores").json()}
    # The sidebar tells the truth about how much of the collection is there,
    # instead of counting a folder that has partly gone as though it were whole.
    assert client.get("/api/collections").json() == [
        {"collection": "Classical", "count": 3, "missing": 1}
    ]
    assert client.get("/api/tags").json() == [{"name": "wedding", "count": 1}]


def test_the_duplicates_view_counts_files_and_not_marks(client, library):
    """Marking rather than deleting made this view actively wrong for a while.

    Two identical files in a folder, the folder renamed: the relink declines on
    two candidates, so two rows are marked missing and two new ones inserted.
    This view then reported four copies of a hash that two files on disk share,
    half of them failing to open when clicked. It asks a question about FILES -
    "am I storing this arrangement twice, can I delete one" - so a row with no
    file behind it is not a copy of anything.
    """
    same = b"the very same arrangement"
    put(library, "Classical/Prelude.gp", same)
    put(library, "Classical/Prelude copy.gp", same)
    put(library, "Classical/Study.gp", b"something else")
    scanner._scan()
    assert [g["count"] for g in client.get("/api/duplicates").json()] == [2]

    (library / "Classical").rename(library / "Baroque")
    scanner._scan()

    groups = client.get("/api/duplicates").json()
    assert [g["count"] for g in groups] == [2], "a marked row is not a copy of anything"
    assert {s["path"] for s in groups[0]["scores"]} == {
        "Baroque/Prelude.gp",
        "Baroque/Prelude copy.gp",
    }
    # The four rows are all still there - this view's answer is a view, not a
    # deletion.
    assert len(rows()) == 5


def test_a_ghost_collection_is_shown_as_empty_rather_than_as_full(client, library):
    """A collection that does not exist used to stand in the sidebar with a full
    count beside it - unremovable, and with no reason for a person to distrust
    it. It is still listed, because those rows hold practice and tags and a name
    quietly vanishing is its own kind of alarm, but it says nothing is there.

    It takes DUPLICATE content to produce one, which is worth having in the test
    rather than left implicit: a folder of uniquely-named music that gets renamed
    relinks row for row and leaves no ghost at all. Only the copies the relink
    declines to guess about stay behind under the old name.
    """
    same = b"the very same arrangement"
    put(library, "Classical/Prelude.gp", same)
    put(library, "Classical/Prelude copy.gp", same)
    put(library, "Classical/Study.gp", b"something else")
    scanner._scan()
    assert client.get("/api/collections").json() == [
        {"collection": "Classical", "count": 3, "missing": 0}
    ]

    (library / "Classical").rename(library / "Baroque")
    scanner._scan()

    assert client.get("/api/collections").json() == [
        # Study relinked and moved with the folder; the two copies could not be
        # told apart, so they stayed behind as marks.
        {"collection": "Baroque", "count": 3, "missing": 0},
        {"collection": "Classical", "count": 0, "missing": 2},
    ]


def test_a_pass_that_both_marks_and_adds_says_the_history_is_on_the_other_row(
    library, caplog
):
    """Not an error, but not a clean pass either, and it used to read as one.

    Marks and inserts together means files moved in a way the content-hash
    relink could not match - a changed mount prefix over duplicated content, or
    an edit and a move at once. Nothing is lost, but the library now holds two
    rows for one piece and the practice is on the one nobody will open.
    """
    same = b"the very same arrangement"
    put(library, "Classical/Prelude.gp", same)
    put(library, "Classical/Prelude copy.gp", same)
    put(library, "Classical/Study.gp", b"something else")
    scanner._scan()

    (library / "Classical").rename(library / "Baroque")
    with caplog.at_level(logging.WARNING, logger="fermata.scanner"):
        scanner._scan()

    status = scanner.scan_status()
    assert status["missing"] == 2 and status["added"] == 2
    assert status["unmatched_moves"] == 2
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "could not match to their existing scores" in messages
    assert "not on the new one" in messages


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


def test_an_upload_will_not_recreate_the_library_folder_either(client, library, monkeypatch):
    """One upload used to undo the whole startup refusal.

    dest_dir.mkdir(parents=True) creates the ROOT on its way to the subfolder,
    so a single upload turned a loud, harmless, self-correcting refusal into a
    silent start against an almost-empty library - and the next scan then judged
    that library. In a container it is worse than pointless: the write lands in
    the image layer at the mountpoint, invisible from the host and gone on the
    next start.
    """
    gone = library.parent / "unmounted"
    monkeypatch.setattr(api, "LIBRARY_DIR", gone)

    res = client.post(
        "/api/upload?folder=Uploads",
        files={"file": ("thing.gp", b"a score", "application/octet-stream")},
    )

    assert res.status_code == 503
    assert "is not there" in res.json()["detail"]
    assert not gone.exists(), "the upload created the library folder"


def test_the_config_folder_is_still_ours_to_create(tmp_path, monkeypatch):
    """The asymmetry is the point, so it is asserted rather than implied.

    The config folder is Fermata's own storage: an empty one is a genuine first
    run and there is no data it could be shadowing. The library folder is the
    user's, and Fermata never creates it - which matters more since #56 gave
    Fermata the ability to move, rename and delete files inside it, not less.
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

    Built by running the real init_db with missing_since taken out of BOTH
    places it now comes from - the SCHEMA text for a fresh table, and
    COLUMN_ADDITIONS for an existing one - rather than by restating a schema by
    hand. A hand-written copy is a second definition that can quietly stop
    matching, and what has to be tested is the upgrade from what the previous
    release actually produced. The stamp is wound back to 3 for the same reason:
    that is the version this database would be carrying.
    """
    previous_schema = db.SCHEMA.replace("    missing_since TEXT,\n", "")
    assert previous_schema != db.SCHEMA, "the column is no longer in SCHEMA as spelled here"
    previous_additions = {
        table: {
            column: definition
            for column, definition in columns.items()
            if column != "missing_since"
        }
        for table, columns in db.COLUMN_ADDITIONS.items()
    }
    monkeypatch.setattr(db, "SCHEMA", previous_schema)
    monkeypatch.setattr(db, "COLUMN_ADDITIONS", previous_additions)
    monkeypatch.setattr(db, "SCHEMA_VERSION", 3)
    monkeypatch.setattr(db, "DB_PATH", path)
    db._local.conn = None
    db.init_db()
    conn = db.connect()
    assert "missing_since" not in {
        r["name"] for r in conn.execute("PRAGMA table_info(scores)")
    }
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
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

    # BY NAME, NOT BY POSITION, and the exclusion of order is deliberate rather
    # than convenient. ALTER TABLE ADD COLUMN can only append, and two of this
    # table's columns arrive that way - instrument_id on every install, and
    # missing_since on an upgraded one - so an upgraded database ends
    # `added_at, instrument_id, missing_since` while a fresh one ends
    # `missing_since, added_at, instrument_id`. No reordering of _SCORES_COLUMNS
    # can reconcile those, because the upgraded database already had
    # instrument_id appended before missing_since existed.
    #
    # That is safe here, and it is safe for a checkable reason rather than by
    # assumption: nothing reads a score row positionally. Every reader goes
    # through sqlite3.Row by column name, and db._rebuild_carrying_rows - the one
    # thing that copies a whole table - works out its column list from PRAGMA
    # table_info on both sides and never uses SELECT *. What must match is the
    # set of columns and what each one IS, which is what this compares.
    def shape(conn):
        return {
            "columns": {
                r["name"]: (r["type"].upper(), r["notnull"], r["dflt_value"], r["pk"])
                for r in conn.execute("PRAGMA table_info(scores)")
            },
            # The indexes too, which an upgrade path is just as capable of
            # leaving behind as a column.
            "indexes": {
                row["name"]: [
                    r["name"] for r in conn.execute(f"PRAGMA index_info({row['name']})")
                ]
                for row in conn.execute("PRAGMA index_list(scores)")
            },
            "foreign_keys": sorted(
                (r["table"], r["from"], r["to"], r["on_delete"])
                for r in conn.execute("PRAGMA foreign_key_list(scores)")
            ),
        }

    upgraded_path = tmp_path / "upgraded.db"
    _previous_release_database(upgraded_path, monkeypatch)
    db._local.conn = None
    monkeypatch.undo()
    monkeypatch.setattr(db, "DB_PATH", upgraded_path)
    db._local.conn = None
    db.init_db()
    upgraded = shape(db.connect())
    upgraded_version = db.connect().execute("PRAGMA user_version").fetchone()[0]

    db._local.conn = None
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fresh.db")
    db._local.conn = None
    db.init_db()
    fresh = shape(db.connect())
    fresh_version = db.connect().execute("PRAGMA user_version").fetchone()[0]
    db._local.conn = None

    assert upgraded == fresh
    assert fresh["columns"]["missing_since"] == ("TEXT", 0, None, 0)
    # Both paths stamp the version that makes an older release refuse this
    # database - see the SCHEMA_VERSION comment. An upgraded install getting the
    # column but not the stamp would leave exactly the rollback hole the bump is
    # for open on every database that mattered.
    assert upgraded_version == fresh_version == db.SCHEMA_VERSION


def test_the_release_before_this_one_refuses_to_open_this_database(tmp_path, monkeypatch):
    """The rollback guard, which is the whole reason the version moved.

    The previous release still deletes every score row it did not see and still
    creates a missing library folder, so pointing it at a database this release
    has written destroys exactly what this change protects. It would not fail on
    the unknown column - it would ignore it, read every marked row as present,
    find no file, and delete the lot with the practice history behind it.

    And this release makes a rollback MORE likely: its new failure mode is a
    container that refuses to start, and the reflex answer to that is to put the
    old tag back. So the old release has to refuse, and the version stamp is the
    only thing that makes it. docs/deployment.md has always promised this
    behaviour; before the bump the promise was false.

    `SCHEMA_VERSION` patched down to 3 IS the previous release, as far as this
    check is concerned - _check_schema_version compares that constant against
    the stamp and nothing else.
    """
    path = tmp_path / "written_by_this_release.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db._local.conn = None
    db.init_db()
    assert db.connect().execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    db._local.conn = None

    monkeypatch.setattr(db, "SCHEMA_VERSION", 3)
    db._local.conn = None
    with pytest.raises(RuntimeError) as raised:
        db.init_db()
    assert "written by a newer release" in str(raised.value)
    db._local.conn = None


def test_startup_says_what_is_wrong_before_the_traceback(tmp_path, monkeypatch, caplog):
    """A stack trace first is what sends an operator reaching for the old tag.

    Uvicorn prints a traceback for anything raised in the lifespan hook, so
    without this the first thing somebody sees when the library folder is
    missing is a wall of Python - at which point the obvious move is to roll the
    image back, which is the one action that destroys their practice history.
    The readable sentence has to come first.
    """
    from fermata import main

    absent = tmp_path / "library"
    monkeypatch.setattr(config, "LIBRARY_DIR", absent)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "config" / "cache")

    with caplog.at_level(logging.ERROR, logger="fermata.startup"):
        with pytest.raises(RuntimeError):
            with TestClient(main.app):
                pass

    logged = [r.getMessage() for r in caplog.records if r.name == "fermata.startup"]
    assert len(logged) == 1
    assert "library folder" in logged[0]
    assert "will not create this folder" in logged[0]
    assert str(absent) in logged[0]


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


def test_the_missing_column_is_an_add_column_and_the_version_still_moved(app_env):
    """Two decisions that look contradictory, pinned together so they read as one.

    The column arrives through COLUMN_ADDITIONS, because it is exactly what that
    mechanism is for: nullable, no foreign key, no backfill needed. But
    SCHEMA_VERSION moved anyway, past the highest MIGRATIONS step, which no
    previous version did. That is not an oversight - it is the downgrade guard.
    The release before this one deletes every score row it did not see, so a
    rollback onto this database destroys what the column exists to protect, and
    the stamp is the only thing that makes the old release refuse.
    """
    assert db.COLUMN_ADDITIONS["scores"]["missing_since"] == "TEXT"
    assert db.SCHEMA_VERSION > max(db.MIGRATIONS), (
        "the version was bumped without a migration step on purpose; see SCHEMA_VERSION"
    )
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

    real_stat = scanner.hash_file

    def explode(path):
        if path.name == "Study 1.gp":
            raise OSError("locked by something else")
        return real_stat(path)

    import unittest.mock

    with unittest.mock.patch.object(scanner, "hash_file", explode):
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
