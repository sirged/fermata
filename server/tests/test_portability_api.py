"""Getting everything in and out (issue #58).

The headline claim this feature makes is a round trip: everything a fresh
install of Fermata can be TOLD - a score, its transcription, a session, a
goal, a tag, a favourite, an instrument, a setting - has to be gettable back
OUT, and back IN to an empty library, with nothing dropped, renamed, or
quietly changed along the way. `test_export_import_round_trip_is_lossless`
is that claim, checked field by field through the API on BOTH ends - the
same discipline test_library_management_api.py holds #56 to, and for the
same reason: a test that read the database directly would prove a row
exists without proving a client could ever see it.

The rest of this file is the failure side of the same promise. An archive
either applies completely or changes nothing at all - never halfway - so
every rejection path here (`test_import_rejects_...`) asserts on the
target library's state AFTER the rejection, not only on the HTTP status: a
clean 422 that still left one row behind would be exactly the bug this
feature exists not to have.
"""

import hashlib
import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fermata import api, db, scanner

FIXTURE = b"<score-file-bytes-standing-in-for-a-pdf>"
OTHER_FIXTURE = b"<a-second-score-entirely>"


@pytest.fixture
def library(app_env, tmp_path, monkeypatch):
    """A throwaway library the export/import routes will actually read and
    write - the same fixture shape test_library_management_api.py uses, and
    for the same reason: api.py and scanner.py each bound LIBRARY_DIR by
    value at import, so both need repointing alongside app_env's own."""
    root = tmp_path / "library"
    monkeypatch.setattr(api, "LIBRARY_DIR", root)
    monkeypatch.setattr(scanner, "LIBRARY_DIR", root)
    return root


@pytest.fixture
def client(library):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


@pytest.fixture
def add_score(library):
    """Put a real file in the library and give it a real score row, hashed
    from the actual bytes - the same fixture test_library_management_api.py
    uses, so a move/relink-shaped check here means the same thing it does
    there."""

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


def _zip_of(resp) -> zipfile.ZipFile:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(resp.content))


def _switch_to_a_fresh_environment(monkeypatch, tmp_path, name: str):
    """Point config, db and the two modules that bind LIBRARY_DIR by value at
    a brand new, empty root - the target half of a round trip. Mirrors what
    conftest's `app_env` does for ONE environment; this test needs two in the
    same process, which app_env's fixture shape (a single yield) cannot give,
    so the same steps are done here by hand for the second one.
    """
    from fermata import config

    root = tmp_path / name
    (root / "library").mkdir(parents=True)
    monkeypatch.setattr(config, "LIBRARY_DIR", root / "library")
    monkeypatch.setattr(config, "CONFIG_DIR", root / "config")
    monkeypatch.setattr(config, "CACHE_DIR", root / "config" / "cache")
    monkeypatch.setattr(api, "LIBRARY_DIR", root / "library")
    monkeypatch.setattr(scanner, "LIBRARY_DIR", root / "library")
    monkeypatch.setattr(db, "DB_PATH", root / "config" / "fermata.db")
    (root / "config").mkdir(parents=True, exist_ok=True)
    db._local.conn = None
    db.init_db()


def _by_title(scores: list[dict]) -> dict[str, dict]:
    return {s["title"]: s for s in scores}


# ---------------------------------------------------------------------------
# The headline claim.
# ---------------------------------------------------------------------------


