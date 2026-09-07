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
import re
import struct

import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fermata import api, db, instruments, practice, scanner
from fermata import trainer as trainer_module

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


# ---------------------------------------------------------------------------
# The guard #243 asks for: a table db.py's schema carries but this feature's
# hand-enumerated tuple does not is exactly the bug that shipped for
# trainer_attempts/trainer_chord_attempts (issue #243) - this derives the
# expected table set from the schema ITSELF, so a future table forgotten here
# fails this test rather than only being noticed by reading the tuple by eye.
# ---------------------------------------------------------------------------


def test_export_table_names_matches_every_table_the_schema_creates(client):
    """Every user table a freshly initialised database actually contains,
    read back from sqlite_master after `db.init_db()` has run the schema AND
    the migrations - not the schema's text. A text match would go blind to a
    table written without the exact `CREATE TABLE IF NOT EXISTS` phrasing or
    created by a migration, which is the same silent gap #243 was (two tables
    added to the schema, never to the export). The only exclusion is sqlite's
    own bookkeeping (`sqlite_sequence`, from AUTOINCREMENT), which is not
    user data. There is no allow-list of deliberately-not-portable tables; if
    a future table needs one, it belongs in a commented set subtracted here,
    not in a silent gap in EXPORT_TABLE_NAMES."""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        conn.close()
    live_tables = {row[0] for row in rows}
    # Sanity floor on the catalog read itself: fewer than this would mean the
    # query stopped seeing real tables, which would let this test pass for the
    # wrong reason (a tiny live_tables trivially failing to catch anything left
    # out of EXPORT_TABLE_NAMES).
    assert len(live_tables) >= 14
    assert live_tables == set(api.EXPORT_TABLE_NAMES)


# ---------------------------------------------------------------------------
# #243: drill history (trainer_attempts, trainer_chord_attempts) rides the
# same export/import round trip practice_sessions does.
# ---------------------------------------------------------------------------


def test_export_import_round_trip_carries_trainer_attempts(client, tmp_path, monkeypatch):
    """Log a row into both trainer tables, linked to a session that is NOT
    the first one this library has ever seen (a decoy session comes first),
    export, import into a fresh library that already has one session of its
    own before the import runs. If `session_id` travelled as the raw
    source-side id rather than through this import's own id remap, the
    imported attempt would end up pointing at the wrong session (or, if the
    raw id happens not to exist in the target at all, at a foreign-key
    violation) rather than at the session the archive actually meant - this
    checks the imported row points at the RIGHT session, not merely a valid
    one.
    """
    decoy_session = client.post(
        "/api/practice/sessions", json={"seconds": 60, "activity": "ear_training"}
    ).json()
    real_session = client.post(
        "/api/practice/sessions", json={"seconds": 120, "activity": "fretboard"}
    ).json()
    assert real_session["id"] != decoy_session["id"]

    attempt1 = client.post(
        "/api/trainer/attempts",
        json={
            "session_id": real_session["id"],
            "drill": "fret_to_note",
            "direction": "position_to_note",
            "target_string": 6,
            "target_fret": 3,
            "target_note": "G",
            "given_note": "G",
        },
    ).json()
    attempt2 = client.post(
        "/api/trainer/attempts",
        json={
            "session_id": real_session["id"],
            "drill": "fret_to_note",
            "direction": "position_to_note",
            "target_string": 1,
            "target_fret": 0,
            "target_note": "E",
            "given_note": "F",
        },
    ).json()
    chord_attempt = client.post(
        "/api/trainer/chord-attempts",
        json={
            "session_id": real_session["id"],
            "drill": "chord_flashcards",
            "direction": "shape_to_name",
            "target_root": "C",
            "target_quality": "major",
            "target_shape": [
                {"string": 5, "fret": 3},
                {"string": 4, "fret": 2},
                {"string": 3, "fret": 0},
                {"string": 2, "fret": 1},
                {"string": 1, "fret": 0},
            ],
            "given_root": "C",
            "given_quality": "major",
        },
    ).json()
    assert chord_attempt["correct"] is True

    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    # Logged order preserved in the archive.
    assert [r["id"] for r in manifest["tables"]["trainer_attempts"]] == [
        attempt1["id"], attempt2["id"],
    ]
    assert len(manifest["tables"]["trainer_chord_attempts"]) == 1

    archive = client.get("/api/export").content

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    # A session that already exists in the TARGET before import runs, so the
    # imported sessions cannot coincidentally land on the same ids they had
    # in the source - a broken remap that carried the raw source id across
    # would then point at THIS session, not the one the archive named.
    preexisting = client.post(
        "/api/practice/sessions", json={"seconds": 999, "activity": "ear_training"}
    ).json()

    import_resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert import_resp.status_code == 200, import_resp.text
    summary = import_resp.json()
    assert summary["trainer_attempts_imported"] == 2
    assert summary["trainer_chord_attempts_imported"] == 1

    target_sessions = client.get("/api/practice/sessions").json()["sessions"]
    imported_real_session = next(
        s for s in target_sessions if s["seconds"] == 120 and s["activity"] == "fretboard"
    )
    assert imported_real_session["id"] != preexisting["id"]
    assert imported_real_session["id"] != real_session["id"]  # a fresh id, not the source's

    new_attempts = sorted(
        client.get("/api/trainer/attempts").json()["attempts"], key=lambda a: a["target_note"]
    )
    assert [a["target_note"] for a in new_attempts] == ["E", "G"]
    for a in new_attempts:
        assert a["session_id"] == imported_real_session["id"]
        assert a["session_id"] != preexisting["id"]

    new_chord_attempts = client.get("/api/trainer/chord-attempts").json()["attempts"]
    assert len(new_chord_attempts) == 1
    assert new_chord_attempts[0]["target_root"] == "C"
    assert new_chord_attempts[0]["target_quality"] == "major"
    assert new_chord_attempts[0]["correct"] is True
    assert new_chord_attempts[0]["session_id"] == imported_real_session["id"]


