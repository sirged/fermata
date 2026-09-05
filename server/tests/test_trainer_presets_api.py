"""Named drill scopes (issue #236): trainer.normalise_preset directly, and the
three /api/trainer/presets routes end to end, plus the practice_sessions
column that makes "what was practised" a row rather than a sentence.

Literal-value assertions on stored rows and real round trips, not shape checks
alone - the same discipline test_trainer_api.py sets out and for the same
reason (#146): a field that validates against its model is not the same claim
as a field that reaches the wire carrying what was stored.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fermata import api, db, trainer

# ---------------------------------------------------------------- trainer.py, directly


def make(**overrides):
    base = dict(name="Fifth position", start_fret=5, end_fret=9, strings=[1, 2])
    return {**base, **overrides}


def test_a_whole_scope_normalises_to_a_row_and_a_string_set():
    values = trainer.normalise_preset(**make(key_root="G", key_quality="minor"))
    assert values["preset"] == {
        "name": "Fifth position",
        "start_fret": 5,
        "end_fret": 9,
        "key_root": "G",
        "key_quality": "minor",
    }
    assert values["strings"] == [1, 2]


def test_a_scope_with_no_key_stores_two_nulls():
    values = trainer.normalise_preset(**make())
    assert values["preset"]["key_root"] is None
    assert values["preset"]["key_quality"] is None


def test_the_string_set_is_deduplicated_and_sorted():
    """A set, stored as one row per member - so the same string twice is one
    row, and the order a client happened to send is not a fact about the
    scope."""
    values = trainer.normalise_preset(**make(strings=[3, 1, 3, 2]))
    assert values["strings"] == [1, 2, 3]


def test_a_name_is_cleaned_the_way_a_setlist_name_is():
    values = trainer.normalise_preset(**make(name="  Fifth   position  "))
    assert values["preset"]["name"] == "Fifth position"


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        (dict(name="   "), "needs a name"),
        (dict(name=""), "needs a name"),
        (dict(name=None), "needs a name"),
        (dict(strings=[]), "at least one string"),
        (dict(strings="1,2"), "list of string numbers"),
        (dict(strings=[0]), "between 1 and 24"),
        (dict(strings=[25]), "between 1 and 24"),
        (dict(strings=[True]), "whole number"),
        (dict(start_fret=-1), "between 0 and 36"),
        (dict(end_fret=37), "between 0 and 36"),
        (dict(start_fret=9, end_fret=5), "must not be past"),
        (dict(start_fret=1.5), "whole number"),
        (dict(key_root="G"), "both be given, or neither"),
        (dict(key_quality="major"), "both be given, or neither"),
        (dict(key_root="H", key_quality="major"), "key_root must be one of"),
        (dict(key_root="G", key_quality="dorian"), "key_quality must be one of"),
    ],
)
def test_every_rejection_names_what_is_wrong(overrides, fragment):
    with pytest.raises(ValueError) as caught:
        trainer.normalise_preset(**make(**overrides))
    assert fragment in str(caught.value)


def test_a_one_fret_range_is_a_range():
    """start_fret == end_fret is a real scope (one fret across some strings),
    not the degenerate case the ordering rule refuses."""
    values = trainer.normalise_preset(**make(start_fret=7, end_fret=7))
    assert values["preset"]["start_fret"] == 7
    assert values["preset"]["end_fret"] == 7


# ---------------------------------------------------------------- the routes


@pytest.fixture()
def client(app_env):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def save(client, **overrides):
    resp = client.post("/api/trainer/presets", json=make(**overrides))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_saving_a_scope_stores_it_and_returns_it(client):
    body = save(client, key_root="G", key_quality="major")
    assert body["id"] > 0
    assert body["owner"] == db.DEFAULT_OWNER
    assert body["name"] == "Fifth position"
    assert body["start_fret"] == 5
    assert body["end_fret"] == 9
    assert body["key_root"] == "G"
    assert body["key_quality"] == "major"
    assert body["strings"] == [1, 2]
    assert body["created_at"]


def test_the_string_set_is_stored_as_rows_rather_than_as_text(client):
    """The rule db.py's note states, checked at the storage layer: "which
    strings does this scope allow" is a WHERE clause, so there is one row per
    string and no column anywhere holding a list."""
    body = save(client, strings=[6, 5, 4])
    conn = db.connect()
    rows = conn.execute(
        "SELECT string_number FROM trainer_scope_preset_strings WHERE preset_id = ? "
        "ORDER BY string_number",
        (body["id"],),
    ).fetchall()
    assert [r["string_number"] for r in rows] == [4, 5, 6]
    stored = conn.execute(
        "SELECT * FROM trainer_scope_presets WHERE id = ?", (body["id"],)
    ).fetchone()
    assert "strings" not in stored.keys()


def test_listing_gives_every_scope_newest_first_with_its_strings(client):
    first = save(client, name="Open position", start_fret=0, end_fret=3, strings=[6, 5, 4])
    second = save(client, name="Fifth position", strings=[1, 2])
    listed = client.get("/api/trainer/presets")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [p["id"] for p in body] == [second["id"], first["id"]]
    assert body[1]["strings"] == [4, 5, 6]
    assert body[1]["start_fret"] == 0
    assert body[1]["end_fret"] == 3


def test_an_empty_list_is_an_empty_list(client):
    assert client.get("/api/trainer/presets").json() == []


@pytest.mark.parametrize(
    "overrides",
    [
        dict(name="   "),
        dict(strings=[]),
        dict(strings=[0]),
        dict(start_fret=9, end_fret=5),
        dict(end_fret=37),
        dict(key_root="G"),
        dict(key_quality="major"),
        dict(key_root="H", key_quality="major"),
        dict(key_root="G", key_quality="dorian"),
    ],
)
def test_a_scope_that_does_not_describe_anything_is_refused_with_422(client, overrides):
    resp = client.post("/api/trainer/presets", json=make(**overrides))
    assert resp.status_code == 422, resp.text
    # And nothing was stored - a refused save leaves the list as it was.
    assert client.get("/api/trainer/presets").json() == []


def test_a_name_already_in_use_is_refused_with_409_and_stores_nothing(client):
    """409, not 422: nothing is wrong with the request, it collides with what
    is already there - the status this codebase uses for exactly that (see
    api.rename_folder, api.delete_score). Deliberately unlike setlists, whose
    duplicate names are allowed; see create_trainer_preset's docstring."""
    save(client)
    resp = client.post("/api/trainer/presets", json=make(start_fret=0, end_fret=3))
    assert resp.status_code == 409, resp.text
    assert "Fifth position" in resp.json()["detail"]
    listed = client.get("/api/trainer/presets").json()
    assert len(listed) == 1
    # The FIRST one is untouched - a refused duplicate is not a rename.
    assert listed[0]["start_fret"] == 5