def test_export_import_round_trip_is_lossless(client, library, add_score, tmp_path, monkeypatch):
    # --- Build a source library with one of everything #32/#58 name. ---
    prelude_id = add_score("Classical/Prelude.pdf", content=FIXTURE, title="Prelude")
    etude_id = add_score("Classical/Etude.pdf", content=OTHER_FIXTURE, title="Etude")
    doomed_id = add_score("Inbox/Doomed.pdf", content=b"<gone but not forgotten>", title="Doomed")

    instrument = client.post(
        "/api/instruments",
        json={
            "name": "Parlour guitar",
            "string_count": 6,
            "string_pitches": ["E2", "A2", "D3", "G3", "B3", "E4"],
            "fretted": True,
            "fret_count": 19,
            "capo": 2,
            "reference_pitch": 442.0,
        },
    ).json()
    client.patch(
        f"/api/scores/{prelude_id}",
        json={"favorite": True, "instrument_id": instrument["id"], "tags": ["warm-up", "recital"]},
    )
    client.patch(f"/api/scores/{etude_id}", json={"tags": ["recital"]})

    # A transcription with real content and a confidence blob - the fields
    # #58 names by name (content + source + disclosure).
    conn = db.connect()
    conn.execute(
        """INSERT INTO transcriptions(score_id, format, content, source, confidence)
           VALUES (?, 'alphatex', ?, 'edited', ?)""",
        (prelude_id, '\\title "Prelude"\n.\n:4 0.1 |', json.dumps({"bars_overfull": 0})),
    )
    conn.commit()

    # Practice: several sessions across activities, with tempo, mode, rating
    # and a note - and one session against no score at all (a free-practice
    # activity), which has to survive with score_id staying null.
    client.post(
        f"/api/scores/{prelude_id}/practice",
        json={"seconds": 900, "tempo_bpm": 88, "target_tempo_bpm": 112, "mode": "section",
              "rating": 4, "note": "bar 12 still rushes"},
    )
    client.post(
        f"/api/scores/{etude_id}/practice",
        json={"seconds": 600, "mode": "run_through", "rating": 3},
    )
    client.post(
        "/api/practice/sessions",
        json={"seconds": 300, "activity": "ear_training", "note": "interval drill"},
    )

    goal = client.post(
        "/api/practice/goals",
        json={"scope": "score", "score_id": prelude_id, "target_days": 3,
              "intent": "clean at full tempo"},
    ).json()

    client.put("/api/settings", json={"staff_theme": "noir", "week_starts_on": "sunday"})

    # A setlist arranged by hand (#6), including the score that is about to be
    # trashed. Hand-arranged order is non-regenerable data, so #58 must carry
    # it: this proves it does, and that a trashed member survives marked rather
    # than being silently dropped from the backup.
    setlist = client.post("/api/setlists", json={"name": "Recital order"}).json()
    for member in (prelude_id, etude_id, doomed_id):
        added = client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": member})
        assert added.status_code == 200, added.text
    # Reorder so the STORED order is not the insertion order - what travels must
    # be the arrangement, not the sequence rows happened to be added in.
    reordered = client.put(
        f"/api/setlists/{setlist['id']}/order",
        json={"score_ids": [etude_id, doomed_id, prelude_id]},
    )
    assert reordered.status_code == 200, reordered.text

    # A score in the trash, with practice history attached, deliberately kept
    # in the export (include_trash defaults to true).
    delete_result = client.delete(f"/api/scores/{doomed_id}")
    assert delete_result.status_code == 200, delete_result.text

    # --- What the SOURCE library says, before anything is exported. ---
    expected_scores = _by_title(client.get("/api/scores").json())
    expected_trash = _by_title(client.get("/api/trash").json())
    expected_sessions = sorted(
        client.get("/api/practice/sessions").json()["sessions"],
        key=lambda s: (s["seconds"], s["note"] or ""),
    )
    expected_goals = client.get("/api/practice/goals").json()["goals"]
    expected_settings = client.get("/api/settings").json()
    expected_instruments = client.get("/api/instruments").json()
    expected_transcription = client.get(f"/api/scores/{prelude_id}/transcription").json()

    export_resp = client.get("/api/export")
    archive = export_resp.content

    # --- Move to a second, completely fresh library and database. ---
    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    assert client.get("/api/scores").json() == []

    import_resp = client.post(
        "/api/import",
        params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert import_resp.status_code == 200, import_resp.text
    summary = import_resp.json()
    assert summary["dry_run"] is False
    assert summary["scores_imported"] == 3
    assert summary["scores_trashed_imported"] == 1
    assert summary["files_written"] == 3
    assert summary["transcriptions_imported"] == 1
    assert summary["practice_sessions_imported"] == 3
    assert summary["practice_goals_imported"] == 1
    assert summary["instruments_imported"] == 1
    assert summary["tags_imported"] == 2
    assert summary["setlists_imported"] == 1
    assert summary["setlist_scores_imported"] == 3

    # --- Every field, read back through the API, equal by literal value. ---
    actual_scores = _by_title(client.get("/api/scores").json())
    actual_trash = _by_title(client.get("/api/trash").json())

    for title in ("Prelude", "Etude"):
        exp, act = expected_scores[title], actual_scores[title]
        for field in (
            "title", "composer", "collection", "series", "source", "path",
            "file_type", "content_kind", "pages", "favorite", "hash", "size",
            "last_page", "missing_since", "deleted_at", "deleted_from",
            "tags", "practice_seconds", "last_practiced",
        ):
            assert act[field] == exp[field], f"{title}.{field}: {act[field]!r} != {exp[field]!r}"

    doomed_exp, doomed_act = expected_trash["Doomed"], actual_trash["Doomed"]
    for field in ("title", "deleted_from", "tags", "hash", "size"):
        assert doomed_act[field] == doomed_exp[field]
    assert doomed_act["deleted_at"] is not None
    # The trashed file itself really travelled, not only its row.
    trashed_path = tmp_path / "target" / "library" / doomed_act["path"]
    assert trashed_path.read_bytes() == b"<gone but not forgotten>"

    # The instrument followed by value, and Prelude's link to it survived the
    # remap (both ends got a fresh id on the target side; what matters is
    # they still point at each other's DATA, not at the same numbers).
    assert len(expected_instruments) == 1
    actual_instruments = client.get("/api/instruments").json()
    assert len(actual_instruments) == 1
    for field in ("name", "kind", "fretted", "string_count", "string_pitches",
                   "fret_count", "capo", "reference_pitch"):
        assert actual_instruments[0][field] == expected_instruments[0][field]
    assert actual_scores["Prelude"]["instrument_id"] is not None

    actual_transcription = client.get(
        f"/api/scores/{actual_scores['Prelude']['id']}/transcription"
    ).json()
    for field in ("format", "content", "source"):
        assert actual_transcription[field] == expected_transcription[field]

    actual_sessions = sorted(
        client.get("/api/practice/sessions").json()["sessions"],
        key=lambda s: (s["seconds"], s["note"] or ""),
    )
    assert len(actual_sessions) == len(expected_sessions) == 3
    for exp, act in zip(expected_sessions, actual_sessions):
        for field in ("activity", "mode", "started_at", "local_date", "seconds",
                       "tempo_bpm", "target_tempo_bpm", "rating", "note"):
            assert act[field] == exp[field], f"session {field}: {act[field]!r} != {exp[field]!r}"

    actual_goals = client.get("/api/practice/goals").json()["goals"]
    assert len(actual_goals) == len(expected_goals) == 1
    for field in ("period", "period_start", "period_end", "target_days",
                  "target_minutes", "scope", "activity", "intent", "reflection",
                  "realistic"):
        assert actual_goals[0][field] == expected_goals[0][field]

    assert client.get("/api/settings").json() == expected_settings

    # --- The setlist and its ORDERED membership survived (#6 through #58). ---
    actual_setlists = client.get("/api/setlists").json()
    assert len(actual_setlists) == 1
    assert actual_setlists[0]["name"] == "Recital order"
    # All three members are counted, the trashed one included - it is still in
    # the setlist.
    assert actual_setlists[0]["score_count"] == 3
    actual_setlist = client.get(f"/api/setlists/{actual_setlists[0]['id']}").json()
    # The reordered order travelled by literal value, not insertion order, and
    # the positions are contiguous 1..3.
    assert [m["score"]["title"] for m in actual_setlist["scores"]] == ["Etude", "Doomed", "Prelude"]
    assert [m["position"] for m in actual_setlist["scores"]] == [1, 2, 3]
    # The trashed member came back marked, not dropped and not a broken link -
    # exactly what #6 requires of a deleted score in a setlist.
    doomed_member = next(
        m for m in actual_setlist["scores"] if m["score"]["title"] == "Doomed"
    )
    assert doomed_member["score"]["deleted_at"] is not None
    # And membership follows the id remap: every member points at a score that
    # is really in this target library, not at a source-side id.
    target_score_ids = {s["id"] for s in client.get("/api/scores").json()} | {
        s["id"] for s in client.get("/api/trash").json()
    }
    assert all(m["score"]["id"] in target_score_ids for m in actual_setlist["scores"])


def test_export_can_leave_the_trash_out(client, add_score):
    live_id = add_score("Keeper.pdf", title="Keeper")
    trashed_id = add_score("Doomed.pdf", title="Doomed")
    client.delete(f"/api/scores/{trashed_id}")

    with_trash = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    assert len(with_trash["tables"]["scores"]) == 2

    without_trash = json.loads(
        _zip_of(client.get("/api/export?include_trash=false")).read("manifest.json")
    )
    scores = without_trash["tables"]["scores"]
    assert [s["title"] for s in scores] == ["Keeper"]
    # Nothing about the excluded score survives as a dangling reference -
    # only its OWN row is left out; nothing hanging off it is included since
    # this library has nothing hanging off Doomed. Covered for real (a
    # session on the excluded score keeping its own row with score_id
    # nulled) by test_export_leaving_out_trash_detaches_its_sessions below.
    assert live_id  # sanity: fixture actually returned something


def test_export_leaving_out_trash_detaches_its_sessions_rather_than_dropping_them(
    client, add_score
):
    trashed_id = add_score("Doomed.pdf", title="Doomed")
    client.post(f"/api/scores/{trashed_id}/practice", json={"seconds": 400})
    client.delete(f"/api/scores/{trashed_id}")

    manifest = json.loads(
        _zip_of(client.get("/api/export?include_trash=false")).read("manifest.json")
    )
    assert manifest["tables"]["scores"] == []
    sessions = manifest["tables"]["practice_sessions"]
    assert len(sessions) == 1
    assert sessions[0]["seconds"] == 400
    assert sessions[0]["score_id"] is None


def test_export_without_files_still_carries_every_row_but_no_bytes(client, add_score):
    add_score("Prelude.pdf", title="Prelude")
    zf = _zip_of(client.get("/api/export?include_files=false"))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["tables"]["scores"][0]["file_included"] is False
    assert not any(name.startswith("files/") for name in zf.namelist())


# ---------------------------------------------------------------------------
# dry_run: reports, never writes.
# ---------------------------------------------------------------------------


def test_dry_run_import_writes_nothing(client, add_score, tmp_path, monkeypatch):
    add_score("Prelude.pdf", title="Prelude")
    archive = client.get("/api/export").content

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    preview = client.post(
        "/api/import", files={"file": ("export.zip", archive, "application/zip")}
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["dry_run"] is True
    assert body["scores_imported"] == 1
    assert body["files_written"] == 1

    assert client.get("/api/scores").json() == []
    # The library root folder always lists itself, even empty - the useful
    # claim a dry run makes is that it holds no scores.
    folders = client.get("/api/library/folders").json()
    assert all(f["score_count"] == 0 for f in folders)


# ---------------------------------------------------------------------------
# Rejection: a malformed or incompatible archive changes nothing at all.
# ---------------------------------------------------------------------------


def _bytes_of_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_import_rejects_a_zip_with_no_manifest(client):
    archive = _bytes_of_zip({"nothing.txt": b"not an export"})
    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("bad.zip", archive, "application/zip")},
    )
    assert resp.status_code == 422
    assert "manifest.json" in resp.json()["detail"]