def test_import_accepts_an_archive_from_before_the_trainer_tables_existed(client, add_score):
    """A manifest with a different set of keys under `tables` is refused
    rather than read partially - EXCEPT for the two keys #243 added,
    LEGACY_OPTIONAL_TABLES: an archive missing those entirely predates the
    tables, not malformed, and refusing to restore every backup taken before
    today would be strictly worse than importing one with an empty drill
    history. This engineers exactly that ten-table archive by deleting the
    two keys from an otherwise-real manifest, and asserts it still imports
    (mutation-tested: see the PR text for the red count when the tolerance
    for a missing key is removed)."""
    add_score("Prelude.pdf", title="Prelude")
    manifest = json.loads(
        _zip_of(client.get("/api/export?include_files=false")).read("manifest.json")
    )
    assert set(manifest["tables"]) == set(api.EXPORT_TABLE_NAMES)
    del manifest["tables"]["trainer_attempts"]
    del manifest["tables"]["trainer_chord_attempts"]
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("legacy-ten-table.zip", archive, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["trainer_attempts_imported"] == 0
    assert summary["trainer_chord_attempts_imported"] == 0
    assert client.get("/api/scores").json()[0]["title"] == "Prelude"
    assert client.get("/api/trainer/attempts").json()["total"] == 0
    assert client.get("/api/trainer/chord-attempts").json()["total"] == 0


# ---------------------------------------------------------------------------
# #236: named drill scopes and their string sets ride the same round trip,
# and practice_sessions.preset_id follows the id remap.
# ---------------------------------------------------------------------------


def test_export_import_round_trip_carries_presets_their_strings_and_the_sessions_reference(
    client, tmp_path, monkeypatch
):
    """Save two scopes, log a session under the SECOND one (so a broken remap
    that carried the raw source id across would land on the wrong preset
    rather than merely on a valid one), export, and import into a fresh
    library that already holds a preset and a session of its own - so the
    imported rows cannot coincidentally get the ids they had in the source.

    The strings are the half that would be silently lost: a preset row can
    arrive intact while its string set does not, and the restored scope would
    then narrow nothing while looking perfectly well-formed. So the assertion
    is on the string sets, per preset, by name.
    """
    decoy = client.post(
        "/api/trainer/presets",
        json={"name": "Open position", "start_fret": 0, "end_fret": 3, "strings": [6, 5, 4]},
    ).json()
    real = client.post(
        "/api/trainer/presets",
        json={
            "name": "Fifth position",
            "start_fret": 5,
            "end_fret": 9,
            "strings": [1, 2, 3],
            "key_root": "G",
            "key_quality": "minor",
        },
    ).json()
    assert real["id"] != decoy["id"]
    session = client.post(
        "/api/practice/sessions",
        json={"seconds": 120, "activity": "fretboard", "preset_id": real["id"]},
    ).json()
    assert session["preset_id"] == real["id"]

    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    assert [r["id"] for r in manifest["tables"]["trainer_scope_presets"]] == [
        decoy["id"], real["id"],
    ]
    # One row per string, never a list in a column.
    assert len(manifest["tables"]["trainer_scope_preset_strings"]) == 6

    archive = client.get("/api/export").content

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    preexisting = client.post(
        "/api/trainer/presets",
        json={"name": "Already here", "start_fret": 0, "end_fret": 12, "strings": [6]},
    ).json()
    client.post("/api/practice/sessions", json={"seconds": 999, "activity": "ear_training"})

    import_resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert import_resp.status_code == 200, import_resp.text
    summary = import_resp.json()
    assert summary["trainer_presets_imported"] == 2
    assert summary["trainer_preset_strings_imported"] == 6

    by_name = {p["name"]: p for p in client.get("/api/trainer/presets").json()}
    assert set(by_name) == {"Already here", "Open position", "Fifth position"}
    assert by_name["Open position"]["strings"] == [4, 5, 6]
    assert by_name["Fifth position"]["strings"] == [1, 2, 3]
    assert by_name["Fifth position"]["start_fret"] == 5
    assert by_name["Fifth position"]["end_fret"] == 9
    assert by_name["Fifth position"]["key_root"] == "G"
    assert by_name["Fifth position"]["key_quality"] == "minor"
    # Fresh ids on both sides of the join, and the session names the RIGHT one.
    imported_real = by_name["Fifth position"]
    assert imported_real["id"] not in {real["id"], preexisting["id"]}
    imported_session = next(
        s
        for s in client.get("/api/practice/sessions").json()["sessions"]
        if s["seconds"] == 120
    )
    assert imported_session["preset_id"] == imported_real["id"]
    assert imported_session["preset_id"] != by_name["Open position"]["id"]
    assert imported_session["preset_id"] != preexisting["id"]


def test_import_accepts_an_archive_from_before_named_scopes_existed(client, add_score):
    """The LEGACY_OPTIONAL_TABLES tolerance, extended to #236's two for the
    same reason #243's two have it: an archive taken yesterday cannot carry a
    table that did not exist, and refusing every backup a person already
    holds would be strictly worse than restoring one with no named scopes. A
    session in such an archive has no `preset_id` either, so nothing dangles.
    Engineered by deleting the two keys - and the column - from an
    otherwise-real manifest."""
    add_score("Prelude.pdf", title="Prelude")
    client.post("/api/practice/sessions", json={"seconds": 300, "activity": "fretboard"})
    manifest = json.loads(
        _zip_of(client.get("/api/export?include_files=false")).read("manifest.json")
    )
    assert set(manifest["tables"]) == set(api.EXPORT_TABLE_NAMES)
    del manifest["tables"]["trainer_scope_presets"]
    del manifest["tables"]["trainer_scope_preset_strings"]
    for row in manifest["tables"]["practice_sessions"]:
        del row["preset_id"]
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("legacy-twelve-table.zip", archive, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["trainer_presets_imported"] == 0
    assert summary["trainer_preset_strings_imported"] == 0
    assert summary["practice_sessions_imported"] == 1
    assert client.get("/api/scores").json()[0]["title"] == "Prelude"
    assert client.get("/api/trainer/presets").json() == []
    # Import ADDS, so the source session and its restored copy are both here;
    # what matters is that neither ended up naming a scope this archive never
    # carried.
    restored = client.get("/api/practice/sessions").json()["sessions"]
    assert [s["seconds"] for s in restored] == [300, 300]
    assert [s["preset_id"] for s in restored] == [None, None]


def test_an_archive_naming_a_preset_it_does_not_carry_is_refused_before_anything_is_written(
    client, add_score
):
    """Referential integrity WITHIN the archive, the same check every other
    reference gets: a session naming a preset the archive left out cannot be
    inserted without dropping the reference silently or crashing partway
    through write_tx()."""
    add_score("Prelude.pdf", title="Prelude")
    preset = client.post(
        "/api/trainer/presets",
        json={"name": "Fifth position", "start_fret": 5, "end_fret": 9, "strings": [1, 2]},
    ).json()
    client.post(
        "/api/practice/sessions",
        json={"seconds": 120, "activity": "fretboard", "preset_id": preset["id"]},
    )
    manifest = json.loads(
        _zip_of(client.get("/api/export?include_files=false")).read("manifest.json")
    )
    manifest["tables"]["trainer_scope_presets"] = []
    manifest["tables"]["trainer_scope_preset_strings"] = []
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("broken.zip", archive, "application/zip")},
    )
    assert resp.status_code == 422, resp.text
    assert "preset" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# #260: a same-named preset is renamed rather than refusing the whole
# archive. See the module comment above EXPORT_TABLE_NAMES ("THE ONE OTHER
# EXCEPTION") for why this, and only this, collision class gets this
# treatment - everything else still reaches the IntegrityError -> 409 path,
# covered below by test_import_is_transactional_on_a_collision_in_the_target_library
# (practice_goals' own UNIQUE(owner, period_start), the one other uniqueness
# rule this feature can trip that is NOT a preset).
# ---------------------------------------------------------------------------


