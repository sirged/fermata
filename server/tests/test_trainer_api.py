"""Per-attempt fretboard drill results (issue #27): trainer.normalise_attempt
directly, and the two /api/trainer/attempts routes end to end.

Structured rows, not a free-text note - issue #32's promise for this second
trainer - so the tests below are literal-value assertions on a stored row and
a real round trip through the API, not a shape check alone. See #146: a field
that validates against its model is not the same claim as a field that
actually reaches the wire with the value that was stored.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fermata import api, db, trainer

# ---------------------------------------------------------------- trainer.py, directly


def make(**overrides):
    base = dict(
        drill="fret_to_note",
        direction="position_to_note",
        target_string=6,
        target_fret=3,
        target_note="G",
        given_note="G",
    )
    return {**base, **overrides}


def test_a_matching_answer_is_correct():
    row = trainer.normalise_attempt(**make())
    assert row["correct"] is True


def test_a_mismatched_answer_is_incorrect():
    row = trainer.normalise_attempt(**make(given_note="F#"))
    assert row["correct"] is False


def test_correct_is_computed_and_cannot_be_smuggled_in():
    """normalise_attempt's signature has no `correct` parameter at all - a
    caller cannot pass one through and have it accepted, honest or not."""
    import inspect

    assert "correct" not in inspect.signature(trainer.normalise_attempt).parameters


def test_note_to_position_direction_computes_correctness_the_same_way():
    """The other direction: no target position, an answer given as a tapped
    position's note rather than a chosen one. Same rule, same field."""
    row = trainer.normalise_attempt(
        drill="fret_to_note",
        direction="note_to_position",
        target_note="C",
        given_string=5,
        given_fret=3,
        given_note="C",
    )
    assert row["correct"] is True
    assert row["target_string"] is None
    assert row["target_fret"] is None


def test_position_to_note_rejects_a_given_position():
    with pytest.raises(ValueError, match="given_string/given_fret must be omitted"):
        trainer.normalise_attempt(**make(given_string=1, given_fret=0))


def test_position_to_note_requires_a_target_position():
    with pytest.raises(ValueError, match="target_string and target_fret"):
        trainer.normalise_attempt(**make(target_string=None, target_fret=None))


def test_note_to_position_rejects_a_target_position():
    with pytest.raises(ValueError, match="target_string/target_fret must be omitted"):
        trainer.normalise_attempt(
            drill="fret_to_note",
            direction="note_to_position",
            target_string=6,
            target_fret=0,
            target_note="E",
            given_string=6,
            given_fret=0,
            given_note="E",
        )


def test_note_to_position_requires_a_given_position():
    with pytest.raises(ValueError, match="given_string and given_fret are required"):
        trainer.normalise_attempt(
            drill="fret_to_note", direction="note_to_position", target_note="E", given_note="E"
        )


def test_a_position_needs_both_halves():
    with pytest.raises(ValueError, match="must both be given, or neither"):
        trainer.normalise_attempt(**make(target_fret=None))


@pytest.mark.parametrize("field", ["target_note", "given_note"])
def test_only_the_twelve_canonical_pitch_classes_are_accepted(field):
    """Db is not accepted alongside C# - one spelling per pitch class, the
    same twelve pitch.js's spellMidi (and neck.js's pitchClass) produce, so
    "which notes get missed" is a GROUP BY rather than a query that first has
    to fold synonyms together. See trainer.py's PITCH_CLASSES."""
    with pytest.raises(ValueError, match="target_note|given_note"):
        trainer.normalise_attempt(**make(**{field: "Db"}))


def test_an_octave_is_rejected_not_silently_stripped():
    with pytest.raises(ValueError):
        trainer.normalise_attempt(**make(target_note="G3"))


@pytest.mark.parametrize("drill", ["fret_to_note", "FRET_TO_NOTE", "chords", ""])
def test_drill_must_be_a_known_one(drill):
    if drill == "fret_to_note":
        trainer.normalise_attempt(**make(drill=drill))  # does not raise
    else:
        with pytest.raises(ValueError, match="drill must be one of"):
            trainer.normalise_attempt(**make(drill=drill))


def test_string_number_bounds():
    with pytest.raises(ValueError, match="target_string"):
        trainer.normalise_attempt(**make(target_string=0))
    with pytest.raises(ValueError, match="target_string"):
        trainer.normalise_attempt(**make(target_string=25))


def test_fret_bounds():
    with pytest.raises(ValueError, match="target_fret"):
        trainer.normalise_attempt(**make(target_fret=-1))
    with pytest.raises(ValueError, match="target_fret"):
        trainer.normalise_attempt(**make(target_fret=37))
    # An open string is fret 0, and is accepted.
    row = trainer.normalise_attempt(**make(target_fret=0, given_note="F#"))
    assert row["target_fret"] == 0


def test_response_ms_bounds():
    row = trainer.normalise_attempt(**make(response_ms=1500))
    assert row["response_ms"] == 1500
    with pytest.raises(ValueError, match="response_ms"):
        trainer.normalise_attempt(**make(response_ms=-1))
    with pytest.raises(ValueError, match="response_ms"):
        trainer.normalise_attempt(**make(response_ms=trainer.MAX_RESPONSE_MS + 1))


# ---------------------------------------------------------------------------
# The API, end to end
# ---------------------------------------------------------------------------