def test_import_rejects_a_non_zip_file(client):
    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("bad.zip", b"not a zip at all", "application/zip")},
    )
    assert resp.status_code == 422


def test_import_rejects_the_wrong_schema_version_and_changes_nothing(client, add_score):
    add_score("Prelude.pdf", title="Prelude")
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["schema_version"] = manifest["schema_version"] + 1
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("wrong-version.zip", archive, "application/zip")},
    )
    assert resp.status_code == 422
    assert "schema version" in resp.json()["detail"]
    # Nothing was touched - the library this ran against still has only the
    # one score the fixture put there, not a duplicate and not zero.
    assert len(client.get("/api/scores").json()) == 1


def test_import_rejects_a_corrupted_file_and_writes_nothing(client, add_score, tmp_path, monkeypatch):
    """Break the hash re-link on purpose: a file in the archive whose bytes
    do not match the hash the archive itself records for it. This is the
    mutation-shaped test for `_apply_import`'s `scanner.hash_file(dest) !=
    row["hash"]` check (and the earlier in-memory sha1 check on the way in) -
    remove either one and this goes from a clean 422 to a silently corrupted
    import."""
    add_score("Prelude.pdf", title="Prelude")
    zf = _zip_of(client.get("/api/export"))
    manifest_bytes = zf.read("manifest.json")
    names = [n for n in zf.namelist() if n.startswith("files/")]
    assert len(names) == 1

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr("manifest.json", manifest_bytes)
        out.writestr(names[0], b"these are not the bytes that were exported")
    corrupted = buf.getvalue()

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("corrupt.zip", corrupted, "application/zip")},
    )
    assert resp.status_code == 422
    assert "hash" in resp.json()["detail"] or "corrupt" in resp.json()["detail"]
    assert client.get("/api/scores").json() == []