def test_import_renames_a_preset_that_collides_with_one_already_in_the_target_library(
    client, tmp_path, monkeypatch
):
    """The headline claim: import keeps "added, never merged" even here. The
    colliding preset is imported anyway, under a derived name, with its
    string set intact, and the archive's own session is remapped to point at
    the IMPORTED copy - not at the pre-existing preset of the same name.
    Dry run has to report the identical rename apply mode actually makes.
    """
    real = client.post(
        "/api/trainer/presets",
        json={"name": "Fifth position", "start_fret": 5, "end_fret": 9, "strings": [1, 2, 3]},
    ).json()
    client.post(
        "/api/practice/sessions",
        json={"seconds": 120, "activity": "fretboard", "preset_id": real["id"]},
    )
    archive = client.get("/api/export").content

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    preexisting = client.post(
        "/api/trainer/presets",
        json={"name": "Fifth position", "start_fret": 0, "end_fret": 12, "strings": [6]},
    ).json()

    expected_rename = [{"from": "Fifth position", "to": "Fifth position (imported)"}]

    preview = client.post(
        "/api/import", files={"file": ("export.zip", archive, "application/zip")}
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["dry_run"] is True
    assert preview_body["trainer_scope_presets_renamed"] == expected_rename

    applied = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert applied.status_code == 200, applied.text
    applied_body = applied.json()
    assert applied_body["dry_run"] is False
    assert applied_body["trainer_scope_presets_renamed"] == expected_rename

    by_name = {p["name"]: p for p in client.get("/api/trainer/presets").json()}
    assert set(by_name) == {"Fifth position", "Fifth position (imported)"}
    imported = by_name["Fifth position (imported)"]
    assert imported["strings"] == [1, 2, 3]
    assert imported["start_fret"] == 5
    assert imported["end_fret"] == 9
    # The pre-existing preset of the same name is untouched.
    assert by_name["Fifth position"]["id"] == preexisting["id"]
    assert by_name["Fifth position"]["strings"] == [6]

    imported_session = next(
        s for s in client.get("/api/practice/sessions").json()["sessions"]
        if s["seconds"] == 120
    )
    assert imported_session["preset_id"] == imported["id"]
    assert imported_session["preset_id"] != preexisting["id"]


def test_a_second_import_of_the_same_archive_derives_imported_2(client, tmp_path, monkeypatch):
    """`_derive_preset_renames` re-reads the library each call, so importing
    the SAME archive a second time (a person restoring the same backup
    twice, say) sees its own first import's "(imported)" copy already sitting
    there and has to skip past it too."""
    client.post(
        "/api/trainer/presets",
        json={"name": "Fifth position", "start_fret": 5, "end_fret": 9, "strings": [1, 2, 3]},
    )
    archive = client.get("/api/export").content

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    client.post(
        "/api/trainer/presets",
        json={"name": "Fifth position", "start_fret": 0, "end_fret": 12, "strings": [6]},
    )

    first = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert first.status_code == 200, first.text
    assert first.json()["trainer_scope_presets_renamed"] == [
        {"from": "Fifth position", "to": "Fifth position (imported)"}
    ]

    second = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert second.status_code == 200, second.text
    assert second.json()["trainer_scope_presets_renamed"] == [
        {"from": "Fifth position", "to": "Fifth position (imported 2)"}
    ]
    names = {p["name"] for p in client.get("/api/trainer/presets").json()}
    assert names == {
        "Fifth position", "Fifth position (imported)", "Fifth position (imported 2)",
    }


def test_a_collision_differing_only_in_case_is_renamed(client, tmp_path, monkeypatch):
    """The unique index this whole feature exists to get past is
    `(owner, name COLLATE NOCASE)` - a rename decision that compared names
    case-sensitively would miss exactly this collision and let _apply_import
    crash into the IntegrityError -> 409 path instead of renaming."""
    client.post(
        "/api/trainer/presets",
        json={"name": "fifth position", "start_fret": 5, "end_fret": 9, "strings": [1, 2, 3]},
    )
    archive = client.get("/api/export").content

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    client.post(
        "/api/trainer/presets",
        json={"name": "FIFTH POSITION", "start_fret": 0, "end_fret": 12, "strings": [6]},
    )

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["trainer_scope_presets_renamed"] == [
        {"from": "fifth position", "to": "fifth position (imported)"}
    ]
    names = sorted(p["name"] for p in client.get("/api/trainer/presets").json())
    assert names == ["FIFTH POSITION", "fifth position (imported)"]


def test_two_identically_named_presets_within_one_archive_do_not_500(
    client, tmp_path, monkeypatch
):
    """Two presets sharing a name INSIDE THE SAME ARCHIVE - not constructible
    through this API (the (owner, name COLLATE NOCASE) index refuses it
    there) but nothing stops a hand-edited manifest, or a merge of two
    exports, from naming two. `_derive_preset_renames` tracks every name it
    has already handed out THIS import (not only what the library already
    held), so the second one still lands on a free name instead of raising
    an IntegrityError _apply_import has no except clause for."""
    client.post(
        "/api/trainer/presets",
        json={"name": "Alpha", "start_fret": 0, "end_fret": 4, "strings": [6, 5]},
    )
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    presets = manifest["tables"]["trainer_scope_presets"]
    strings = manifest["tables"]["trainer_scope_preset_strings"]
    assert len(presets) == 1
    original_id = presets[0]["id"]
    dup = dict(presets[0])
    dup["id"] = original_id + 1000
    presets.append(dup)
    for row in list(strings):
        if row["preset_id"] == original_id:
            strings.append({**row, "preset_id": dup["id"]})
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("dup-name.zip", archive, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["trainer_presets_imported"] == 2
    assert summary["trainer_scope_presets_renamed"] == [
        {"from": "Alpha", "to": "Alpha (imported)"}
    ]
    names = sorted(p["name"] for p in client.get("/api/trainer/presets").json())
    assert names == ["Alpha", "Alpha (imported)"]


def test_a_preset_row_whose_name_is_not_a_string_is_refused_by_the_normaliser(
    client, tmp_path, monkeypatch
):
    """Follow-up to #260, superseded by #268. Before #268, `_derive_preset_renames`
    passed a `name` that was null, missing entirely, or some other JSON type
    (nothing this API's own export ever writes, but nothing stops a
    hand-edited or foreign manifest from carrying one) through completely
    unexamined: null/missing reached SQLite's own NOT NULL constraint (409),
    and a stray integer was silently accepted (SQLite's TEXT affinity
    stringifies it).

    #268 runs every archived preset through trainer.normalise_preset BEFORE
    any of that - trainer._preset_name refuses anything that is not a real
    string outright (`isinstance(name, str)`), so all three shapes are now
    a clean 422, in both dry run and applied mode, naming the row's
    position in the archive and the normaliser's own reason, with nothing
    imported either way. The "weird, but not this fix's problem" integer
    case is now this fix's problem, and it is refused too."""
    client.post(
        "/api/trainer/presets",
        json={"name": "Untouched", "start_fret": 0, "end_fret": 4, "strings": [6]},
    )
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    template = manifest["tables"]["trainer_scope_presets"][0]

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")

    shapes = {
        "null": {**template, "name": None},
        "missing": {k: v for k, v in template.items() if k != "name"},
        "integer": {**template, "name": 42},
    }
    for shape, bad_row in shapes.items():
        bad_manifest = json.loads(json.dumps(manifest))  # deep copy - each shape is independent
        bad_manifest["tables"]["trainer_scope_presets"] = [bad_row]
        archive = _bytes_of_zip({"manifest.json": json.dumps(bad_manifest).encode()})

        preview = client.post(
            "/api/import", files={"file": (f"{shape}.zip", archive, "application/zip")},
        )
        assert preview.status_code == 422, f"{shape} dry run: {preview.text}"
        assert "needs a name" in preview.text, preview.text
        assert "trainer_scope_presets row 0" in preview.text, preview.text

        applied = client.post(
            "/api/import", params={"dry_run": "false"},
            files={"file": (f"{shape}.zip", archive, "application/zip")},
        )
        assert applied.status_code == 422, f"{shape} applied: {applied.text}"
        assert "needs a name" in applied.text, applied.text

    # A rejected import leaves nothing behind, whichever shape rejected it.
    assert client.get("/api/trainer/presets").json() == []


def test_a_cap_length_colliding_preset_name_is_trimmed_to_fit(client, tmp_path, monkeypatch):
    """#249's cap on a preset's own name (trainer.MAX_PRESET_NAME_CHARS) is an
    invariant `POST /api/trainer/presets` enforces on every name it accepts -
    a collision's derived name has to satisfy it too, or import would hand
    back a row `preset_dict` reports as 211 characters when this same
    library's own POST would refuse anything over 200. The base is trimmed
    so `<base> (imported)` still fits, and a second import of the very same
    archive still finds a free (still-fitting) name."""
    long_name = "A" * trainer_module.MAX_PRESET_NAME_CHARS
    assert len(long_name) == 200
    client.post(
        "/api/trainer/presets",
        json={"name": long_name, "start_fret": 5, "end_fret": 9, "strings": [1, 2, 3]},
    )
    archive = client.get("/api/export").content

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    client.post(
        "/api/trainer/presets",
        json={"name": long_name, "start_fret": 0, "end_fret": 12, "strings": [6]},
    )

    first = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("long.zip", archive, "application/zip")},
    )
    assert first.status_code == 200, first.text
    renamed = first.json()["trainer_scope_presets_renamed"]
    assert len(renamed) == 1
    assert renamed[0]["from"] == long_name
    assert len(renamed[0]["to"]) <= trainer_module.MAX_PRESET_NAME_CHARS
    assert renamed[0]["to"].endswith("(imported)")

    names = {p["name"] for p in client.get("/api/trainer/presets").json()}
    assert long_name in names
    assert renamed[0]["to"] in names
    assert all(len(n) <= trainer_module.MAX_PRESET_NAME_CHARS for n in names)

    second = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("long.zip", archive, "application/zip")},
    )
    assert second.status_code == 200, second.text
    second_renamed = second.json()["trainer_scope_presets_renamed"]
    assert len(second_renamed) == 1
    assert second_renamed[0]["from"] == long_name
    assert second_renamed[0]["to"] != renamed[0]["to"]
    assert len(second_renamed[0]["to"]) <= trainer_module.MAX_PRESET_NAME_CHARS
    names_after = {p["name"] for p in client.get("/api/trainer/presets").json()}
    assert len(names_after) == 3
    assert all(len(n) <= trainer_module.MAX_PRESET_NAME_CHARS for n in names_after)


def test_a_unicode_collision_nocase_would_not_actually_raise_on_is_not_renamed(
    client, tmp_path, monkeypatch
):
    """`(owner, name COLLATE NOCASE)` is SQLite's own NOCASE - ASCII A-Z only
    - not Python's str.casefold(), which also folds characters NOCASE does
    not: 'ß'.casefold() == 'ss', so a library preset named 'Straße' and an
    archived one named 'STRASSE' looked identical to casefold() even though
    the unique index those two would actually hit never collides on them.
    Renaming one anyway would be exactly backwards - a rename this feature
    exists to AVOID, on an archive the real insert would have accepted
    unchanged. 'Fifth'/'FIFTH' - plain ASCII case only - still has to
    collide, or the fix has gone too far the other way."""
    client.post(
        "/api/trainer/presets",
        json={"name": "STRASSE", "start_fret": 5, "end_fret": 9, "strings": [1, 2, 3]},
    )
    client.post(
        "/api/trainer/presets",
        json={"name": "Fifth", "start_fret": 0, "end_fret": 4, "strings": [1]},
    )
    archive = client.get("/api/export").content

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    client.post(
        "/api/trainer/presets",
        json={"name": "Straße", "start_fret": 0, "end_fret": 12, "strings": [6]},
    )
    client.post(
        "/api/trainer/presets",
        json={"name": "FIFTH", "start_fret": 0, "end_fret": 4, "strings": [2]},
    )

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    renamed = resp.json()["trainer_scope_presets_renamed"]
    assert renamed == [{"from": "Fifth", "to": "Fifth (imported)"}]
    names = sorted(p["name"] for p in client.get("/api/trainer/presets").json())
    assert names == ["FIFTH", "Fifth (imported)", "STRASSE", "Straße"]


# ---------------------------------------------------------------------------
# #268: every archived preset row (and its string set) goes through
# trainer.normalise_preset - the same call POST /api/trainer/presets makes -
# before any of #260's collision handling runs. A row the normaliser
# refuses refuses the WHOLE import (nothing applied, dry run or applied); a
# row it only cleans is imported under the cleaned name, which #260's
# collision check then runs against.
# ---------------------------------------------------------------------------


