"""Moving, renaming, deleting and reorganising scores (issue #56).

This is the one feature in Fermata that writes to somebody's own files, so
these tests are not really about the endpoints. They are about the five
promises made in api.py's own comment over this section, each of which is a
way this feature could quietly ruin a library:

  1. nothing is written outside the library folder;
  2. deleting is a move to a trash folder, never a delete, and the row - with
     the practice history, goals, tags and transcription hanging off it -
     stays;
  3. nothing is destroyed as a side effect of an organisational change: a move
     never overwrites, and a batch is all or nothing;
  4. a bulk operation is a dry run until somebody says otherwise;
  5. the score row follows the file by CONTENT HASH, which is the identity test
     the scanner already uses, so a move cannot attach one score's history to
     another score's music.

And a sixth that is not about files at all but about the scanner: a scan and a
move must not run at once, in either order, because a scan decides what to
write from a directory listing taken when it started.

EVERYTHING IS READ BACK THROUGH THE API, never out of the database, wherever
the claim is about what a person would see - the point of "the practice
history survived" is that a client asking for it gets it, and a test that goes
straight to SQL proves the row exists while proving nothing about whether the
library shows it.
"""

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fermata import api, db, scanner, thumbs

FIXTURE = b"<score-file-bytes-standing-in-for-a-pdf>"


@pytest.fixture
def library(app_env, tmp_path, monkeypatch):
    """A throwaway library these endpoints will actually write into.

    app_env has made tmp_path/library and pointed config at it; api.py and
    scanner.py each bound LIBRARY_DIR by value at import, so both are repointed
    here - the same fixture shape test_scanner.py uses, and for the same
    reason.
    """
    root = tmp_path / "library"
    monkeypatch.setattr(api, "LIBRARY_DIR", root)
    monkeypatch.setattr(scanner, "LIBRARY_DIR", root)
    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "config" / "cache")
    return root


@pytest.fixture
def client(library):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


@pytest.fixture
def add_score(library):
    """Put a real file in the library and give it a real score row.

    The row's hash is the file's actual hash, which conftest's `insert_score`
    deliberately does not bother with - and every move here turns on that hash
    matching, because that is how the row follows the file.
    """

    def _add(rel: str, content: bytes = FIXTURE, title: str | None = None) -> int:
        path = library / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        stat = path.stat()
        conn = db.connect()
        parts = rel.split("/")
        cur = conn.execute(
            """INSERT INTO scores(title, collection, path, file_type, hash, size, mtime)
               VALUES (?, ?, ?, 'pdf', ?, ?, ?)""",
            (
                title or parts[-1].rsplit(".", 1)[0],
                parts[0] if len(parts) > 1 else None,
                rel,
                scanner.hash_file(path),
                stat.st_size,
                stat.st_mtime,
            ),
        )
        conn.commit()
        return cur.lastrowid

    return _add