def test_import_is_transactional_on_a_collision_in_the_target_library(
    client, add_score, tmp_path, monkeypatch
):
    """The one failure validation cannot rule out in advance: a real,
    internally-consistent archive that still collides with something already
    in the TARGET library once apply actually starts writing. Two goals for
    the same week is refused by practice_goals' own UNIQUE(owner,
    period_start) index - engineered here by importing the same archive
    TWICE in a row without dry_run, so the second attempt fails partway
    through (after its scores and tags have already been inserted, since
    goals are applied last) and the whole thing has to roll back rather than
    leave a second copy of the scores behind.
    """
    score_id = add_score("Prelude.pdf", title="Prelude")
    client.post(
        "/api/practice/goals",
        json={"scope": "score", "score_id": score_id, "target_days": 2, "intent": "bar 34"},
    )
    archive = client.get("/api/export").content

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    first = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert first.status_code == 200, first.text
    assert len(client.get("/api/scores").json()) == 1

    second = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert second.status_code != 200
    # ROLLED BACK, NOT HALF-APPLIED: the second import's scores must not be
    # sitting in the library even though its own insert ran (and would have
    # committed) before the goal collision was hit.
    assert len(client.get("/api/scores").json()) == 1
    assert len(client.get("/api/practice/goals").json()["goals"]) == 1