def test_a_5000_character_preset_name_is_refused_in_both_modes(client, tmp_path, monkeypatch):
    """The measured premise (#264's delta review): on main, a manifest whose
    preset name is 5000 characters imported with 200 and stored all 5000 -
    longer than `POST /api/trainer/presets` itself would ever accept
    (`TrainerPresetIn.name`'s own `Field(max_length=...)`). #268 refuses it
    instead, in both dry run and applied mode, with nothing imported."""
    client.post(
        "/api/trainer/presets",
        json={"name": "seed", "start_fret": 0, "end_fret": 4, "strings": [6]},
    )
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["trainer_scope_presets"][0]["name"] = "N" * 5000

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})

    preview = client.post(
        "/api/import", files={"file": ("evil.zip", archive, "application/zip")},
    )
    assert preview.status_code == 422, preview.text
    assert "trainer_scope_presets row 0" in preview.text, preview.text
    assert "200 characters" in preview.text, preview.text

    applied = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("evil.zip", archive, "application/zip")},
    )
    assert applied.status_code == 422, applied.text
    assert client.get("/api/trainer/presets").json() == []


def test_a_whitespace_padded_preset_name_imports_cleaned_and_collides_per_260(
    client, tmp_path, monkeypatch
):
    """A name #268's normaliser only CLEANS (surrounding whitespace, here) is
    not refused: it is imported under the cleaned name, and #260's collision
    check runs against THAT name - so a whitespace-padded archived name that
    happens to match an existing preset once cleaned is renamed exactly as
    any other collision would be, not inserted as a second "same" name with
    different spacing. The reported entry carries both facts: `from` is the
    name exactly as archived (with its whitespace), `to` is the #260-derived
    name, and `reason` is "collision" - not "cleaned" alone - since the
    cleaned name was ALSO already taken.

    The whitespace has to be put into the manifest by hand: POST
    /api/trainer/presets cleans a name on the way IN, so a real save of
    "  Fifth position  " would already be stored (and exported) as "Fifth
    position" - never reaching this function with whitespace still on it.
    A hand-edited or pre-#268 archive is exactly the source this bet is
    about (see the issue)."""
    client.post(
        "/api/trainer/presets",
        json={"name": "Fifth position", "start_fret": 5, "end_fret": 9, "strings": [1, 2, 3]},
    )
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["trainer_scope_presets"][0]["name"] = "  Fifth position  "

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    client.post(
        "/api/trainer/presets",
        json={"name": "Fifth position", "start_fret": 0, "end_fret": 12, "strings": [6]},
    )
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})

    preview = client.post(
        "/api/import", files={"file": ("export.zip", archive, "application/zip")},
    )
    assert preview.status_code == 200, preview.text
    expected = [
        {
            "from": "  Fifth position  ",
            "to": "Fifth position (imported)",
            "reason": "collision",
        }
    ]
    assert preview.json()["trainer_scope_presets_renamed"] == expected

    applied = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["trainer_scope_presets_renamed"] == expected

    names = sorted(p["name"] for p in client.get("/api/trainer/presets").json())
    assert names == ["Fifth position", "Fifth position (imported)"]


def test_a_whitespace_padded_preset_name_that_does_not_collide_is_reported_cleaned(
    client, tmp_path, monkeypatch
):
    """The other half of the reporting decision: a name #268 cleans that does
    NOT collide with anything still shows up in
    `trainer_scope_presets_renamed` (#260 never reported this case at all,
    since nothing collided) - `reason: "cleaned"`, `to` the cleaned name
    itself, no "(imported)" suffix. The whitespace is put into the manifest
    by hand for the same reason the collision test above does - POST
    /api/trainer/presets would have cleaned it before it was ever stored."""
    client.post(
        "/api/trainer/presets",
        json={"name": "Open position", "start_fret": 0, "end_fret": 4, "strings": [6]},
    )
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["trainer_scope_presets"][0]["name"] = "  Open position  "

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["trainer_scope_presets_renamed"] == [
        {"from": "  Open position  ", "to": "Open position", "reason": "cleaned"}
    ]
    names = [p["name"] for p in client.get("/api/trainer/presets").json()]
    assert names == ["Open position"]


def test_a_preset_fret_outside_bounds_is_refused(client, tmp_path, monkeypatch):
    """A fret outside MIN_FRET/MAX_FRET cannot be produced through
    `POST /api/trainer/presets` (trainer.normalise_preset itself refuses
    it) - only a hand-edited or pre-#268 archive could carry one. #268
    refuses it the same way, before anything is written."""
    client.post(
        "/api/trainer/presets",
        json={"name": "Valid", "start_fret": 0, "end_fret": 4, "strings": [6]},
    )
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["trainer_scope_presets"][0]["end_fret"] = 37

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})

    for dry_run in (True, False):
        resp = client.post(
            "/api/import", params={"dry_run": str(dry_run).lower()},
            files={"file": ("evil.zip", archive, "application/zip")},
        )
        assert resp.status_code == 422, f"dry_run={dry_run}: {resp.text}"
        assert "trainer_scope_presets row 0" in resp.text, resp.text
        assert "between 0 and 36" in resp.text, resp.text
    assert client.get("/api/trainer/presets").json() == []


def test_a_preset_with_an_empty_string_set_is_refused(client, tmp_path, monkeypatch):
    """A preset with no strings at all cannot be saved through the route
    either (trainer._preset_strings refuses an empty set - "every string" is
    spelled by naming every string, never by an empty list). Only an archive
    that dropped its trainer_scope_preset_strings rows for a preset - by
    hand, or from a source that never wrote them - could carry one."""
    client.post(
        "/api/trainer/presets",
        json={"name": "Valid", "start_fret": 0, "end_fret": 4, "strings": [6]},
    )
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["trainer_scope_preset_strings"] = []

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})

    for dry_run in (True, False):
        resp = client.post(
            "/api/import", params={"dry_run": str(dry_run).lower()},
            files={"file": ("evil.zip", archive, "application/zip")},
        )
        assert resp.status_code == 422, f"dry_run={dry_run}: {resp.text}"
        assert "at least one string" in resp.text, resp.text
    assert client.get("/api/trainer/presets").json() == []


def test_an_archived_duplicate_string_row_is_deduplicated_the_same_as_post(
    client, tmp_path, monkeypatch
):
    """A preset whose trainer_scope_preset_strings rows carry a duplicate
    (preset_id, string_number) pair - only ever possible in a hand-edited or
    foreign archive, never one this server wrote itself - passes
    _read_and_validate_manifest, since trainer.normalise_preset dedupes a
    string set before checking anything else about it. Before this fix,
    `_apply_import` then inserted the archive's raw rows verbatim, so the
    SAME duplicate that made dry run report 200 made the applied import
    insert the pair twice and hit trainer_scope_preset_strings' own
    UNIQUE(preset_id, string_number) - a 409 dry run never predicted. Now
    both modes agree, and the stored set is exactly what `POST
    /api/trainer/presets` stores for the same (deduplicated) input."""
    client.post(
        "/api/trainer/presets",
        json={"name": "Doubled strings", "start_fret": 0, "end_fret": 4, "strings": [1, 3]},
    )
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    string_rows = manifest["tables"]["trainer_scope_preset_strings"]
    assert sorted(r["string_number"] for r in string_rows) == [1, 3]
    preset_id = string_rows[0]["preset_id"]
    assert all(r["preset_id"] == preset_id for r in string_rows)
    # Duplicate the row naming string 1 - the archive now carries [1, 1, 3]
    # for this preset, exactly the shape trainer.normalise_preset dedupes.
    doubled_row = dict(next(r for r in string_rows if r["string_number"] == 1))
    string_rows.append(doubled_row)

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})

    preview = client.post(
        "/api/import", files={"file": ("export.zip", archive, "application/zip")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["trainer_scope_presets_renamed"] == []

    applied = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["trainer_scope_presets_renamed"] == []

    imported = client.get("/api/trainer/presets").json()
    assert len(imported) == 1
    assert imported[0]["strings"] == [1, 3]

    # The POST route given the same duplicated list stores the same set -
    # the divergence this test closes was import inserting the raw rows
    # while POST always deduped through the normaliser first.
    posted = client.post(
        "/api/trainer/presets",
        json={"name": "Doubled via post", "start_fret": 0, "end_fret": 4, "strings": [1, 1, 3]},
    )
    assert posted.status_code == 200, posted.text
    assert posted.json()["strings"] == [1, 3]


def test_a_preset_string_number_outside_bounds_is_refused(client, tmp_path, monkeypatch):
    """A string number outside MIN_STRING_NUMBER/MAX_STRING_NUMBER (1..24)
    cannot be saved through the route either - only a hand-edited or
    foreign archive could carry one."""
    client.post(
        "/api/trainer/presets",
        json={"name": "Valid", "start_fret": 0, "end_fret": 4, "strings": [6]},
    )
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["trainer_scope_preset_strings"][0]["string_number"] = 25

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})

    for dry_run in (True, False):
        resp = client.post(
            "/api/import", params={"dry_run": str(dry_run).lower()},
            files={"file": ("evil.zip", archive, "application/zip")},
        )
        assert resp.status_code == 422, f"dry_run={dry_run}: {resp.text}"
        assert "between 1 and 24" in resp.text, resp.text
    assert client.get("/api/trainer/presets").json() == []


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