@pytest.fixture
def client(app_env):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_logging_an_attempt_stores_the_row_and_returns_it(client):
    resp = client.post(
        "/api/trainer/attempts",
        json={
            "drill": "fret_to_note",
            "direction": "position_to_note",
            "target_string": 6,
            "target_fret": 3,
            "target_note": "G",
            "given_note": "F#",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Literal-value assertions on the response - not just "it validates".
    assert body["drill"] == "fret_to_note"
    assert body["direction"] == "position_to_note"
    assert body["target_string"] == 6
    assert body["target_fret"] == 3
    assert body["target_note"] == "G"
    assert body["given_note"] == "F#"
    assert body["given_string"] is None
    assert body["given_fret"] is None
    assert body["correct"] is False
    assert body["owner"] == "local"
    assert body["session_id"] is None
    assert isinstance(body["id"], int)
    assert body["created_at"]

    # And the field ROUND-TRIPS: what a fresh read of the same row says is
    # exactly what the write returned (#146's lesson, applied here rather
    # than only trusted from the write's own response).
    conn = db.connect()
    row = dict(conn.execute("SELECT * FROM trainer_attempts WHERE id = ?", (body["id"],)).fetchone())
    assert row["target_note"] == "G"
    assert row["given_note"] == "F#"
    assert row["correct"] == 0  # SQLite's storage of False

    fetched = client.get("/api/trainer/attempts?limit=10").json()["attempts"][0]
    assert fetched["id"] == body["id"]
    assert fetched["correct"] is False
    assert fetched["target_note"] == "G"
    assert fetched["given_note"] == "F#"


def test_a_correct_answer_round_trips_true(client):
    resp = client.post(
        "/api/trainer/attempts",
        json={
            "drill": "fret_to_note",
            "direction": "position_to_note",
            "target_string": 1,
            "target_fret": 0,
            "target_note": "E",
            "given_note": "E",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["correct"] is True
    listed = client.get("/api/trainer/attempts?correct=true").json()
    assert listed["total"] == 1
    assert listed["attempts"][0]["id"] == resp.json()["id"]
    assert client.get("/api/trainer/attempts?correct=false").json()["total"] == 0


def test_a_bad_body_is_refused_with_422_naming_the_problem(client):
    resp = client.post(
        "/api/trainer/attempts",
        json={
            "drill": "fret_to_note",
            "direction": "position_to_note",
            "target_string": 6,
            "target_fret": 3,
            "target_note": "H",  # not a pitch class
            "given_note": "G",
        },
    )
    assert resp.status_code == 422
    assert "target_note" in resp.json()["detail"]


def test_an_unknown_session_id_is_refused(client):
    resp = client.post(
        "/api/trainer/attempts",
        json={
            "session_id": 99999,
            "drill": "fret_to_note",
            "direction": "position_to_note",
            "target_string": 6,
            "target_fret": 3,
            "target_note": "G",
            "given_note": "G",
        },
    )
    assert resp.status_code == 404


def test_an_attempt_can_be_linked_to_a_real_session(client):
    session = client.post(
        "/api/practice/sessions", json={"seconds": 120, "activity": "fretboard"}
    ).json()
    resp = client.post(
        "/api/trainer/attempts",
        json={
            "session_id": session["id"],
            "drill": "fret_to_note",
            "direction": "position_to_note",
            "target_string": 6,
            "target_fret": 0,
            "target_note": "E",
            "given_note": "E",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["session_id"] == session["id"]
    assert client.get(f"/api/trainer/attempts?session_id={session['id']}").json()["total"] == 1

    # Deleting the session keeps the attempt - the question was still asked
    # and answered - and only unlinks it, mirroring practice_sessions'
    # score_id ON DELETE SET NULL.
    client.delete(f"/api/practice/sessions/{session['id']}")
    still_there = client.get("/api/trainer/attempts").json()
    assert still_there["total"] == 1
    assert still_there["attempts"][0]["session_id"] is None


def test_listing_filters_by_drill_and_direction(client):
    for direction in ("position_to_note", "note_to_position"):
        body = (
            {
                "drill": "fret_to_note",
                "direction": direction,
                "target_string": 3,
                "target_fret": 2,
                "target_note": "A",
                "given_note": "A",
            }
            if direction == "position_to_note"
            else {
                "drill": "fret_to_note",
                "direction": direction,
                "target_note": "A",
                "given_string": 3,
                "given_fret": 2,
                "given_note": "A",
            }
        )
        assert client.post("/api/trainer/attempts", json=body).status_code == 200

    assert client.get("/api/trainer/attempts").json()["total"] == 2
    assert (
        client.get("/api/trainer/attempts?direction=position_to_note").json()["total"] == 1
    )
    assert (
        client.get("/api/trainer/attempts?direction=note_to_position").json()["total"] == 1
    )
    assert client.get("/api/trainer/attempts?drill=fret_to_note").json()["total"] == 2


def test_an_unknown_filter_value_is_refused(client):
    assert client.get("/api/trainer/attempts?drill=chords").status_code == 422
    assert client.get("/api/trainer/attempts?direction=sideways").status_code == 422


def test_truncation_is_reported_honestly(client):
    for fret in range(3):
        client.post(
            "/api/trainer/attempts",
            json={
                "drill": "fret_to_note",
                "direction": "position_to_note",
                "target_string": 6,
                "target_fret": fret,
                "target_note": "E",
                "given_note": "E",
            },
        )
    listed = client.get("/api/trainer/attempts?limit=2").json()
    assert listed["total"] == 3
    assert len(listed["attempts"]) == 2
    assert listed["truncated"] is True