def _wait_for_scan(timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while scanner.scan_status()["scanning"]:
        if time.monotonic() > deadline:
            raise AssertionError("the scan did not finish")
        time.sleep(0.02)


def paths(client) -> set[str]:
    return {s["path"] for s in client.get("/api/scores").json()}


# ---------------------------------------------------------------------------
# Moving and renaming: the file goes, and the row goes with it.
# ---------------------------------------------------------------------------


def test_moving_a_score_moves_the_file_and_the_row_follows_it(client, library, add_score):
    score_id = add_score("Inbox/Study in C.pdf")

    res = client.post(f"/api/scores/{score_id}/move", json={"folder": "Classical/Sor"})
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["applied"] is True
    assert body["dry_run"] is False
    assert [(m["from_path"], m["to_path"], m["status"]) for m in body["moves"]] == [
        ("Inbox/Study in C.pdf", "Classical/Sor/Study in C.pdf", "move")
    ]
    # The file really moved - both ends of it checked, because "the new one
    # exists" would also be true of a copy.
    assert (library / "Classical/Sor/Study in C.pdf").read_bytes() == FIXTURE
    assert not (library / "Inbox/Study in C.pdf").exists()
    # And the SAME row followed it. A new row with the same title would look
    # identical in a list and would carry none of the history.
    assert body["score"]["id"] == score_id
    assert body["score"]["path"] == "Classical/Sor/Study in C.pdf"
    assert client.get(f"/api/scores/{score_id}").json()["path"] == (
        "Classical/Sor/Study in C.pdf"
    )


def test_everything_hanging_off_a_moved_score_survives_the_move(client, library, add_score):
    """The matrix issue #56 exists to protect, checked by literal value.

    Every one of these is set through the API before the move and read back
    through the API after it, so what is being asserted is what a client would
    actually get - not that a row survived somewhere.
    """
    score_id = add_score("Inbox/Prelude.pdf", title="Prelude")

    instrument = client.post(
        "/api/instruments",
        json={
            "name": "Parlour guitar",
            "string_count": 6,
            "string_pitches": ["E2", "A2", "D3", "G3", "B3", "E4"],
            "fretted": True,
            "fret_count": 19,
            "capo": 2,
        },
    ).json()
    client.patch(
        f"/api/scores/{score_id}",
        json={
            "favorite": True,
            "last_page": 7,
            "tags": ["wedding", "warm-up"],
            "instrument_id": instrument["id"],
            "composer": "Villa-Lobos",
        },
    )
    logged = client.post(
        f"/api/scores/{score_id}/practice",
        json={"seconds": 2400, "note": "felt rough", "tempo_bpm": 92, "local_date": "2026-08-01"},
    ).json()["session"]
    goal = client.post(
        "/api/practice/goals",
        json={"scope": "score", "score_id": score_id, "target_days": 5, "intent": "to tempo"},
    ).json()
    client.put(
        f"/api/scores/{score_id}/transcription",
        json={"content": '\\title "Prelude"\n.\n:4 0.1 |'},
    )

    moved = client.post(
        f"/api/scores/{score_id}/move",
        json={"folder": "Classical/Villa-Lobos", "filename": "Prelude No 1.pdf"},
    )
    assert moved.status_code == 200, moved.text

    after = client.get(f"/api/scores/{score_id}").json()
    assert after["path"] == "Classical/Villa-Lobos/Prelude No 1.pdf"
    # The score's own fields, by literal value.
    assert after["favorite"] is True
    assert after["last_page"] == 7
    assert sorted(after["tags"]) == ["warm-up", "wedding"]
    assert after["instrument_id"] == instrument["id"]
    assert after["has_transcription"] is True
    assert after["practice_seconds"] == 2400
    assert after["last_practiced"] == "2026-08-01"

    # The practice session itself, not merely its total.
    practice = client.get(f"/api/scores/{score_id}/practice").json()
    assert practice["session_count"] == 1
    session = practice["sessions"][0]
    assert session["id"] == logged["id"]
    assert (session["seconds"], session["note"], session["tempo_bpm"]) == (
        2400,
        "felt rough",
        92,
    )
    assert session["local_date"] == "2026-08-01"

    # The goal still names the piece, and is still countable against it.
    goals = client.get("/api/practice/goals").json()["goals"]
    assert [g["id"] for g in goals] == [goal["id"]]
    assert goals[0]["score_id"] == score_id
    assert goals[0]["intent"] == "to tempo"
    assert goals[0]["progress"]["countable"] is True

    # And the transcription, by its content.
    transcription = client.get(f"/api/scores/{score_id}/transcription").json()
    assert transcription["content"] == '\\title "Prelude"\n.\n:4 0.1 |'
    assert transcription["source"] == "edited"

    # The file is where it was asked to go, and the old one is not there.
    assert (library / "Classical/Villa-Lobos/Prelude No 1.pdf").read_bytes() == FIXTURE
    assert not (library / "Inbox/Prelude.pdf").exists()


def test_a_move_re_derives_where_a_score_is_but_not_what_it_is(client, library, add_score):
    """`collection` and `series` follow the folders; the piece's own facts do
    not - see api._location_fields."""
    score_id = add_score("Inbox/Study.pdf", title="Study")
    client.patch(
        f"/api/scores/{score_id}",
        json={"title": "Estudio Sencillo", "composer": "Brouwer", "source": "Patreon"},
    )

    client.post(f"/api/scores/{score_id}/move", json={"folder": "Classical/Brouwer/Estudios"})
    after = client.get(f"/api/scores/{score_id}").json()

    assert after["collection"] == "Classical"
    assert after["series"] == "Estudios"
    # Corrected by hand before the move, and still correct after it. Re-deriving
    # these from the path would have made the title "Study" again.
    assert after["title"] == "Estudio Sencillo"
    assert after["composer"] == "Brouwer"
    assert after["source"] == "Patreon"


def test_renaming_a_score_renames_the_file_and_leaves_it_where_it_is(
    client, library, add_score
):
    score_id = add_score("Classical/tarrega-study.pdf")

    res = client.post(f"/api/scores/{score_id}/move", json={"filename": "Study in E minor.pdf"})

    assert res.json()["score"]["path"] == "Classical/Study in E minor.pdf"
    assert (library / "Classical/Study in E minor.pdf").is_file()
    assert not (library / "Classical/tarrega-study.pdf").exists()


def test_a_rename_must_keep_an_extension_fermata_can_read(client, add_score):
    """Renaming a PDF to something the scanner does not pick up is a deletion
    wearing a rename's clothes: the file stays and the library loses it."""
    score_id = add_score("Classical/Study.pdf")

    res = client.post(f"/api/scores/{score_id}/move", json={"filename": "Study.txt"})

    assert res.status_code == 422
    assert "extension" in res.json()["detail"]
    assert client.get(f"/api/scores/{score_id}").json()["path"] == "Classical/Study.pdf"


def test_a_dry_run_move_says_what_would_happen_and_does_none_of_it(
    client, library, add_score
):
    score_id = add_score("Inbox/Study.pdf")

    body = client.post(
        f"/api/scores/{score_id}/move", json={"folder": "Classical", "dry_run": True}
    ).json()

    assert body["dry_run"] is True and body["applied"] is False
    assert body["moves"][0]["to_path"] == "Classical/Study.pdf"
    assert (library / "Inbox/Study.pdf").is_file()
    assert not (library / "Classical").exists()
    assert body["score"]["path"] == "Inbox/Study.pdf"


def test_a_move_that_would_land_on_an_existing_file_is_refused(client, library, add_score):
    """Rule 3: nothing is destroyed as a side effect of an organisational
    change. The file in the way here is not even a score Fermata knows about."""
    score_id = add_score("Inbox/Study.pdf")
    (library / "Classical").mkdir()
    (library / "Classical/Study.pdf").write_bytes(b"something else entirely")

    res = client.post(f"/api/scores/{score_id}/move", json={"folder": "Classical"})

    assert res.status_code == 409
    assert (library / "Classical/Study.pdf").read_bytes() == b"something else entirely"
    assert (library / "Inbox/Study.pdf").read_bytes() == FIXTURE
    assert client.get(f"/api/scores/{score_id}").json()["path"] == "Inbox/Study.pdf"


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("../outside", "is not a usable folder name"),
        ("Classical/../../outside", "is not a usable folder name"),
        ("/etc", "not an absolute path"),
        ("C:/Windows", "not an absolute path"),
        (".fermata-trash", "Fermata's trash folder"),
        (".fermata-trash/12", "Fermata's trash folder"),
    ],
)
def test_a_move_out_of_the_library_folder_is_refused(
    client, library, add_score, folder, expected
):
    """Issue #56 asks for this by name: refuse to write outside the configured
    library directory, and test that. The trash is included because it is
    Fermata's, not a folder scores may be filed into by hand - a score moved in
    there would be invisible to the library and to the trash view alike.

    WHICH GUARD REFUSED IS ASSERTED, not only that something did, and that is
    not fussiness. There are two here - the segment rules in _safe_parts and the
    resolved-path containment check in _resolve_in_library - and they overlap:
    with the message unchecked, taking the segment rules out entirely left this
    test green, because containment quietly caught every case behind it. That is
    the shape of overlapping guard this repository has been bitten by before
    (see scanner.py's note on why its single-pass loss test was removed), so the
    message pins which one actually spoke. Containment has a test of its own
    below, for the same reason in the other direction.
    """
    score_id = add_score("Inbox/Study.pdf")
    outside = library.parent / "outside"

    res = client.post(f"/api/scores/{score_id}/move", json={"folder": folder})

    assert res.status_code == 422, res.text
    assert expected in res.json()["detail"]
    assert not outside.exists()
    assert (library / "Inbox/Study.pdf").read_bytes() == FIXTURE
    assert client.get(f"/api/scores/{score_id}").json()["path"] == "Inbox/Study.pdf"