def _corrupt_one_crc_byte(archive: bytes, member: str) -> bytes:
    """Flip one byte of `member`'s CRC-32 as recorded in the archive's
    central directory - #292's exact shape: the zip itself stays well-formed
    (still opens, still lists every name), but `ZipFile.read()` on that ONE
    member raises `zipfile.BadZipFile` partway through decompressing it,
    rather than the `KeyError` a missing name raises. That is a different
    exception than every other rejection test in this file provokes, and is
    the one the two `zf.read()` calls in api.py did not used to catch.

    Central directory file header layout (`PK\\x01\\x02`): the CRC-32 field
    sits 16 bytes past the signature - see APPNOTE.TXT section 4.3.12, or
    just count the fixed fields before it (sig, ver-made-by, ver-needed,
    flags, method, time, date, then crc)."""
    sig = b"PK\x01\x02"
    buf = bytearray(archive)
    idx = 0
    while True:
        idx = buf.find(sig, idx)
        if idx == -1:
            raise AssertionError(f"{member!r} not found in central directory")
        name_len = struct.unpack_from("<H", buf, idx + 28)[0]
        name = bytes(buf[idx + 46 : idx + 46 + name_len])
        if name == member.encode():
            crc_offset = idx + 16
            buf[crc_offset] ^= 0xFF
            return bytes(buf)
        idx += len(sig)


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


def test_import_rejects_a_corrupted_manifest_crc_in_both_modes(
    client, add_score, tmp_path, monkeypatch
):
    """#292: a real export whose manifest.json CRC-32 is off by one byte in
    the central directory used to reach `ZipFile.read()` unhandled -
    `zipfile.BadZipFile` isn't `KeyError`, so it fell through the manifest
    reader's existing `except KeyError` and reached the framework as a bare
    500 instead of this feature's usual unreadable-archive 422. Both dry run
    and applied import call `_read_and_validate_manifest` before either one
    opens a transaction, so one corrupted archive proves both."""
    add_score("Prelude.pdf", title="Prelude")
    archive = client.get("/api/export").content
    corrupted = _corrupt_one_crc_byte(archive, "manifest.json")

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")

    dry = client.post(
        "/api/import", params={"dry_run": "true"},
        files={"file": ("corrupt.zip", corrupted, "application/zip")},
    )
    assert dry.status_code == 422, dry.text
    assert "manifest.json" in dry.json()["detail"]

    applied = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("corrupt.zip", corrupted, "application/zip")},
    )
    assert applied.status_code == 422, applied.text
    assert "manifest.json" in applied.json()["detail"]
    assert client.get("/api/scores").json() == []


def test_import_rejects_a_corrupted_score_file_crc_in_both_modes(
    client, add_score, tmp_path, monkeypatch
):
    """Same shape as the manifest case above, but for the OTHER `zf.read()`
    this route makes: a score file's own bytes under `files/`, read inside
    the `_referenced_files` loop that runs identically before dry run and
    apply branch apart, so a `BadZipFile` there used to escape its own
    `except KeyError` the same way manifest.json's did."""
    add_score("Prelude.pdf", title="Prelude")
    archive = client.get("/api/export").content
    names = [n for n in zipfile.ZipFile(io.BytesIO(archive)).namelist() if n.startswith("files/")]
    assert len(names) == 1
    corrupted = _corrupt_one_crc_byte(archive, names[0])

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")

    dry = client.post(
        "/api/import", params={"dry_run": "true"},
        files={"file": ("corrupt.zip", corrupted, "application/zip")},
    )
    assert dry.status_code == 422, dry.text
    assert names[0] in dry.json()["detail"]

    applied = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("corrupt.zip", corrupted, "application/zip")},
    )
    assert applied.status_code == 422, applied.text
    assert names[0] in applied.json()["detail"]
    # The refusal happens in the pre-transaction file-collection loop, before
    # _apply_import ever opens write_tx() - so this is nothing inserted, not
    # merely something rolled back.
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

    Also the (d) case #260 asks for: a collision that is NOT a preset still
    has to reach the plain 409, with nothing applied - #260 only carves out
    an exception for trainer_scope_presets' own uniqueness rule, and this
    goal collision is the other uniqueness rule this feature can actually
    trip.
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
    assert second.status_code == 409, second.text
    # ROLLED BACK, NOT HALF-APPLIED: the second import's scores must not be
    # sitting in the library even though its own insert ran (and would have
    # committed) before the goal collision was hit.
    assert len(client.get("/api/scores").json()) == 1
    assert len(client.get("/api/practice/goals").json()["goals"]) == 1


# ---------------------------------------------------------------------------
# #275: an archive from an OLDER schema imports into this one, so the backup
# route docs/deployment.md recommends survives a schema bump. The exact-match
# check that used to sit here made every archive worthless the moment
# db.SCHEMA_VERSION moved.
#
# EVERY OLDER ARCHIVE IN THIS SECTION IS SYNTHETIC, AND HAS TO BE. Export
# landed on 2026-08-31, three days AFTER db.SCHEMA_VERSION last moved (to 5),
# so no released Fermata has ever written a manifest stamped 4 or lower and
# there is no real one to copy - see `_as_written_by_schema`, which derives
# one from a real export by removing exactly the columns that arrived after
# the version being simulated, rather than hand-copying rows.
# ---------------------------------------------------------------------------


# Which db.COLUMN_ADDITIONS entries had NOT arrived yet at a given schema
# version - and so which keys a manifest written at that version could not
# have carried. Read off db.py's own history: instrument_id came with 1,
# missing_since with 4, deleted_at/deleted_from with the 4 -> 5 step (#56),
# and key/tempo/difficulty (#8) plus practice_sessions.preset_id (#236) came
# after 5 was already stamped, so no pre-5 archive can hold any of them.
_ABSENT_BELOW_5 = {
    "scores": ("deleted_at", "deleted_from", "key", "tempo", "difficulty"),
    "practice_sessions": ("preset_id",),
}
_ABSENT_BELOW_4 = {"scores": ("missing_since",)}


def _as_written_by_schema(manifest: dict, version: int) -> dict:
    """A real export's manifest, reduced to what a Fermata at `version` could
    have written: the stamp changed, every column that arrived later removed
    from every row, and the tables that arrived later dropped outright (which
    is exactly what api.LEGACY_OPTIONAL_TABLES already tolerates). Derived
    from the live export rather than typed out, so a column added tomorrow
    travels through here the same way it travels through _dump_table."""
    out = json.loads(json.dumps(manifest))
    out["schema_version"] = version
    absent = dict(_ABSENT_BELOW_5)
    if version < 4:
        for table, columns in _ABSENT_BELOW_4.items():
            absent[table] = absent.get(table, ()) + columns
    if version < 5:
        for table, columns in absent.items():
            for row in out["tables"][table]:
                for column in columns:
                    row.pop(column, None)
        for table in api.LEGACY_OPTIONAL_TABLES:
            out["tables"].pop(table, None)
    return out


def _archive_of(manifest: dict) -> bytes:
    return _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})


def _exported_manifest(client) -> dict:
    return json.loads(
        _zip_of(client.get("/api/export?include_files=false")).read("manifest.json")
    )


