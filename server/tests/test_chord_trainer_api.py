"""Per-attempt chord flash card results (issue #28): trainer.
normalise_chord_attempt directly, and the two /api/trainer/chord-attempts
routes end to end.

Structured rows, not a free-text note - issue #32's promise, the same as
fret to note's - so the tests below are literal-value assertions on a
stored row and a real round trip through the API (#146's lesson), not a
shape check alone.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fermata import api, db, trainer

# ---------------------------------------------------------------- trainer.py, directly


def shape_to_name(**overrides):
    base = dict(
        drill="chord_flashcards",
        direction="shape_to_name",
        target_root="C",
        target_quality="major",
        target_shape=[
            {"string": 5, "fret": 3},
            {"string": 4, "fret": 2},
            {"string": 3, "fret": 0},
            {"string": 2, "fret": 1},
            {"string": 1, "fret": 0},
        ],
        given_root="C",
        given_quality="major",
    )
    return {**base, **overrides}


def name_to_shape(**overrides):
    base = dict(
        drill="chord_flashcards",
        direction="name_to_shape",
        target_root="G",
        target_quality="major",
        given_notes=["G", "B", "D"],
        given_shape=[{"string": 6, "fret": 3}, {"string": 5, "fret": 2}],
    )
    return {**base, **overrides}


def test_a_matching_chord_name_is_correct():
    row = trainer.normalise_chord_attempt(**shape_to_name())
    assert row["correct"] is True


def test_a_different_chord_name_is_incorrect():
    row = trainer.normalise_chord_attempt(**shape_to_name(given_root="C", given_quality="minor"))
    assert row["correct"] is False


def test_correct_is_computed_and_cannot_be_smuggled_in():
    import inspect

    assert "correct" not in inspect.signature(trainer.normalise_chord_attempt).parameters


def test_name_to_shape_grades_by_tone_set_not_by_exact_fingering():
    """The taps do not have to reproduce any one canonical shape - only
    every required tone, and nothing else. A G major played a different way
    than the reference voicing still grades correct."""
    row = trainer.normalise_chord_attempt(
        drill="chord_flashcards",
        direction="name_to_shape",
        target_root="G",
        target_quality="major",
        given_notes=["D", "G", "B"],  # same set, different order
        given_shape=[{"string": 4, "fret": 0}, {"string": 3, "fret": 0}, {"string": 2, "fret": 0}],
    )
    assert row["correct"] is True


def test_name_to_shape_is_incorrect_when_a_required_tone_is_missing():
    row = trainer.normalise_chord_attempt(**name_to_shape(given_notes=["G", "B"]))
    assert row["correct"] is False


def test_name_to_shape_is_incorrect_against_an_empty_tap():
    row = trainer.normalise_chord_attempt(
        drill="chord_flashcards",
        direction="name_to_shape",
        target_root="G",
        target_quality="major",
        given_notes=[],
        given_shape=[],
    )
    assert row["correct"] is False


def test_shape_to_name_rejects_a_tapped_answer():
    with pytest.raises(ValueError, match="given_notes/given_shape must be omitted"):
        trainer.normalise_chord_attempt(**shape_to_name(given_notes=["C"], given_shape=[]))


def test_shape_to_name_requires_a_target_shape():
    with pytest.raises(ValueError, match="target_shape"):
        trainer.normalise_chord_attempt(**shape_to_name(target_shape=None))


def test_shape_to_name_requires_a_chosen_name():
    with pytest.raises(ValueError, match="given_root and given_quality"):
        trainer.normalise_chord_attempt(**shape_to_name(given_root=None, given_quality=None))


def test_name_to_shape_rejects_a_target_shape():
    with pytest.raises(ValueError, match="target_shape must be omitted"):
        trainer.normalise_chord_attempt(
            **name_to_shape(target_shape=[{"string": 6, "fret": 3}])
        )


def test_name_to_shape_rejects_a_chosen_name():
    with pytest.raises(ValueError, match="given_root/given_quality must be omitted"):
        trainer.normalise_chord_attempt(**name_to_shape(given_root="G", given_quality="major"))


def test_name_to_shape_requires_what_was_tapped():
    with pytest.raises(ValueError, match="given_notes and given_shape"):
        trainer.normalise_chord_attempt(**name_to_shape(given_notes=None, given_shape=None))


@pytest.mark.parametrize("field", ["target_root", "given_root"])
def test_only_the_twelve_canonical_pitch_classes_are_accepted_for_a_root(field):
    with pytest.raises(ValueError, match="target_root|given_root"):
        trainer.normalise_chord_attempt(**shape_to_name(**{field: "Db"}))


@pytest.mark.parametrize("field", ["target_quality", "given_quality"])
def test_only_the_known_qualities_are_accepted(field):
    with pytest.raises(ValueError, match="target_quality|given_quality"):
        trainer.normalise_chord_attempt(**shape_to_name(**{field: "augmented"}))


@pytest.mark.parametrize("drill", ["chord_flashcards", "CHORD_FLASHCARDS", "fret_to_note", ""])
def test_drill_must_be_a_known_chord_drill(drill):
    if drill == "chord_flashcards":
        trainer.normalise_chord_attempt(**shape_to_name(drill=drill))  # does not raise
    else:
        with pytest.raises(ValueError, match="drill must be one of"):
            trainer.normalise_chord_attempt(**shape_to_name(drill=drill))


def test_a_shape_position_needs_bounded_whole_numbers():
    with pytest.raises(ValueError, match="target_shape"):
        trainer.normalise_chord_attempt(
            **shape_to_name(target_shape=[{"string": 0, "fret": 0}])
        )
    with pytest.raises(ValueError, match="target_shape"):
        trainer.normalise_chord_attempt(
            **shape_to_name(target_shape=[{"string": 5, "fret": -1}])
        )


def test_response_ms_bounds():
    row = trainer.normalise_chord_attempt(**shape_to_name(response_ms=1200))
    assert row["response_ms"] == 1200
    with pytest.raises(ValueError, match="response_ms"):
        trainer.normalise_chord_attempt(**shape_to_name(response_ms=-1))


def test_chord_attempt_dict_decodes_the_json_columns_back_into_lists():
    row = trainer.normalise_chord_attempt(**shape_to_name())
    row["id"] = 1
    row["owner"] = "local"
    row["created_at"] = "2026-01-01T00:00:00"
    d = trainer.chord_attempt_dict(row)
    assert d["target_shape"] == [
        {"string": 5, "fret": 3},
        {"string": 4, "fret": 2},
        {"string": 3, "fret": 0},
        {"string": 2, "fret": 1},
        {"string": 1, "fret": 0},
    ]
    assert d["given_notes"] is None
    assert d["correct"] is True


# ---------------------------------------------------------------------------
# The API, end to end
# ---------------------------------------------------------------------------


@pytest.fixture
def client(app_env):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_logging_a_shape_to_name_attempt_stores_the_row_and_returns_it(client):
    resp = client.post("/api/trainer/chord-attempts", json=shape_to_name())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["drill"] == "chord_flashcards"
    assert body["direction"] == "shape_to_name"
    assert body["target_root"] == "C"
    assert body["target_quality"] == "major"
    assert body["target_shape"] == [
        {"string": 5, "fret": 3},
        {"string": 4, "fret": 2},
        {"string": 3, "fret": 0},
        {"string": 2, "fret": 1},
        {"string": 1, "fret": 0},
    ]
    assert body["given_root"] == "C"
    assert body["given_quality"] == "major"
    assert body["given_notes"] is None
    assert body["given_shape"] is None
    assert body["correct"] is True
    assert body["owner"] == "local"
    assert body["session_id"] is None
    assert isinstance(body["id"], int)
    assert body["created_at"]

    # And the field ROUND-TRIPS: what a fresh read of the same row says is
    # exactly what the write returned (#146's lesson).
    conn = db.connect()
    row = dict(
        conn.execute(
            "SELECT * FROM trainer_chord_attempts WHERE id = ?", (body["id"],)
        ).fetchone()
    )
    assert row["target_root"] == "C"
    assert row["given_root"] == "C"
    assert row["correct"] == 1  # SQLite's storage of True

    fetched = client.get("/api/trainer/chord-attempts?limit=10").json()["attempts"][0]
    assert fetched["id"] == body["id"]
    assert fetched["correct"] is True
    assert fetched["target_root"] == "C"


def test_logging_a_name_to_shape_attempt_round_trips_the_tapped_notes(client):
    resp = client.post("/api/trainer/chord-attempts", json=name_to_shape())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["direction"] == "name_to_shape"
    assert body["target_shape"] is None
    assert sorted(body["given_notes"]) == ["B", "D", "G"]
    assert body["given_shape"] == [{"string": 6, "fret": 3}, {"string": 5, "fret": 2}]
    assert body["correct"] is True


def test_an_incorrect_answer_round_trips_false(client):
    resp = client.post(
        "/api/trainer/chord-attempts", json=shape_to_name(given_root="A", given_quality="minor")
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["correct"] is False
    listed = client.get("/api/trainer/chord-attempts?correct=false").json()
    assert listed["total"] == 1
    assert listed["attempts"][0]["target_root"] == "C"
    assert client.get("/api/trainer/chord-attempts?correct=true").json()["total"] == 0


def test_a_bad_body_is_refused_with_422_naming_the_problem(client):
    resp = client.post(
        "/api/trainer/chord-attempts", json=shape_to_name(target_quality="augmented")
    )
    assert resp.status_code == 422
    assert "target_quality" in resp.json()["detail"]


def test_an_unknown_session_id_is_refused(client):
    resp = client.post(
        "/api/trainer/chord-attempts", json=shape_to_name(session_id=99999)
    )
    assert resp.status_code == 404


def test_an_attempt_can_be_linked_to_a_real_session(client):
    session = client.post(
        "/api/practice/sessions", json={"seconds": 90, "activity": "chords"}
    ).json()
    resp = client.post(
        "/api/trainer/chord-attempts", json=shape_to_name(session_id=session["id"])
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["session_id"] == session["id"]
    assert (
        client.get(f"/api/trainer/chord-attempts?session_id={session['id']}").json()["total"] == 1
    )

    # Deleting the session keeps the attempt and only unlinks it - the same
    # rule trainer_attempts follows for the same reason.
    client.delete(f"/api/practice/sessions/{session['id']}")
    still_there = client.get("/api/trainer/chord-attempts").json()
    assert still_there["total"] == 1
    assert still_there["attempts"][0]["session_id"] is None


def test_listing_filters_by_root_and_quality(client):
    client.post("/api/trainer/chord-attempts", json=shape_to_name())
    client.post("/api/trainer/chord-attempts", json=name_to_shape())

    assert client.get("/api/trainer/chord-attempts").json()["total"] == 2
    assert client.get("/api/trainer/chord-attempts?root=C").json()["total"] == 1
    assert client.get("/api/trainer/chord-attempts?root=G").json()["total"] == 1
    assert client.get("/api/trainer/chord-attempts?quality=major").json()["total"] == 2
    assert client.get("/api/trainer/chord-attempts?root=C&quality=major").json()["total"] == 1
    assert client.get("/api/trainer/chord-attempts?direction=shape_to_name").json()["total"] == 1


def test_an_unknown_filter_value_is_refused(client):
    assert client.get("/api/trainer/chord-attempts?drill=fret_to_note").status_code == 422
    assert client.get("/api/trainer/chord-attempts?direction=sideways").status_code == 422
    assert client.get("/api/trainer/chord-attempts?root=H").status_code == 422
    assert client.get("/api/trainer/chord-attempts?quality=augmented").status_code == 422


def test_truncation_is_reported_honestly(client):
    for root in ("C", "D", "E"):
        client.post("/api/trainer/chord-attempts", json=shape_to_name(target_root=root, given_root=root))
    listed = client.get("/api/trainer/chord-attempts?limit=2").json()
    assert listed["total"] == 3
    assert len(listed["attempts"]) == 2
    assert listed["truncated"] is True