def test_the_containment_check_refuses_an_escaping_path_on_its_own(library):
    """The second guard, tested where nothing else can be covering for it.

    Every route builds its destination through _safe_parts first, so through
    the API this check is unreachable - which is exactly why removing it
    changed no test result until this one existed. It is called here directly,
    with the kind of path _safe_parts would never let through, because it is
    the guarantee ("nothing is written outside the library folder") rather than
    the error message: it works on the RESOLVED path, so it is also what would
    refuse a destination that only escapes through a symlink, which no amount
    of inspecting the text of a path can catch.
    """
    from fastapi import HTTPException

    (library / "Inside").mkdir()
    assert api._resolve_in_library("Inside/Study.pdf") == library / "Inside" / "Study.pdf"

    for escaping in ("../outside/Study.pdf", "Inside/../../outside/Study.pdf"):
        with pytest.raises(HTTPException) as exc_info:
            api._resolve_in_library(escaping)
        assert exc_info.value.status_code == 422
        assert "outside your library folder" in exc_info.value.detail


def test_moving_a_file_on_disk_never_overwrites_what_is_already_there(library):
    """The last-moment guard, tested where nothing else can be covering for it.

    _plan_move already refuses a destination that exists, so through the API
    this check is unreachable and removing it changed no test result. It exists
    for the gap between planning and moving - something outside Fermata writing
    into the library while a batch is being applied - and it is the reason
    os.replace is not used here, which is a decision worth having a test
    behind.
    """
    from fastapi import HTTPException

    (library / "a.pdf").write_bytes(b"the one being moved")
    (library / "b.pdf").write_bytes(b"the one already there")

    with pytest.raises(HTTPException) as exc_info:
        api._move_file_on_disk(library / "a.pdf", library / "b.pdf")

    assert exc_info.value.status_code == 409
    assert (library / "b.pdf").read_bytes() == b"the one already there"
    assert (library / "a.pdf").read_bytes() == b"the one being moved"