def test_an_archive_from_the_schema_before_this_one_imports(client, add_score):
    """The headline of #275. A manifest stamped 4 - no `deleted_at`, no
    `deleted_from`, no `preset_id` on a session, and none of the four tables
    that arrived after 5 - lands in a version 5 database, with the columns it
    does not carry taking their schema defaults rather than being demanded of
    it. `schema_version_read` is what says which version was actually read;
    `schema_version` is the one the rows now live under."""
    add_score("Prelude.pdf", title="Prelude")
    client.post("/api/practice/sessions", json={"seconds": 300, "activity": "fretboard"})
    older = _as_written_by_schema(_exported_manifest(client), 4)
    assert "deleted_at" not in older["tables"]["scores"][0]
    assert "preset_id" not in older["tables"]["practice_sessions"][0]

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("v4.zip", _archive_of(older), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["schema_version_read"] == 4
    assert summary["schema_version"] == db.SCHEMA_VERSION
    assert summary["scores_imported"] == 1
    assert summary["scores_trashed_imported"] == 0
    assert summary["practice_sessions_imported"] == 1
    assert summary["trainer_presets_imported"] == 0

    # Import ADDS, so the source rows and the restored ones are both here.
    titles = sorted(s["title"] for s in client.get("/api/scores").json())
    assert titles == ["Prelude", "Prelude"]
    restored = client.get("/api/practice/sessions").json()["sessions"]
    assert [s["seconds"] for s in restored] == [300, 300]
    # The column the archive could not carry filled from the schema, not from
    # a guess - NULL is what "practised under no named scope" already means.
    assert [s["preset_id"] for s in restored] == [None, None]


def test_a_dry_run_of_an_older_archive_reports_the_version_it_read(client, add_score):
    """The dry run has to say the same thing the applied import does, or the
    preview is worthless - `schema_version_read` included."""
    add_score("Prelude.pdf", title="Prelude")
    older = _as_written_by_schema(_exported_manifest(client), 4)

    resp = client.post(
        "/api/import", params={"dry_run": "true"},
        files={"file": ("v4.zip", _archive_of(older), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["dry_run"] is True
    assert summary["schema_version_read"] == 4
    assert summary["schema_version"] == db.SCHEMA_VERSION
    # A dry run writes nothing, so the library still holds only the fixture's
    # own score.
    assert len(client.get("/api/scores").json()) == 1


def test_the_oldest_accepted_schema_version_still_imports(client, add_score):
    """api.OLDEST_IMPORTABLE_SCHEMA_VERSION is an accepted version, not the
    first refused one - the boundary is checked from both sides here and in
    the test below it."""
    add_score("Prelude.pdf", title="Prelude")
    oldest = api.OLDEST_IMPORTABLE_SCHEMA_VERSION
    older = _as_written_by_schema(_exported_manifest(client), oldest)
    assert "missing_since" not in older["tables"]["scores"][0]

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("oldest.zip", _archive_of(older), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["schema_version_read"] == oldest
    assert len(client.get("/api/scores").json()) == 2


def test_an_archive_from_before_the_practice_repair_is_refused_and_changes_nothing(
    client, add_score
):
    """One below the floor. A database stamped 2 has not been through
    db.MIGRATIONS' step 3, so an archive taken from one may carry a practice
    row naming a score that is gone - which that step repairs and this import
    path cannot. Refused with a message that says so, rather than accepted
    and then rejected downstream for a missing score."""
    add_score("Prelude.pdf", title="Prelude")
    too_old = _as_written_by_schema(
        _exported_manifest(client), api.OLDEST_IMPORTABLE_SCHEMA_VERSION - 1
    )

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("too-old.zip", _archive_of(too_old), "application/zip")},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert str(api.OLDEST_IMPORTABLE_SCHEMA_VERSION) in detail
    assert "practice" in detail
    assert len(client.get("/api/scores").json()) == 1


def test_an_archive_from_a_newer_fermata_is_refused_and_says_to_upgrade(client, add_score):
    """The direction that stays refused, and the one the old exact-match
    check was actually right about: a newer archive's rows may carry columns
    and meanings this code knows nothing about."""
    add_score("Prelude.pdf", title="Prelude")
    manifest = _exported_manifest(client)
    manifest["schema_version"] = db.SCHEMA_VERSION + 1

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("newer.zip", _archive_of(manifest), "application/zip")},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "newer Fermata" in detail
    assert "upgrade first" in detail
    assert len(client.get("/api/scores").json()) == 1


def test_an_archive_carrying_a_column_this_fermata_no_longer_has_is_refused_by_name(
    client, add_score
):
    """The other half of accepting an older archive: a column that has since
    been renamed or dropped cannot be inserted, and _insert_row names the
    archive's own keys - so an unknown one would be an OperationalError in
    the middle of write_tx() rather than a message anybody can act on. Named
    here, before anything is written. Engineered by adding a column no
    schema has ever had to an otherwise-importable version 4 manifest."""
    add_score("Prelude.pdf", title="Prelude")
    older = _as_written_by_schema(_exported_manifest(client), 4)
    older["tables"]["scores"][0]["binding_colour"] = "green"

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("stray-column.zip", _archive_of(older), "application/zip")},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "binding_colour" in detail
    assert "scores" in detail
    assert len(client.get("/api/scores").json()) == 1


def test_an_archive_carrying_a_table_this_fermata_no_longer_has_is_refused_by_name(
    client, add_score
):
    """The table-level form of the same rule. A key under `tables` that this
    Fermata has no table for is refused, and the message NAMES it - which is
    the whole of what somebody restoring an old archive needs to know."""
    add_score("Prelude.pdf", title="Prelude")
    older = _as_written_by_schema(_exported_manifest(client), 4)
    older["tables"]["practice_diaries"] = []

    resp = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("stray-table.zip", _archive_of(older), "application/zip")},
    )
    assert resp.status_code == 422, resp.text
    assert "practice_diaries" in resp.json()["detail"]
    assert len(client.get("/api/scores").json()) == 1


# ---------------------------------------------------------------------------
# #286: every table that has a normaliser is run through it on the way in.
#
# #268 did this for named drill scopes and said outright that it did presets
# only. That left the archive - the documented migration path onto the server
# stack, and a JSON file anybody can open in an editor - as the one way into
# this database that skipped the rules every POST route applies. Measured on
# 0279b50 before this section existed: a manifest whose practice_sessions row
# carried `seconds: -30` and whose trainer_attempts row carried
# `target_fret: 99` imported with 200 in BOTH modes and stored both values
# verbatim, while POST /api/practice/sessions and POST /api/trainer/attempts
# refused those same two values with 422.
#
# Each table below gets the same pair of tests: a REFUSED row (4xx, nothing
# applied, dry run and applied identical) and a CLEANED row (imported as the
# value POST would have stored, and counted in `ImportOut.cleaned`).
# ---------------------------------------------------------------------------


def _target_library_is_untouched(client):
    """Every table this feature writes, read back through the API, still
    empty. Asserted after each refusal rather than trusting the status code:
    a clean 422 that still left one row behind is exactly the bug this
    feature exists not to have (see this module's own docstring)."""
    assert client.get("/api/scores").json() == []
    assert client.get("/api/instruments").json() == []
    assert client.get("/api/practice/sessions").json()["sessions"] == []
    assert client.get("/api/practice/goals").json()["goals"] == []
    assert client.get("/api/trainer/attempts").json()["attempts"] == []
    assert client.get("/api/trainer/chord-attempts").json()["attempts"] == []
    assert client.get("/api/trainer/presets").json() == []
    assert client.get("/api/tags").json() == []
    assert client.get("/api/setlists").json() == []


def _refused_in_both_modes(client, manifest, *fragments):
    """Post one hand-edited manifest as a dry run AND as an applied import,
    and require the two answers to be identical - the parity half of the
    promise, which is the whole point of validating before a transaction is
    ever opened. `fragments` are substrings the message has to carry: the
    table and row position, and the normaliser's own reason."""
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})
    answers = []
    for dry_run in (True, False):
        resp = client.post(
            "/api/import", params={"dry_run": str(dry_run).lower()},
            files={"file": ("hand-edited.zip", archive, "application/zip")},
        )
        assert resp.status_code == 422, f"dry_run={dry_run}: {resp.text}"
        for fragment in fragments:
            assert fragment in resp.text, f"dry_run={dry_run}: {resp.text}"
        answers.append((resp.status_code, resp.json()))
    assert answers[0] == answers[1]
    _target_library_is_untouched(client)