def test_a_name_that_cleans_to_an_existing_one_collides_too(client):
    """The clash is checked on the CLEANED name, not the raw one, or "Fifth
    position" and "Fifth   position" would sit in the picker as two entries
    nobody could tell apart."""
    save(client)
    resp = client.post("/api/trainer/presets", json=make(name="  Fifth   position  "))
    assert resp.status_code == 409, resp.text


def test_a_name_differing_only_in_case_collides_too(client):
    """"Fifth position" and "fifth position" are one entry to a reader, so
    they are one entry here: the clash check compares without case, and the
    unique index underneath it does the same (see the schema test)."""
    save(client)
    resp = client.post("/api/trainer/presets", json=make(name="FIFTH POSITION"))
    assert resp.status_code == 409, resp.text
    assert len(client.get("/api/trainer/presets").json()) == 1


def test_deleting_a_scope_removes_it_and_its_strings(client):
    body = save(client)
    resp = client.delete(f"/api/trainer/presets/{body['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": body["id"], "sessions_kept": 0}
    assert client.get("/api/trainer/presets").json() == []
    conn = db.connect()
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM trainer_scope_preset_strings WHERE preset_id = ?",
            (body["id"],),
        ).fetchone()["n"]
        == 0
    )


def test_deleting_a_scope_that_is_not_there_is_a_404(client):
    assert client.delete("/api/trainer/presets/9999").status_code == 404


# ------------------------------------------- the practice session's own column


def log(client, **overrides):
    body = {"activity": "fretboard", "seconds": 600, **overrides}
    resp = client.post("/api/practice/sessions", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_session_carries_the_scope_it_was_practised_under_and_reads_it_back(client):
    preset = save(client)
    session = log(client, preset_id=preset["id"])
    assert session["preset_id"] == preset["id"]
    # Read back through the LIST route as well, not only off the write's own
    # response - the claim is about what a later reader sees.
    listed = client.get("/api/practice/sessions?limit=10").json()["sessions"]
    assert [s["preset_id"] for s in listed] == [preset["id"]]


def test_a_session_with_no_named_scope_carries_none(client):
    session = log(client)
    assert session["preset_id"] is None


def test_a_session_naming_a_scope_that_is_not_there_is_a_404(client):
    """Answered as a 404 naming what was not found, rather than left for the
    foreign key to raise an IntegrityError nobody could act on."""
    resp = client.post(
        "/api/practice/sessions", json={"activity": "fretboard", "seconds": 600, "preset_id": 9999}
    )
    assert resp.status_code == 404, resp.text


def test_a_patch_can_add_or_clear_the_scope_on_a_session_already_logged(client):
    preset = save(client)
    session = log(client)
    patched = client.patch(
        f"/api/practice/sessions/{session['id']}", json={"preset_id": preset["id"]}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["preset_id"] == preset["id"]
    cleared = client.patch(
        f"/api/practice/sessions/{session['id']}", json={"preset_id": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["preset_id"] is None


def test_deleting_a_scope_keeps_the_practice_and_clears_only_the_reference(client):
    """The claim db.py's ON DELETE SET NULL note makes, end to end: the
    minutes were still practised, so the session stays whole and loses only
    the name of the scope it ran under."""
    preset = save(client)
    session = log(client, preset_id=preset["id"], seconds=1234, note="Fret to note. 8 questions.")

    resp = client.delete(f"/api/trainer/presets/{preset['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["sessions_kept"] == 1

    after = client.get("/api/practice/sessions?limit=10").json()["sessions"]
    assert len(after) == 1
    assert after[0]["id"] == session["id"]
    assert after[0]["seconds"] == 1234
    assert after[0]["activity"] == "fretboard"
    assert after[0]["note"] == "Fret to note. 8 questions."
    assert after[0]["preset_id"] is None


def test_sessions_kept_counts_every_session_the_delete_leaves_behind(client):
    preset = save(client)
    for _ in range(3):
        log(client, preset_id=preset["id"])
    log(client)
    resp = client.delete(f"/api/trainer/presets/{preset['id']}")
    assert resp.json()["sessions_kept"] == 3
    assert len(client.get("/api/practice/sessions?limit=10").json()["sessions"]) == 4