def test_a_file_that_changed_under_the_move_does_not_take_the_history_with_it(
    client, library, add_score, monkeypatch
):
    """Rule 5, and the reason the move re-hashes at the destination.

    The row is only re-pointed at a file whose CONTENT still matches the hash
    the row carries - the same identity test scanner._scan_file's relink makes.
    Here the file is replaced between being listed and being moved, which is
    what a sync client writing into the library looks like; attaching this
    score's practice history to whatever arrived would be silent and
    unrecoverable.
    """
    score_id = add_score("Inbox/Study.pdf")
    real_move = api._move_file_on_disk

    def swap_then_move(src, dest):
        real_move(src, dest)
        dest.write_bytes(b"completely different music")

    monkeypatch.setattr(api, "_move_file_on_disk", swap_then_move)

    res = client.post(f"/api/scores/{score_id}/move", json={"folder": "Classical"})

    assert res.status_code == 409
    assert "different music" in res.json()["detail"]
    # The row still names the file it always named, and the moved file was put
    # back rather than left at the new path with nothing pointing at it.
    assert client.get(f"/api/scores/{score_id}").json()["path"] == "Inbox/Study.pdf"
    assert (library / "Inbox/Study.pdf").is_file()
    assert not (library / "Classical/Study.pdf").exists()


def test_a_score_whose_file_is_missing_cannot_be_moved(client, add_score):
    score_id = add_score("Inbox/Study.pdf")
    conn = db.connect()
    conn.execute(
        "UPDATE scores SET missing_since = datetime('now') WHERE id = ?", (score_id,)
    )
    conn.commit()

    res = client.post(f"/api/scores/{score_id}/move", json={"folder": "Classical"})

    assert res.status_code == 409
    assert "cannot find this score's file" in res.json()["detail"]


def test_a_move_with_neither_a_folder_nor_a_filename_is_refused(client, add_score):
    score_id = add_score("Inbox/Study.pdf")
    res = client.post(f"/api/scores/{score_id}/move", json={})
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# A move and a scan must not overlap, in either order.
# ---------------------------------------------------------------------------


def test_a_move_is_refused_while_a_scan_is_running(client, add_score, monkeypatch):
    """A scan decides what to write from a listing taken when it started, so a
    file moving underneath it reads as a file that went missing - see
    scanner.hold_library_still."""
    score_id = add_score("Inbox/Study.pdf")
    monkeypatch.setitem(scanner._state, "scanning", True)

    res = client.post(f"/api/scores/{score_id}/move", json={"folder": "Classical"})

    assert res.status_code == 409
    assert "scan is running" in res.json()["detail"]
    assert client.get(f"/api/scores/{score_id}").json()["path"] == "Inbox/Study.pdf"


def test_a_scan_will_not_start_while_the_library_is_being_changed(client, add_score):
    """The other direction, which is the one that actually corrupts something:
    a scan starting mid-move takes its listing with the file at neither end."""
    add_score("Inbox/Study.pdf")

    with scanner.hold_library_still():
        assert scanner.start_scan() is False
        assert client.post("/api/scan").json()["started"] is False

    # And the exclusion lifts by itself.
    assert scanner.start_scan() is True
    _wait_for_scan()


def test_a_second_change_is_refused_while_one_is_being_applied(client, add_score):
    score_id = add_score("Inbox/Study.pdf")

    with scanner.hold_library_still():
        res = client.post(f"/api/scores/{score_id}/move", json={"folder": "Classical"})

    assert res.status_code == 409
    assert "another change" in res.json()["detail"]


def test_the_next_scan_leaves_a_moved_score_exactly_as_the_move_left_it(
    client, library, add_score
):
    """End to end against the real scanner: the move re-points the row itself,
    so the scan that follows has nothing to reconcile - no relink, no mark, no
    second row for one piece."""
    score_id = add_score("Inbox/Study.pdf")
    add_score("Inbox/Other.pdf", content=b"a different score entirely")
    client.post(f"/api/scores/{score_id}/move", json={"folder": "Classical/Sor"})

    scanner._scan()

    status = scanner.scan_status()
    assert (status["missing"], status["added"], status["refused"]) == (0, 0, False)
    assert paths(client) == {"Classical/Sor/Study.pdf", "Inbox/Other.pdf"}
    assert client.get(f"/api/scores/{score_id}").json()["missing_since"] is None


# ---------------------------------------------------------------------------
# Reorganising: folders, and moving several scores at once.
# ---------------------------------------------------------------------------


def test_a_batch_move_is_a_dry_run_unless_it_is_told_otherwise(client, library, add_score):
    """Rule 4, and the default issue #56 asks for by name: a client that has
    never heard of `dry_run` gets the plan, not the reorganisation."""
    first = add_score("Inbox/One.pdf", content=b"one")
    second = add_score("Inbox/Two.pdf", content=b"two")

    body = client.post(
        "/api/library/move", json={"score_ids": [first, second], "folder": "Classical"}
    ).json()

    assert body["dry_run"] is True and body["applied"] is False
    assert (body["moved"], body["blocked"], body["unchanged"]) == (2, 0, 0)
    assert {m["to_path"] for m in body["moves"]} == {
        "Classical/One.pdf",
        "Classical/Two.pdf",
    }
    assert (library / "Inbox/One.pdf").is_file()
    assert not (library / "Classical").exists()


def test_a_batch_move_applied_moves_every_one_of_them(client, library, add_score):
    first = add_score("Inbox/One.pdf", content=b"one")
    second = add_score("Inbox/Two.pdf", content=b"two")

    body = client.post(
        "/api/library/move",
        json={"score_ids": [first, second], "folder": "Classical", "dry_run": False},
    ).json()

    assert body["applied"] is True and body["moved"] == 2
    assert paths(client) == {"Classical/One.pdf", "Classical/Two.pdf"}
    assert (library / "Classical/One.pdf").read_bytes() == b"one"
    assert (library / "Classical/Two.pdf").read_bytes() == b"two"
    assert not (library / "Inbox/One.pdf").exists()