def _imported_in_both_modes(client, manifest, expected_cleaned):
    """The other side: an archive whose rows the normaliser only CLEANS is
    imported, and both modes report the same `cleaned` map. Returns the
    applied import's own response body."""
    archive = _bytes_of_zip({"manifest.json": json.dumps(manifest).encode()})
    preview = client.post(
        "/api/import", files={"file": ("hand-edited.zip", archive, "application/zip")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["cleaned"] == expected_cleaned
    _target_library_is_untouched(client)

    applied = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("hand-edited.zip", archive, "application/zip")},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["cleaned"] == expected_cleaned
    return applied.json()


def _an_instrument(client):
    resp = client.post(
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
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _a_chord_attempt(client, **overrides):
    body = {
        "drill": "chord_flashcards",
        "direction": "shape_to_name",
        "target_root": "C",
        "target_quality": "major",
        "target_shape": [
            {"string": 5, "fret": 3},
            {"string": 4, "fret": 2},
            {"string": 3, "fret": 0},
            {"string": 2, "fret": 1},
            {"string": 1, "fret": 0},
        ],
        "given_root": "C",
        "given_quality": "major",
    }
    body.update(overrides)
    resp = client.post("/api/trainer/chord-attempts", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_an_archived_instrument_with_too_few_string_pitches_is_refused(
    client, tmp_path, monkeypatch
):
    """A six-string instrument whose tuning names no strings at all. POST
    /api/instruments cannot produce one (instruments.normalise counts the
    pitches against string_count), so only a hand-edited archive carries it -
    and stored, it is an instrument whose neck cannot be drawn."""
    _an_instrument(client)
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    # The column holds JSON text, which is what the manifest carries.
    manifest["tables"]["instruments"][0]["string_pitches"] = "[]"

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _refused_in_both_modes(
        client, manifest, "instruments row 0", "0 string pitch(es) were given"
    )


def test_an_archived_instrument_with_a_wrong_type_field_names_it_in_the_reason(
    client, tmp_path, monkeypatch
):
    """The measured premise: `instruments.normalise` compares string_count,
    fret_count, capo and reference_pitch against their bounds without first
    checking they are numbers, which POST /api/instruments never has to
    worry about (pydantic settles the type first) but an archive can carry
    anything. On 0279b50 the 422 for a string `string_count` was Python's own
    `'<=' not supported between instances of 'int' and 'str'` - this asserts
    a sentence a person could act on instead."""
    _an_instrument(client)
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["instruments"][0]["string_count"] = "6"

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _refused_in_both_modes(
        client, manifest, "instruments row 0", "string_count must be a whole number"
    )


def test_an_archived_instrument_is_imported_with_its_name_and_pitches_cleaned(
    client, tmp_path, monkeypatch
):
    """The cleaned half. A name with doubled spaces and pitch names in
    lowercase are both things instruments.normalise tidies rather than
    refuses - so the row is imported as POST would have stored it ("E2", not
    "e2": storing the typed spelling is what makes a later comparison against
    a preset or a tuning miss), and the ONE row it changed is counted."""
    _an_instrument(client)
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    row = manifest["tables"]["instruments"][0]
    row["name"] = "  Parlour   guitar  "
    row["string_pitches"] = json.dumps(["e2", "a2", "d3", "g3", "b3", "e4"])

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _imported_in_both_modes(client, manifest, {"instruments": 1})

    imported = client.get("/api/instruments").json()
    assert len(imported) == 1
    assert imported[0]["name"] == "Parlour guitar"
    assert imported[0]["string_pitches"] == ["E2", "A2", "D3", "G3", "B3", "E4"]


def test_an_archived_setlist_named_only_whitespace_is_refused(
    client, tmp_path, monkeypatch
):
    """A setlist's name is the only rule it has, and `_clean_setlist_name` -
    which both POST /api/setlists and the rename endpoint apply - refuses a
    name that is nothing but whitespace rather than storing a blank. An
    archived one would otherwise restore as an unnamed entry in a list of
    named arrangements."""
    created = client.post("/api/setlists", json={"name": "Recital order"})
    assert created.status_code == 200, created.text
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["setlists"][0]["name"] = "   "

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _refused_in_both_modes(client, manifest, "setlists row 0", "a setlist needs a name")


def test_an_archived_setlist_name_is_imported_with_its_whitespace_collapsed(
    client, tmp_path, monkeypatch
):
    """And the cleaned half: doubled spaces and padded ends are collapsed and
    trimmed, exactly as the route would have stored the same name, with the
    one row it changed counted."""
    created = client.post("/api/setlists", json={"name": "Recital order"})
    assert created.status_code == 200, created.text
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["setlists"][0]["name"] = "  Recital   order  "

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _imported_in_both_modes(client, manifest, {"setlists": 1})

    setlists = client.get("/api/setlists").json()
    assert [s["name"] for s in setlists] == ["Recital order"]


def test_an_archived_session_with_a_negative_duration_is_refused(
    client, tmp_path, monkeypatch
):
    """The issue's own example, and the measured premise: on 0279b50 this
    archive imported with 200 in both modes and stored `seconds: -30`, a
    duration POST /api/practice/sessions refuses with 422 - practice time
    that ran backwards, counted into every total this library reports."""
    client.post("/api/practice/sessions", json={"seconds": 600, "activity": "technique"})
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["practice_sessions"][0]["seconds"] = -30

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _refused_in_both_modes(
        client, manifest, "practice_sessions row 0", "seconds must be between 1 and 86400"
    )


def test_an_archived_session_note_is_imported_trimmed_and_counted(
    client, tmp_path, monkeypatch
):
    """A note that is nothing but padding around a sentence. practice's
    `_optional_text` trims it (and stores a note that is ONLY whitespace as
    NULL, so "nothing was written" is one value rather than two), which is a
    cleaning, not a refusal."""
    client.post(
        "/api/practice/sessions",
        json={"seconds": 600, "activity": "technique", "note": "bar 12 still rushes"},
    )
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["practice_sessions"][0]["note"] = "   bar 12 still rushes   "

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _imported_in_both_modes(client, manifest, {"practice_sessions": 1})

    sessions = client.get("/api/practice/sessions").json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["note"] == "bar 12 still rushes"
    assert sessions[0]["seconds"] == 600


def test_an_archived_session_dated_into_the_future_is_still_refused(
    client, tmp_path, monkeypatch
):
    """The measured premise behind #290's fix: `check_day_window=False` used
    to gate BOTH bounds on local_date, so exempting import from the
    backdating floor silently exempted it from "local_date is in the
    future" too. An archive is still a claim that each session happened on
    the date it names - restoring one should not be a way to log practice
    for a day that has not happened, which posting the same date to
    /api/practice/sessions still refuses. Nothing is applied in either
    mode."""
    client.post("/api/practice/sessions", json={"seconds": 600, "activity": "technique"})
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["practice_sessions"][0]["local_date"] = "2099-01-01"

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _refused_in_both_modes(
        client, manifest, "practice_sessions row 0", "local_date is in the future"
    )


def test_an_archived_session_older_than_the_backdating_window_still_imports(
    client, tmp_path, monkeypatch
):
    """The one rule this section deliberately does NOT apply to an archive.
    How far back a practice day may sit from today bounds what somebody may
    CLAIM now (practice.MAX_BACKDATE_DAYS); applied to an already-stored
    date it would make every backup older than that unrestorable, which is
    the opposite of what an archive is for. patch_session already turns the
    same check off when the date is not what is being written, and import
    passes the flag the same way - so a session from years ago imports
    untouched and is not counted as cleaned either."""
    client.post("/api/practice/sessions", json={"seconds": 600, "activity": "technique"})
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["practice_sessions"][0]["local_date"] = "2019-04-01"

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _imported_in_both_modes(client, manifest, {})

    sessions = client.get("/api/practice/sessions").json()["sessions"]
    assert [s["local_date"] for s in sessions] == ["2019-04-01"]


def test_an_archived_goal_with_no_target_at_all_is_refused(client, tmp_path, monkeypatch):
    """A goal has to be concrete enough to be either met or missed, which is
    the whole point of setting one - practice.normalise_goal requires at
    least one of days or minutes. A goal with neither cannot be created
    through POST /api/practice/goals and cannot be counted by anything that
    reads it back."""
    resp = client.post("/api/practice/goals", json={"target_days": 3})
    assert resp.status_code == 200, resp.text
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["practice_goals"][0]["target_days"] = None
    manifest["tables"]["practice_goals"][0]["target_minutes"] = None

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _refused_in_both_modes(client, manifest, "practice_goals row 0", "a goal needs a target")


def test_an_archived_goals_period_end_is_recomputed_from_its_own_start(
    client, tmp_path, monkeypatch
):
    """`period_end` is derived, never stated: normalise_goal computes it from
    the start and the period's length, which is why POST cannot be given one.
    An archive CAN carry one - and one that disagrees with its own start
    describes a week of some other number of days, which every query that
    counts practice into a period would then read as truth. Corrected on the
    way in, and counted."""
    resp = client.post("/api/practice/goals", json={"target_days": 3})
    assert resp.status_code == 200, resp.text
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    goal_row = manifest["tables"]["practice_goals"][0]
    real_end = goal_row["period_end"]
    goal_row["period_end"] = "2099-12-31"

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _imported_in_both_modes(client, manifest, {"practice_goals": 1})

    goals = client.get("/api/practice/goals").json()["goals"]
    assert len(goals) == 1
    assert goals[0]["period_end"] == real_end


def test_an_archived_attempt_at_a_fret_outside_the_bounds_is_refused(
    client, tmp_path, monkeypatch
):
    """The issue's second example, and the other half of the measured
    premise: on 0279b50 an archive carrying `target_fret: 99` imported with
    200 in both modes and stored it, while POST /api/trainer/attempts refuses
    the same value with 422. A position no instrument this app accepts could
    have is not a hard question about drills - it is a row that makes "which
    positions get missed" answer with a fret nobody ever played."""
    client.post(
        "/api/trainer/attempts",
        json={
            "drill": "fret_to_note", "direction": "position_to_note",
            "target_string": 6, "target_fret": 3, "target_note": "G", "given_note": "G",
        },
    )
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["trainer_attempts"][0]["target_fret"] = 99

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _refused_in_both_modes(
        client, manifest, "trainer_attempts row 0", "target_fret must be between 0 and 36"
    )


def test_an_archived_attempts_verdict_is_recomputed_from_the_notes_it_records(
    client, tmp_path, monkeypatch
):
    """`correct` is computed by trainer.normalise_attempt and is NEVER
    accepted from a caller - TrainerAttemptIn has no such field at all. An
    archive carries the column, so a hand-edited one can claim a verdict its
    own target/given notes do not support: here, a wrong answer marked
    correct. Recomputed on the way in from the two notes, which is the one
    thing this table exists to be queried on, and counted as cleaned."""
    client.post(
        "/api/trainer/attempts",
        json={
            "drill": "fret_to_note", "direction": "position_to_note",
            "target_string": 1, "target_fret": 0, "target_note": "E", "given_note": "F",
        },
    )
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    assert manifest["tables"]["trainer_attempts"][0]["correct"] == 0
    manifest["tables"]["trainer_attempts"][0]["correct"] = 1

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _imported_in_both_modes(client, manifest, {"trainer_attempts": 1})

    attempts = client.get("/api/trainer/attempts").json()["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["target_note"] == "E" and attempts[0]["given_note"] == "F"
    assert attempts[0]["correct"] is False


def test_an_archived_chord_attempt_with_a_shape_outside_the_bounds_is_refused(
    client, tmp_path, monkeypatch
):
    """The same bounds a single position is held to, applied to every
    position in a shown fingering (trainer._shape). A shape reaching fret 99
    cannot be posted and cannot be drawn."""
    _a_chord_attempt(client)
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    row = manifest["tables"]["trainer_chord_attempts"][0]
    shape = json.loads(row["target_shape"])
    shape[0]["fret"] = 99
    row["target_shape"] = json.dumps(shape)

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _refused_in_both_modes(
        client, manifest,
        "trainer_chord_attempts row 0", "target_shape fret must be between 0 and 36",
    )


def test_an_archived_chord_attempts_verdict_is_recomputed_from_its_tone_sets(
    client, tmp_path, monkeypatch
):
    """The chord drill's own form of the rule above: `correct` is decided by
    comparing TONE SETS, in trainer.normalise_chord_attempt, and is never
    accepted from a caller. A C major shape answered "A minor" is wrong; an
    archive claiming otherwise is corrected, not carried."""
    attempt = _a_chord_attempt(client, given_root="A", given_quality="minor")
    assert attempt["correct"] is False
    manifest = json.loads(_zip_of(client.get("/api/export")).read("manifest.json"))
    manifest["tables"]["trainer_chord_attempts"][0]["correct"] = 1

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    _imported_in_both_modes(client, manifest, {"trainer_chord_attempts": 1})

    attempts = client.get("/api/trainer/chord-attempts").json()["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["correct"] is False
    assert attempts[0]["given_root"] == "A" and attempts[0]["given_quality"] == "minor"


def test_a_real_export_of_every_validated_table_round_trips_with_nothing_cleaned(
    client, add_score, tmp_path, monkeypatch
):
    """The guard on the whole section: everything above rewrites values on
    the way in, and a rule applied a little too eagerly would show up here as
    a real export coming back changed. One row in each of the five tables
    #286 validates (plus the preset #268 already did), written through the
    API itself, exported and imported - `cleaned` is empty in BOTH modes,
    which is the claim that normalising an archive Fermata itself wrote is a
    no-op, and every value is read back identical."""
    score_id = add_score("Classical/Prelude.pdf", title="Prelude")
    _an_instrument(client)
    client.post("/api/setlists", json={"name": "Recital order"})
    client.post(
        "/api/trainer/presets",
        json={"name": "Fifth position", "start_fret": 5, "end_fret": 9, "strings": [1, 2, 3]},
    )
    session = client.post(
        f"/api/scores/{score_id}/practice",
        json={"seconds": 900, "tempo_bpm": 88, "mode": "section", "rating": 4,
              "note": "bar 12 still rushes"},
    ).json()["session"]
    client.post(
        "/api/practice/goals",
        json={"scope": "score", "score_id": score_id, "target_days": 3,
              "intent": "clean at full tempo"},
    )
    client.post(
        "/api/trainer/attempts",
        json={
            "session_id": session["id"], "drill": "fret_to_note",
            "direction": "position_to_note", "target_string": 6, "target_fret": 3,
            "target_note": "G", "given_note": "G",
        },
    )
    _a_chord_attempt(client, session_id=session["id"])

    expected_instruments = client.get("/api/instruments").json()
    expected_sessions = client.get("/api/practice/sessions").json()["sessions"]
    expected_goals = client.get("/api/practice/goals").json()["goals"]
    expected_attempts = client.get("/api/trainer/attempts").json()["attempts"]
    expected_chords = client.get("/api/trainer/chord-attempts").json()["attempts"]
    expected_setlists = [s["name"] for s in client.get("/api/setlists").json()]
    archive = client.get("/api/export").content

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    preview = client.post(
        "/api/import", files={"file": ("export.zip", archive, "application/zip")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["cleaned"] == {}

    applied = client.post(
        "/api/import", params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["cleaned"] == {}

    def _without_ids(rows, *drop):
        return [
            {k: v for k, v in row.items() if k not in ("id", "session_id", "score_id", *drop)}
            for row in rows
        ]

    assert _without_ids(client.get("/api/instruments").json()) == _without_ids(
        expected_instruments
    )
    assert _without_ids(
        client.get("/api/practice/sessions").json()["sessions"]
    ) == _without_ids(expected_sessions)
    assert _without_ids(client.get("/api/practice/goals").json()["goals"]) == _without_ids(
        expected_goals
    )
    assert _without_ids(
        client.get("/api/trainer/attempts").json()["attempts"], "created_at"
    ) == _without_ids(expected_attempts, "created_at")
    assert _without_ids(
        client.get("/api/trainer/chord-attempts").json()["attempts"], "created_at"
    ) == _without_ids(expected_chords, "created_at")
    assert [s["name"] for s in client.get("/api/setlists").json()] == expected_setlists


def test_one_definition_per_rule_and_both_the_route_and_import_run_it(
    client, tmp_path, monkeypatch
):
    """The contract this bet actually rests on: a rule has ONE definition,
    and the import path and the POST route both call THAT one - not a second
    copy that can drift from it. Checked two ways, because neither alone is
    enough. First by source: each rule's `def` appears exactly once in the
    package, so `grep -n "def normalise_"` shows one definition per rule.
    Then by behaviour: each definition is replaced with a spy, and both a
    POST and an import are made - a copy of the rule anywhere would leave one
    of the two callers unrecorded."""
    package = Path(api.__file__).resolve().parent
    sources = {p.name: p.read_text(encoding="utf-8") for p in package.glob("*.py")}
    # (module, attribute) for every rule import now shares with a route.
    rules = (
        (instruments, "normalise"),
        (practice, "normalise_session"),
        (practice, "normalise_goal"),
        (trainer_module, "normalise_attempt"),
        (trainer_module, "normalise_chord_attempt"),
        (trainer_module, "normalise_preset"),
        # A setlist's name is its only rule, and it lives among the routes
        # rather than in a domain module - three lines with nothing else to
        # keep them company (see its own docstring). Import calls that one,
        # not a second copy of the same three lines.
        (api, "_clean_setlist_name"),
    )
    for module, name in rules:
        definitions = sum(
            len(re.findall(rf"^def {name}\(", text, re.MULTILINE))
            for text in sources.values()
        )
        assert definitions == 1, f"{name} is defined {definitions} times, not once"
        # And the one definition lives in the module the route imports it
        # from, rather than being re-exported from somewhere else.
        assert re.search(
            rf"^def {name}\(", sources[Path(module.__file__).name], re.MULTILINE
        ), f"{name} is not defined in {Path(module.__file__).name}"

    # --- A source library with one row per validated table. ---
    _an_instrument(client)
    client.post("/api/setlists", json={"name": "Recital order"})
    client.post(
        "/api/trainer/presets",
        json={"name": "Fifth position", "start_fret": 5, "end_fret": 9, "strings": [1, 2, 3]},
    )
    client.post("/api/practice/sessions", json={"seconds": 600, "activity": "technique"})
    client.post("/api/practice/goals", json={"target_days": 3})
    client.post(
        "/api/trainer/attempts",
        json={
            "drill": "fret_to_note", "direction": "position_to_note",
            "target_string": 6, "target_fret": 3, "target_note": "G", "given_note": "G",
        },
    )
    _a_chord_attempt(client)
    archive = client.get("/api/export").content

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")

    callers: dict[str, set[str]] = {name: set() for _, name in rules}
    phase = {"who": "route"}

    def _spy(module, name):
        real = getattr(module, name)

        def wrapper(*args, **kwargs):
            callers[name].add(phase["who"])
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)

    for module, name in rules:
        _spy(module, name)

    # --- The routes, on the fresh library. ---
    _an_instrument(client)
    client.post("/api/setlists", json={"name": "Encores"})
    client.post(
        "/api/trainer/presets",
        json={"name": "Ninth position", "start_fret": 9, "end_fret": 12, "strings": [1]},
    )
    client.post("/api/practice/sessions", json={"seconds": 600, "activity": "technique"})
    client.post("/api/practice/goals", json={"target_days": 3})
    client.post(
        "/api/trainer/attempts",
        json={
            "drill": "fret_to_note", "direction": "position_to_note",
            "target_string": 6, "target_fret": 3, "target_note": "G", "given_note": "G",
        },
    )
    _a_chord_attempt(client)

    # --- The same six rules, reached through import instead. ---
    phase["who"] = "import"
    resp = client.post(
        "/api/import", files={"file": ("export.zip", archive, "application/zip")},
    )
    assert resp.status_code == 200, resp.text

    for _, name in rules:
        assert callers[name] == {"route", "import"}, f"{name}: only {sorted(callers[name])}"