def test_a_batch_with_one_blocked_line_moves_nothing_at_all(client, library, add_score):
    """Rule 3's other half: half a reorganisation is worse than none, because
    the person then has to work out which half happened."""
    first = add_score("Inbox/One.pdf", content=b"one")
    second = add_score("Inbox/Two.pdf", content=b"two")
    (library / "Classical").mkdir()
    (library / "Classical/Two.pdf").write_bytes(b"already here")

    res = client.post(
        "/api/library/move",
        json={"score_ids": [first, second], "folder": "Classical", "dry_run": False},
    )

    assert res.status_code == 409
    assert "already a file at that path" in res.json()["detail"]
    # Including the one that had nothing wrong with it.
    assert paths(client) == {"Inbox/One.pdf", "Inbox/Two.pdf"}
    assert (library / "Inbox/One.pdf").read_bytes() == b"one"
    assert (library / "Classical/Two.pdf").read_bytes() == b"already here"


def test_two_scores_that_would_collide_are_both_reported_rather_than_one_lost(
    client, library, add_score
):
    """Two files with the same name in different folders, moved into one. The
    second must not land on the first."""
    first = add_score("Bach/Prelude.pdf", content=b"bach")
    second = add_score("Chopin/Prelude.pdf", content=b"chopin")

    body = client.post(
        "/api/library/move", json={"score_ids": [first, second], "folder": "Favourites"}
    ).json()

    statuses = {m["score_id"]: m["status"] for m in body["moves"]}
    assert statuses[second] == "blocked"
    assert body["blocked"] == 1
    reason = next(m["reason"] for m in body["moves"] if m["score_id"] == second)
    assert "another score in this same move" in reason


def test_a_folder_can_be_created_and_moved_into(client, library, add_score):
    score_id = add_score("Inbox/Study.pdf")

    created = client.post("/api/library/folders", json={"path": "Wedding/Processional"})
    assert created.status_code == 200, created.text
    assert created.json() == {"created": "Wedding/Processional", "existed": False}
    assert (library / "Wedding/Processional").is_dir()

    # Offered by the folder list even though nothing is in it yet, which is the
    # whole point of being able to make one.
    folders = client.get("/api/library/folders").json()
    by_path = {f["path"]: f for f in folders}
    assert by_path["Wedding/Processional"]["score_count"] == 0
    assert by_path["Wedding/Processional"]["depth"] == 2
    assert by_path["Inbox"]["score_count"] == 1
    assert by_path[""]["name"] == "Library root"

    client.post(
        "/api/library/move",
        json={"score_ids": [score_id], "folder": "Wedding/Processional", "dry_run": False},
    )
    assert client.get(f"/api/scores/{score_id}").json()["path"] == (
        "Wedding/Processional/Study.pdf"
    )
    assert client.get("/api/library/folders").json()
    assert {f["path"]: f["score_count"] for f in client.get("/api/library/folders").json()}[
        "Wedding/Processional"
    ] == 1


def test_creating_a_folder_that_is_already_there_is_not_an_error(client, library):
    (library / "Classical").mkdir()
    res = client.post("/api/library/folders", json={"path": "Classical"})
    assert res.json() == {"created": "Classical", "existed": True}


def test_the_trash_folder_is_not_offered_as_a_destination(client, library, add_score):
    score_id = add_score("Inbox/Study.pdf")
    client.delete(f"/api/scores/{score_id}")

    assert (library / scanner.TRASH_DIR_NAME).is_dir()
    assert all(
        not f["path"].startswith(scanner.TRASH_DIR_NAME)
        for f in client.get("/api/library/folders").json()
    )


def test_renaming_a_folder_takes_its_scores_with_it(client, library, add_score):
    first = add_score("Patreon/One.pdf", content=b"one")
    second = add_score("Patreon/Series/Two.pdf", content=b"two")
    client.post(f"/api/scores/{first}/practice", json={"seconds": 900})

    preview = client.post(
        "/api/library/folders/rename",
        json={"from_path": "Patreon", "to_path": "Arrangements"},
    ).json()
    assert preview["dry_run"] is True and preview["applied"] is False
    assert {m["to_path"] for m in preview["moves"]} == {
        "Arrangements/One.pdf",
        "Arrangements/Series/Two.pdf",
    }
    assert (library / "Patreon/One.pdf").is_file()

    applied = client.post(
        "/api/library/folders/rename",
        json={"from_path": "Patreon", "to_path": "Arrangements", "dry_run": False},
    ).json()
    assert applied["applied"] is True and applied["moved"] == 2

    assert paths(client) == {"Arrangements/One.pdf", "Arrangements/Series/Two.pdf"}
    assert (library / "Arrangements/Series/Two.pdf").read_bytes() == b"two"
    assert not (library / "Patreon").exists()
    # The scores are the same scores: same ids, and the practice is still on
    # the one that has it.
    assert client.get(f"/api/scores/{first}").json()["practice_seconds"] == 900
    assert client.get(f"/api/scores/{second}").json()["id"] == second
    # The sidebar's collection follows the folder, at every depth.
    assert {c["collection"] for c in client.get("/api/collections").json()} == {
        "Arrangements"
    }


def test_a_folder_cannot_be_renamed_into_itself_or_onto_something(client, library, add_score):
    add_score("Patreon/One.pdf")
    (library / "Taken").mkdir()

    inside = client.post(
        "/api/library/folders/rename",
        json={"from_path": "Patreon", "to_path": "Patreon/Deeper", "dry_run": False},
    )
    assert inside.status_code == 422
    assert "inside itself" in inside.json()["detail"]

    onto = client.post(
        "/api/library/folders/rename",
        json={"from_path": "Patreon", "to_path": "Taken", "dry_run": False},
    )
    assert onto.status_code == 409
    assert (library / "Patreon/One.pdf").is_file()


def test_renaming_a_folder_that_is_not_there_is_a_404(client):
    res = client.post(
        "/api/library/folders/rename",
        json={"from_path": "Nowhere", "to_path": "Somewhere", "dry_run": False},
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Deleting: a move to the trash, and the second, separate step that destroys.
# ---------------------------------------------------------------------------


def test_deleting_a_score_moves_its_file_to_the_trash_and_keeps_the_row(
    client, library, add_score
):
    score_id = add_score("Inbox/Study.pdf")
    client.post(f"/api/scores/{score_id}/practice", json={"seconds": 1800, "note": "slow"})
    client.patch(f"/api/scores/{score_id}", json={"tags": ["wedding"]})

    res = client.delete(f"/api/scores/{score_id}")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["deleted"] == score_id
    assert body["deleted_from"] == "Inbox/Study.pdf"
    assert body["trashed_to"].startswith(f"{scanner.TRASH_DIR_NAME}/{score_id}/")
    # The counts that make "nothing was lost" a number rather than a promise.
    assert body["practice_sessions_kept"] == 1
    assert body["tags_kept"] == 1

    # The file MOVED. It is out of the library proper and still on disk.
    assert not (library / "Inbox/Study.pdf").exists()
    assert (library / body["trashed_to"]).read_bytes() == FIXTURE
    # The row is still there, still answers to its id, and still has its
    # practice on it.
    row = client.get(f"/api/scores/{score_id}").json()
    assert row["deleted_at"] is not None
    assert row["deleted_from"] == "Inbox/Study.pdf"
    assert row["practice_seconds"] == 1800
    assert client.get(f"/api/scores/{score_id}/practice").json()["sessions"][0]["note"] == (
        "slow"
    )


def test_a_deleted_score_leaves_every_library_view_and_appears_in_the_trash(
    client, add_score
):
    kept = add_score("Classical/Kept.pdf", content=b"kept")
    doomed = add_score("Classical/Doomed.pdf", content=b"doomed")
    client.patch(f"/api/scores/{doomed}", json={"tags": ["dross"]})

    client.delete(f"/api/scores/{doomed}")

    assert paths(client) == {"Classical/Kept.pdf"}
    assert [s["id"] for s in client.get("/api/trash").json()] == [doomed]
    # The counts the sidebar shows follow it out.
    assert [(c["collection"], c["count"]) for c in client.get("/api/collections").json()] == [
        ("Classical", 1)
    ]
    assert {t["name"]: t["count"] for t in client.get("/api/tags").json()}["dross"] == 0
    assert client.get(f"/api/scores/{kept}").json()["deleted_at"] is None


def test_two_identical_scores_stop_being_duplicates_when_one_is_deleted(client, add_score):
    first = add_score("Bach/Prelude.pdf")
    add_score("Copies/Prelude.pdf")
    assert len(client.get("/api/duplicates").json()) == 1

    client.delete(f"/api/scores/{first}")

    assert client.get("/api/duplicates").json() == []


def test_a_scan_does_not_bring_a_deleted_score_back(client, library, add_score):
    """The relink candidate filter, which is the one that would resurrect a
    deletion silently: the trashed file has the same content hash, and a fresh
    copy of that content arriving in the library must become its own score
    rather than adopting the deleted one's row."""
    score_id = add_score("Inbox/Study.pdf")
    client.delete(f"/api/scores/{score_id}")

    (library / "Elsewhere").mkdir()
    (library / "Elsewhere/Study.pdf").write_bytes(FIXTURE)
    scanner._scan()

    deleted = client.get(f"/api/scores/{score_id}").json()
    assert deleted["deleted_at"] is not None
    assert deleted["path"].startswith(scanner.TRASH_DIR_NAME)
    assert paths(client) == {"Elsewhere/Study.pdf"}
    assert [s["id"] for s in client.get("/api/scores").json()] != [score_id]


def test_a_scan_does_not_take_anything_in_the_trash_for_a_score(client, library, add_score):
    """The trash is not part of the library, and the scan's own listing is
    where that has to be true.

    Without the exclusion in scanner._library_files, anything sitting in the
    trash folder is walked like any other file - so a file put there by hand,
    or left behind by an interrupted purge, becomes a brand new score in the
    library, filed under a collection called `.fermata-trash`. Deleted things
    reappearing as new scores is the exact failure this feature must not have.
    The reported totals are checked too: somebody watching a scan should not be
    told their library holds files they deleted.
    """
    add_score("Inbox/Kept.pdf", content=b"kept")
    trash = library / scanner.TRASH_DIR_NAME / "stray"
    trash.mkdir(parents=True)
    (trash / "Left behind.pdf").write_bytes(b"not a score any more")

    scanner._scan()

    assert paths(client) == {"Inbox/Kept.pdf"}
    assert scanner.scan_status()["total"] == 1
    assert all(
        not s["path"].startswith(scanner.TRASH_DIR_NAME)
        for s in client.get("/api/scores").json()
    )


def test_a_scan_does_not_mark_a_deleted_score_missing_or_refuse_over_it(
    client, library, add_score
):
    """A deleted score's file is in the trash, which the scan skips - so
    counting it among the rows believed present would make every deletion look
    like a file that vanished."""
    doomed = add_score("Inbox/Doomed.pdf", content=b"doomed")
    add_score("Inbox/Kept.pdf", content=b"kept")
    client.delete(f"/api/scores/{doomed}")

    scanner._scan()

    status = scanner.scan_status()
    assert (status["missing"], status["refused"], status["added"]) == (0, False, 0)
    assert client.get(f"/api/scores/{doomed}").json()["missing_since"] is None


def test_deleting_most_of_a_library_does_not_make_the_next_lost_file_a_refusal(
    client, library, add_score
):
    """scanner.record_deliberate_shrink, and what goes wrong without it.

    The proportional guard measures what remains against the most this library
    has ever held. That mark only rises by itself, so deleting most of a library
    on purpose would leave it describing a library that no longer exists - and
    the very next file that genuinely went missing would take the remaining
    count under half of it and refuse a scan over one file, about a loss the
    person had already confirmed twelve times by pressing delete.
    """
    ids = [add_score(f"Inbox/Score {n}.pdf", content=f"score {n}".encode()) for n in range(12)]
    scanner._scan()
    assert scanner.scan_status()["refused"] is False

    for score_id in ids[:9]:
        assert client.delete(f"/api/scores/{score_id}").status_code == 200

    # And now one of the three that are left genuinely goes missing.
    (library / "Inbox/Score 9.pdf").unlink()
    scanner._scan()

    status = scanner.scan_status()
    assert status["refused"] is False, status["refused_reason"]
    assert status["missing"] == 1


def test_a_deleted_score_can_be_restored_with_everything_still_on_it(
    client, library, add_score
):
    score_id = add_score("Inbox/Study.pdf")
    client.post(f"/api/scores/{score_id}/practice", json={"seconds": 3600, "note": "good"})
    client.patch(f"/api/scores/{score_id}", json={"tags": ["wedding"], "last_page": 4})
    client.delete(f"/api/scores/{score_id}")

    res = client.post(f"/api/trash/{score_id}/restore")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["restored"] == score_id
    assert body["restored_from"] == "Inbox/Study.pdf"
    assert body["restored_to"] == "Inbox/Study.pdf"
    assert (library / "Inbox/Study.pdf").read_bytes() == FIXTURE
    assert not any((library / scanner.TRASH_DIR_NAME).rglob("*.pdf"))

    restored = client.get(f"/api/scores/{score_id}").json()
    assert restored["deleted_at"] is None and restored["deleted_from"] is None
    assert restored["path"] == "Inbox/Study.pdf"
    assert restored["practice_seconds"] == 3600
    assert restored["tags"] == ["wedding"]
    assert restored["last_page"] == 4
    assert paths(client) == {"Inbox/Study.pdf"}
    assert client.get("/api/trash").json() == []


def test_restoring_onto_a_taken_path_lands_beside_it_rather_than_over_it(
    client, library, add_score
):
    score_id = add_score("Inbox/Study.pdf")
    client.delete(f"/api/scores/{score_id}")
    (library / "Inbox").mkdir(exist_ok=True)
    (library / "Inbox/Study.pdf").write_bytes(b"something else took the name")

    body = client.post(f"/api/trash/{score_id}/restore").json()

    assert body["restored_from"] == "Inbox/Study.pdf"
    assert body["restored_to"] == "Inbox/Study (1).pdf"
    assert (library / "Inbox/Study.pdf").read_bytes() == b"something else took the name"
    assert (library / "Inbox/Study (1).pdf").read_bytes() == FIXTURE


def test_restoring_onto_a_path_a_missing_score_still_claims_lands_beside_it(
    client, library, add_score
):
    """scores.path is UNIQUE, and a row can name a path with no file behind it -
    a score marked missing is exactly that. Checking only the filesystem before
    putting a file back would let the update collide with that row and answer an
    unexplained 500 at the moment somebody tried to undo a deletion."""
    doomed = add_score("Inbox/Study.pdf", content=b"the deleted one")
    client.delete(f"/api/scores/{doomed}")
    # Another score claims the freed path, and then loses its file - so the path
    # is free on disk and taken in the database.
    other = add_score("Inbox/Study.pdf", content=b"a different arrangement")
    (library / "Inbox/Study.pdf").unlink()
    conn = db.connect()
    conn.execute("UPDATE scores SET missing_since = datetime('now') WHERE id = ?", (other,))
    conn.commit()

    res = client.post(f"/api/trash/{doomed}/restore")

    assert res.status_code == 200, res.text
    assert res.json()["restored_to"] == "Inbox/Study (1).pdf"
    assert (library / "Inbox/Study (1).pdf").read_bytes() == b"the deleted one"
    # The other score is left exactly as it was, still marked missing.
    assert client.get(f"/api/scores/{other}").json()["path"] == "Inbox/Study.pdf"


def test_a_folder_rename_onto_a_path_another_score_claims_is_refused(
    client, library, add_score
):
    """The same UNIQUE collision, on the route that re-points many rows at once.
    Reported as a blocked line and refused as a whole, not raised as a 500."""
    add_score("Patreon/One.pdf", content=b"one")
    blocker = add_score("Arrangements/One.pdf", content=b"another one")
    (library / "Arrangements/One.pdf").unlink()
    (library / "Arrangements").rmdir()
    conn = db.connect()
    conn.execute("UPDATE scores SET missing_since = datetime('now') WHERE id = ?", (blocker,))
    conn.commit()

    res = client.post(
        "/api/library/folders/rename",
        json={"from_path": "Patreon", "to_path": "Arrangements", "dry_run": False},
    )

    assert res.status_code == 409, res.text
    assert "already at that path" in res.json()["detail"]
    assert (library / "Patreon/One.pdf").read_bytes() == b"one"
    assert paths(client) == {"Patreon/One.pdf", "Arrangements/One.pdf"}


def test_a_folder_name_the_filesystem_refuses_is_a_422_not_a_500(client, monkeypatch):
    """The segment rules cannot know what a particular filesystem will accept -
    Windows reserves CON and NUL outright, a disk can be full, a mount can be
    read-only. Whatever it says comes back as a refusal rather than a crash."""

    def refuse(*args, **kwargs):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr("pathlib.Path.mkdir", refuse)

    res = client.post("/api/library/folders", json={"path": "Nope"})

    assert res.status_code == 422
    assert "would not take that folder name" in res.json()["detail"]


def test_a_score_that_is_not_in_the_trash_cannot_be_restored_or_destroyed(
    client, add_score
):
    score_id = add_score("Inbox/Study.pdf")

    assert client.post(f"/api/trash/{score_id}/restore").status_code == 404
    # The destructive route only ever acts on something already deleted, which
    # is what makes destroying a score always the second of two steps.
    assert client.delete(f"/api/trash/{score_id}").status_code == 404
    assert client.get(f"/api/scores/{score_id}").json()["deleted_at"] is None


def test_deleting_a_score_twice_is_refused_rather_than_moving_it_again(client, add_score):
    score_id = add_score("Inbox/Study.pdf")
    client.delete(f"/api/scores/{score_id}")

    res = client.delete(f"/api/scores/{score_id}")

    assert res.status_code == 409
    assert "already in the trash" in res.json()["detail"]


def test_destroying_a_score_from_the_trash_removes_the_file_and_the_row(
    client, library, add_score
):
    score_id = add_score("Inbox/Study.pdf")
    client.patch(f"/api/scores/{score_id}", json={"tags": ["dross"]})
    client.put(f"/api/scores/{score_id}/transcription", json={"content": '\\title "x"\n.\n:4 0.1 |'})
    logged = client.post(f"/api/scores/{score_id}/practice", json={"seconds": 1200}).json()
    trashed = client.delete(f"/api/scores/{score_id}").json()

    res = client.delete(f"/api/trash/{score_id}")
    assert res.status_code == 200, res.text
    body = res.json()

    # It says what it destroyed, counted before the delete ran.
    assert body["deleted"] == score_id
    assert body["file_deleted"] == trashed["trashed_to"]
    assert body["tags_destroyed"] == 1
    assert body["transcriptions_destroyed"] == 1
    # ...and what it kept.
    assert body["practice_sessions_kept"] == 1

    assert not (library / trashed["trashed_to"]).exists()
    assert client.get(f"/api/scores/{score_id}").status_code == 404
    assert client.get("/api/trash").json() == []
    # The hours were still spent. The session survives, recording practice with
    # no piece named - see db.py on why that reference is ON DELETE SET NULL.
    sessions = client.get("/api/practice/sessions").json()["sessions"]
    kept = [s for s in sessions if s["id"] == logged["session"]["id"]]
    assert len(kept) == 1
    assert kept[0]["seconds"] == 1200
    assert kept[0]["score_id"] is None


def test_deleting_is_refused_while_a_scan_is_running(client, library, add_score, monkeypatch):
    score_id = add_score("Inbox/Study.pdf")
    monkeypatch.setitem(scanner._state, "scanning", True)

    res = client.delete(f"/api/scores/{score_id}")

    assert res.status_code == 409
    assert (library / "Inbox/Study.pdf").is_file()
    assert client.get(f"/api/scores/{score_id}").json()["deleted_at"] is None
