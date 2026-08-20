import re
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from fermata import api, db, instruments, musicxml


@pytest.fixture
def client(app_env):
    """The router alone, without main.py's lifespan or its static mount, so a
    request goes to a throwaway database instead of the real config directory.
    Needed for anything whose validation lives in the HTTP layer - a path
    parameter's bounds are never applied when the endpoint is called directly."""
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)

GUITAR = {
    "name": "My guitar",
    "fretted": True,
    "string_count": 6,
    "string_pitches": ["E2", "A2", "D3", "G3", "B3", "E4"],
    "fret_count": 22,
}

VIOLIN = {
    "name": "My violin",
    "fretted": False,
    "string_count": 4,
    "string_pitches": ["G3", "D4", "A4", "E5"],
}


def make(**overrides) -> api.InstrumentIn:
    return api.InstrumentIn(**{**GUITAR, **overrides})


def make_unfretted(**overrides) -> api.InstrumentIn:
    return api.InstrumentIn(**{**VIOLIN, **overrides})


# ---------------------------------------------------------------- presets


def test_presets_load():
    keys = [p["key"] for p in instruments.presets()]
    assert keys == [
        "guitar-standard",
        "guitar-drop-d",
        "guitar-dadgad",
        "guitar-open-g",
        "guitar-seven-string",
        "bass-four-string",
        "bass-five-string",
        "ukulele",
        "violin",
        "viola",
        "cello",
    ]
    assert api.list_instrument_presets() == instruments.presets()


def test_every_presets_string_count_matches_its_pitches():
    for preset in instruments.presets():
        assert preset["string_count"] == len(preset["string_pitches"]), preset["key"]


def test_unfretted_presets_carry_no_fretted_only_fields():
    unfretted = [p for p in instruments.presets() if not p["fretted"]]
    assert [p["key"] for p in unfretted] == ["violin", "viola", "cello"]
    for preset in unfretted:
        assert preset["fret_count"] is None
        assert preset["capo"] is None


def test_every_preset_is_a_definition_the_api_accepts(app_env):
    """A preset a player picks is submitted back verbatim, so any preset the
    validator would reject is a preset that cannot be saved."""
    for preset in instruments.presets():
        saved = api.create_instrument(
            api.InstrumentIn(
                name=preset["name"],
                fretted=preset["fretted"],
                string_count=preset["string_count"],
                string_pitches=preset["string_pitches"],
                fret_count=preset["fret_count"],
                capo=preset["capo"],
                reference_pitch=preset["reference_pitch"],
            )
        )
        assert saved["string_pitches"] == preset["string_pitches"]
        assert saved["strings"] == preset["strings"]


def test_the_reentrant_ukulele_preset_is_not_rejected_for_its_order(app_env):
    """A ukulele's string 4 sounds above its string 3. String order is physical,
    not ascending pitch, so nothing may enforce a rising sequence."""
    uke = next(p for p in instruments.presets() if p["key"] == "ukulele")
    assert uke["string_pitches"] == ["G4", "C4", "E4", "A4"]
    saved = api.create_instrument(
        api.InstrumentIn(
            name="Ukulele", fretted=True, string_count=4,
            string_pitches=uke["string_pitches"], fret_count=12,
        )
    )
    assert [s["number"] for s in saved["strings"]] == [4, 3, 2, 1]
    assert saved["strings"][0]["midi"] > saved["strings"][1]["midi"]


# ---------------------------------------------------------------- frequencies


def test_standard_tuning_frequencies_at_a440(app_env):
    saved = api.create_instrument(make())
    by_pitch = {s["pitch"]: round(s["frequency"], 3) for s in saved["strings"]}
    assert by_pitch == {
        "E2": 82.407,
        "A2": 110.0,
        "D3": 146.832,
        "G3": 195.998,
        "B3": 246.942,
        "E4": 329.628,
    }


def test_frequencies_are_sent_unrounded(app_env):
    """The server rounding and the browser formatting would be two opinions
    about precision, and they disagree: at 300 Hz, MIDI 22 is 19.8650...,
    which rounds to 19.865 and then formats to 19.87, while formatting the
    unrounded value gives 19.86. Exactly one place turns a frequency into text,
    and it is not this one."""
    saved = api.create_instrument(make())
    low_e = next(s for s in saved["strings"] if s["pitch"] == "E2")
    assert low_e["frequency"] != round(low_e["frequency"], 3)
    assert low_e["frequency"] == instruments.frequency(40, 440.0)


def test_the_reference_pitch_moves_every_string(app_env):
    saved = api.create_instrument(make(reference_pitch=415.0))
    by_pitch = {s["pitch"]: s["frequency"] for s in saved["strings"]}
    # A415, a common Baroque pitch: concert A lands on 103.75 (415/4) and the
    # low E follows it down from 82.407 rather than staying put.
    assert by_pitch["A2"] == pytest.approx(103.75, abs=0.001)
    assert by_pitch["E2"] == pytest.approx(77.725, abs=0.001)


# ---------------------------------------------------------------- capo
# A capo raises every string, so it decides what the instrument sounds - and
# the sounding pitch is what the audition plays and what a player matches by
# ear. What is STORED stays the open, non-capo tuning, which is what
# <staff-tuning> records.


def test_a_capo_does_not_change_what_is_stored(app_env):
    saved = api.create_instrument(make(capo=5))
    assert saved["string_pitches"] == GUITAR["string_pitches"]
    conn = db.connect()
    row = conn.execute(
        "SELECT string_pitches, capo FROM instruments WHERE id = ?", (saved["id"],)
    ).fetchone()
    assert row["capo"] == 5
    assert "E2" in row["string_pitches"]


def test_a_capo_raises_every_sounding_pitch(app_env):
    saved = api.create_instrument(make(capo=5))
    assert [s["sounding_pitch"] for s in saved["strings"]] == [
        "A2", "D3", "G3", "C4", "E4", "A4",
    ]
    assert [s["sounding_midi"] for s in saved["strings"]] == [45, 50, 55, 60, 64, 69]
    # nominal is untouched
    assert [s["pitch"] for s in saved["strings"]] == GUITAR["string_pitches"]
    assert [s["midi"] for s in saved["strings"]] == [40, 45, 50, 55, 59, 64]


def test_the_capo_moves_the_sounding_frequency_not_the_nominal_one(app_env):
    saved = api.create_instrument(make(capo=5))
    low = saved["strings"][0]
    assert low["frequency"] == pytest.approx(82.407, abs=0.001)
    assert low["sounding_frequency"] == pytest.approx(110.0, abs=0.001)


def test_a_capo_of_zero_leaves_the_two_pitches_identical(app_env):
    """The ordinary case: nothing to explain, and the interface has nothing
    extra to show."""
    saved = api.create_instrument(make())
    for s in saved["strings"]:
        assert s["sounding_pitch"] == s["pitch"]
        assert s["sounding_midi"] == s["midi"]
        assert s["sounding_frequency"] == s["frequency"]


def test_a_capo_at_five_is_distinguishable_from_no_capo(app_env):
    """The whole defect this guards: both instruments used to report string 6
    as E2 at 82.407 Hz, byte for byte."""
    plain = api.create_instrument(make(name="Plain"))
    capoed = api.create_instrument(make(name="Capoed", capo=5))
    assert plain["strings"][0]["sounding_frequency"] != capoed["strings"][0]["sounding_frequency"]


def test_a_capo_that_pushes_a_string_off_the_top_is_rejected(app_env):
    """0 <= capo <= fret_count says nothing about where the capo puts a string
    that is already near the ceiling: G9 is the highest note MIDI has, so any
    capo at all takes it past what the synthesiser can sound."""
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(
            make(string_count=1, string_pitches=["G9"], fret_count=24, capo=24)
        )
    assert exc_info.value.status_code == 422
    assert "capo" in exc_info.value.detail
    assert "sounds outside" in exc_info.value.detail


def test_the_same_string_without_a_capo_is_accepted(app_env):
    """The bound above must be about the capo, not about G9 being unusable."""
    saved = api.create_instrument(make(string_count=1, string_pitches=["G9"], fret_count=24))
    assert saved["strings"][0]["sounding_midi"] == 127


def test_a_capo_of_zero_on_an_unfretted_instrument_is_accepted(app_env):
    """Zero plainly means "no capo" - a client that always sends the field is
    not claiming a violin has one."""
    saved = api.create_instrument(make_unfretted(capo=0))
    assert saved["capo"] is None
    assert saved["fretted"] is False


def test_string_numbers_run_opposite_to_list_order(app_env):
    saved = api.create_instrument(make())
    assert [(s["number"], s["pitch"]) for s in saved["strings"]] == [
        (6, "E2"), (5, "A2"), (4, "D3"), (3, "G3"), (2, "B3"), (1, "E4"),
    ]


# ---------------------------------------------------------------- create/read


def test_create_then_read_back(app_env):
    created = api.create_instrument(make())
    assert created["name"] == "My guitar"
    assert created["fretted"] is True
    assert created["string_pitches"] == GUITAR["string_pitches"]
    assert created["fret_count"] == 22
    assert created["capo"] == 0
    assert created["reference_pitch"] == 440.0
    assert api.get_instrument(created["id"]) == created
    assert api.list_instruments() == [created]


def test_owner_defaults_to_the_single_instance_owner(app_env):
    created = api.create_instrument(make())
    conn = db.connect()
    row = conn.execute(
        "SELECT owner FROM instruments WHERE id = ?", (created["id"],)
    ).fetchone()
    assert row["owner"] == db.DEFAULT_OWNER == "local"
    assert created["owner"] == "local"


def test_an_unfretted_instrument_stores_no_fret_fields(app_env):
    created = api.create_instrument(make_unfretted())
    assert created["fretted"] is False
    assert created["fret_count"] is None
    assert created["capo"] is None
    assert [s["pitch"] for s in created["strings"]] == ["G3", "D4", "A4", "E5"]


def test_reading_an_instrument_that_does_not_exist(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.get_instrument(999)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------- validation


def test_too_few_string_pitches_for_the_string_count_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(string_count=6, string_pitches=["E2", "A2", "D3"]))
    assert exc_info.value.status_code == 422
    assert "3 string pitch" in exc_info.value.detail
    assert api.list_instruments() == []


def test_too_many_string_pitches_for_the_string_count_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(
            make(string_count=4, string_pitches=["E1", "A1", "D2", "G2", "C3"])
        )
    assert exc_info.value.status_code == 422


def test_a_fret_count_on_an_unfretted_instrument_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make_unfretted(fret_count=19))
    assert exc_info.value.status_code == 422
    assert "fret_count" in exc_info.value.detail
    assert api.list_instruments() == []


def test_a_capo_on_an_unfretted_instrument_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make_unfretted(capo=2))
    assert exc_info.value.status_code == 422
    assert "capo" in exc_info.value.detail


def test_a_fretted_instrument_needs_a_fret_count(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(fret_count=None))
    assert exc_info.value.status_code == 422


def test_a_capo_past_the_last_fret_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(fret_count=12, capo=13))
    assert exc_info.value.status_code == 422


@pytest.mark.parametrize("fret_count", [0, instruments.MAX_FRETS + 1])
def test_fret_counts_outside_the_bounds_are_rejected(app_env, fret_count):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(fret_count=fret_count))
    assert exc_info.value.status_code == 422


def test_a_string_count_of_zero_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(string_count=0, string_pitches=[]))
    assert exc_info.value.status_code == 422


def test_more_strings_than_any_instrument_has_is_rejected(app_env):
    count = instruments.MAX_STRINGS + 1
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(string_count=count, string_pitches=["E2"] * count))
    assert exc_info.value.status_code == 422


@pytest.mark.parametrize("pitch", ["", "H2", "E", "E2x", "Ebb"])
def test_a_string_pitch_that_is_not_a_pitch_name_is_rejected(app_env, pitch):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(
            make(string_count=2, string_pitches=[pitch, "A2"], fret_count=12)
        )
    assert exc_info.value.status_code == 422


def test_a_string_outside_the_playable_range_is_rejected(app_env):
    """MIDI stops at 127, and a string the synthesiser cannot sound is a
    tuning that cannot be checked - which is most of the point."""
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(string_count=1, string_pitches=["C12"], fret_count=12))
    assert exc_info.value.status_code == 422


@pytest.mark.parametrize("pitch", ["C-1", "B-1"])
def test_a_string_below_what_musicxml_can_write_is_rejected(app_env, pitch):
    """MIDI reaches down to C-1, but MusicXML's `octave` type starts at 0, so a
    tuning stored down there could never be written out by the emitter that
    will eventually read it."""
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(string_count=1, string_pitches=[pitch], fret_count=12))
    assert exc_info.value.status_code == 422


def test_c0_is_the_lowest_string_accepted(app_env):
    saved = api.create_instrument(make(string_count=1, string_pitches=["C0"], fret_count=12))
    assert saved["strings"][0]["midi"] == instruments.MIN_MIDI == 12


def test_the_accepted_range_is_exactly_what_musicxml_can_write(app_env):
    """MIN_MIDI/MAX_MIDI are derived from musicxml.MIN_OCTAVE rather than
    restated, and this is what pins the derivation to its reason."""
    assert musicxml.is_representable(instruments.MIN_MIDI)
    assert not musicxml.is_representable(instruments.MIN_MIDI - 1)
    assert musicxml.is_representable(instruments.MAX_MIDI)
    # the ceiling is MIDI's, not MusicXML's - 128 would still be writable
    assert instruments.MAX_MIDI == 127


@pytest.mark.parametrize("hz", [0, 100.0, 1000.0])
def test_a_reference_pitch_outside_the_bounds_is_rejected(app_env, hz):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(reference_pitch=hz))
    assert exc_info.value.status_code == 422


def test_a_blank_name_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(name="   "))
    assert exc_info.value.status_code == 422


def test_a_name_is_stored_trimmed(app_env):
    created = api.create_instrument(make(name="  Parlour guitar  "))
    assert created["name"] == "Parlour guitar"


@pytest.mark.parametrize(
    "raw,stored",
    [
        ("Guitar\x00", "Guitar"),
        ("Guitar\nBass", "GuitarBass"),
        ("Line one\r\nLine two", "Line oneLine two"),
        ("Tab\there", "Tabhere"),
        ("Spaced   out", "Spaced out"),
    ],
)
def test_control_characters_do_not_survive_into_a_name(app_env, raw, stored):
    """A NUL or a newline pasted out of a tuning chart is not part of a name,
    and stored verbatim it travels into every place the name is later shown."""
    created = api.create_instrument(make(name=raw))
    assert created["name"] == stored


def test_unicode_format_characters_do_not_survive_into_a_name(app_env):
    """U+202E RIGHT-TO-LEFT OVERRIDE is not a control character in the C0 sense
    and survives a control-character filter, but stored in a name it visually
    reorders the text AROUND it wherever the name appears - so an instrument
    could be made to misrepresent the interface showing it."""
    created = api.create_instrument(make(name="Guitar‮gnirts-6‬"))
    assert "‮" not in created["name"]
    assert "‬" not in created["name"]
    assert created["name"] == "Guitargnirts-6"


def test_a_zero_width_joiner_does_not_survive_into_a_name(app_env):
    created = api.create_instrument(make(name="Gui​tar"))
    assert created["name"] == "Guitar"


def test_a_name_of_nothing_but_control_characters_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(name="\x00\n\t"))
    assert exc_info.value.status_code == 422


def test_the_length_limit_applies_to_what_is_actually_stored(app_env):
    """Controls are removed first, so a name that is only over the limit
    because of them is not refused for a length it will not have."""
    created = api.create_instrument(make(name="G" * instruments.MAX_NAME_CHARS + "\x00\x00"))
    assert len(created["name"]) == instruments.MAX_NAME_CHARS


# ---------------------------------------------------------------- update


def test_update_replaces_the_definition(app_env):
    created = api.create_instrument(make())
    updated = api.update_instrument(
        created["id"],
        api.InstrumentIn(
            name="Dropped D",
            fretted=True,
            string_count=6,
            string_pitches=["D2", "A2", "D3", "G3", "B3", "E4"],
            fret_count=20,
            capo=2,
        ),
    )
    assert updated["id"] == created["id"]
    assert updated["name"] == "Dropped D"
    assert updated["string_pitches"][0] == "D2"
    assert updated["fret_count"] == 20
    assert updated["capo"] == 2
    assert api.get_instrument(created["id"]) == updated
    assert len(api.list_instruments()) == 1


def test_update_switching_to_unfretted_clears_the_fret_fields(app_env):
    created = api.create_instrument(make())
    updated = api.update_instrument(
        created["id"],
        api.InstrumentIn(
            name="Fretless", fretted=False, string_count=4,
            string_pitches=["E1", "A1", "D2", "G2"],
        ),
    )
    assert updated["fretted"] is False
    assert updated["fret_count"] is None
    assert updated["capo"] is None


def test_update_applies_the_same_rules_as_create(app_env):
    created = api.create_instrument(make())
    with pytest.raises(HTTPException) as exc_info:
        api.update_instrument(created["id"], make(string_count=6, string_pitches=["E2"]))
    assert exc_info.value.status_code == 422
    # rejected outright, not half-applied
    assert api.get_instrument(created["id"])["string_pitches"] == GUITAR["string_pitches"]


def test_update_leaves_a_fret_count_behind_when_switching_to_unfretted(app_env):
    """The usual way to send a contradictory definition: pick a fretted preset,
    switch it to unfretted, and leave the fret count in the form."""
    created = api.create_instrument(make())
    with pytest.raises(HTTPException) as exc_info:
        api.update_instrument(created["id"], make(fretted=False))
    assert exc_info.value.status_code == 422
    assert api.get_instrument(created["id"])["fretted"] is True


def test_updating_an_instrument_that_does_not_exist(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.update_instrument(999, make())
    assert exc_info.value.status_code == 404


def test_update_keeps_created_at(app_env):
    created = api.create_instrument(make())
    updated = api.update_instrument(created["id"], make(name="Renamed"))
    assert updated["created_at"] == created["created_at"]


# ---------------------------------------------------------------- delete


def test_delete_removes_it(app_env):
    created = api.create_instrument(make())
    assert api.delete_instrument(created["id"]) == {
        "deleted": created["id"],
        "scores_unlinked": 0,
    }
    assert api.list_instruments() == []
    with pytest.raises(HTTPException) as exc_info:
        api.get_instrument(created["id"])
    assert exc_info.value.status_code == 404


def test_deleting_it_twice_is_a_404_the_second_time(app_env):
    created = api.create_instrument(make())
    api.delete_instrument(created["id"])
    with pytest.raises(HTTPException) as exc_info:
        api.delete_instrument(created["id"])
    assert exc_info.value.status_code == 404


def test_delete_leaves_the_others_alone(app_env):
    first = api.create_instrument(make(name="A guitar"))
    second = api.create_instrument(make(name="B guitar"))
    api.delete_instrument(first["id"])
    assert [i["id"] for i in api.list_instruments()] == [second["id"]]


def test_an_id_is_never_reused_after_a_delete(app_env):
    """A plain INTEGER PRIMARY KEY hands the largest deleted rowid to the next
    insert, so an id held anywhere outside the database - an open settings tab,
    a request in flight, a scores.instrument_id in a backup - would come to
    name a different instrument. Hence AUTOINCREMENT on this table."""
    first = api.create_instrument(make(name="First"))
    second = api.create_instrument(make(name="Second"))
    api.delete_instrument(second["id"])
    third = api.create_instrument(make(name="Third"))
    assert third["id"] not in (first["id"], second["id"])
    assert third["id"] > second["id"]


# ------------------------------------------------- ids the database cannot hold


def test_an_id_wider_than_sqlite_can_hold_is_refused_not_a_500(client):
    """SQLite's INTEGER is 64-bit and the driver raises OverflowError on
    anything wider, from inside the query - a 500 for what is only ever a row
    that cannot exist."""
    huge = str(2**64)
    for method, path in [
        ("GET", f"/api/instruments/{huge}"),
        ("DELETE", f"/api/instruments/{huge}"),
        ("GET", f"/api/scores/{huge}"),
    ]:
        response = client.request(method, path)
        assert response.status_code == 422, (method, path, response.status_code)


def test_an_oversized_id_in_a_request_body_is_refused(client):
    response = client.patch("/api/scores/1", json={"instrument_id": 2**64})
    assert response.status_code == 422


def test_a_zero_or_negative_id_is_refused(client):
    assert client.get("/api/instruments/0").status_code == 422
    assert client.get("/api/instruments/-1").status_code == 422


def test_an_ordinary_missing_id_is_still_a_404(client):
    """The bound must not turn "no such instrument" into a validation error."""
    assert client.get("/api/instruments/999").status_code == 404


# ------------------------------------------------- a score's instrument


def test_a_score_can_reference_an_instrument(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    guitar = api.create_instrument(make())
    patched = api.patch_score(score_id, api.ScorePatch(instrument_id=guitar["id"]))
    assert patched["instrument_id"] == guitar["id"]
    assert api.get_score(score_id)["instrument_id"] == guitar["id"]


def test_a_score_cannot_reference_an_instrument_that_does_not_exist(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    with pytest.raises(HTTPException) as exc_info:
        api.patch_score(score_id, api.ScorePatch(instrument_id=999))
    assert exc_info.value.status_code == 404


def test_a_scores_instrument_can_be_cleared(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    guitar = api.create_instrument(make())
    api.patch_score(score_id, api.ScorePatch(instrument_id=guitar["id"]))
    cleared = api.patch_score(score_id, api.ScorePatch(instrument_id=None))
    assert cleared["instrument_id"] is None


def test_patching_something_else_leaves_the_instrument_alone(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    guitar = api.create_instrument(make())
    api.patch_score(score_id, api.ScorePatch(instrument_id=guitar["id"]))
    patched = api.patch_score(score_id, api.ScorePatch(title="Renamed"))
    assert patched["title"] == "Renamed"
    assert patched["instrument_id"] == guitar["id"]


def test_deleting_an_instrument_leaves_its_scores_in_place(app_env, insert_score):
    """Deleting an instrument means the player no longer has it, not that the
    scores written for it are gone - the reference is dropped, the score stays."""
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    guitar = api.create_instrument(make())
    api.patch_score(score_id, api.ScorePatch(instrument_id=guitar["id"]))
    result = api.delete_instrument(guitar["id"])
    # reported rather than done silently: those scores were written for it
    assert result["scores_unlinked"] == 1
    score = api.get_score(score_id)
    assert score["instrument_id"] is None


def _editor_source() -> str:
    return (
        Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "pitch.js"
    ).read_text(encoding="utf-8")


# Every numeric constant web/src/lib/pitch.js mirrors from this module. The test
# below checks both directions: each of these must agree with its Python
# counterpart, AND pitch.js must export no other numeric constant - so mirroring
# a new bound over there without guarding it here is a failure rather than a
# silent gap.
MIRRORED_CONSTANTS = [
    "MIN_STRINGS",
    "MAX_STRINGS",
    "MIN_FRETS",
    "MAX_FRETS",
    "MIN_REFERENCE_HZ",
    "MAX_REFERENCE_HZ",
    "DEFAULT_REFERENCE_HZ",
    "MAX_NAME_CHARS",
    "MIN_MIDI",
    "MAX_MIDI",
    "REFERENCE_MIDI",
]


def test_the_constants_the_editor_mirrors_match_the_ones_here():
    """web/src/lib/pitch.js mirrors these so a number input can offer the right
    range and a draft can be checked before a save is attempted. Nothing
    connects the two at runtime - the editor never asks the server what is
    valid - so if they drift, the form accepts a value the server answers with a
    422 for no visible reason.

    WHAT THIS CANNOT DO, because it is a regex over a source file: tell whether
    the arithmetic using these numbers is right. Matching constants would pass
    happily while every frequency came out an octave wrong. That is covered by
    calling the functions - web/tests/unit/pitch.spec.js - and this is kept only
    for the narrow thing it is good for, which is catching a bound edited on one
    side and not the other."""
    found = dict(re.findall(r"export const ([A-Z][A-Z0-9_]*) = ([\d.]+);", _editor_source()))
    assert set(found) == set(MIRRORED_CONSTANTS), (
        "the editor's numeric constants and MIRRORED_CONSTANTS have diverged"
    )
    for name in MIRRORED_CONSTANTS:
        assert float(found[name]) == float(getattr(instruments, name)), name


def test_the_editors_pitch_spelling_matches_the_servers():
    """pitch.js needs to name the pitch a capo produces, and cannot call
    musicxml.spell_pitch to do it, so it carries the twelve names as a table.
    Which accidental each pitch class gets is not a free choice - it is what
    spell_pitch returns with no key signature - so this walks every note MIDI
    has and fails if the two ever disagree."""
    match = re.search(r"PITCH_CLASS_NAMES = \[([^\]]*)\]", _editor_source())
    assert match, "could not find PITCH_CLASS_NAMES in instruments.svelte.js"
    names = re.findall(r'"([^"]+)"', match.group(1))
    assert len(names) == 12
    for midi in range(0, 128):
        octave = midi // 12 - 1
        assert f"{names[midi % 12]}{octave}" == instruments.spell_midi(midi), midi


def _instrument_foreign_key(conn):
    return next(
        (
            dict(r)
            for r in conn.execute("PRAGMA foreign_key_list(scores)")
            if r["from"] == "instrument_id"
        ),
        None,
    )


def test_the_instrument_column_is_added_to_a_database_that_predates_it(app_env):
    """SCHEMA's CREATE TABLE IF NOT EXISTS does nothing to a scores table that
    already exists, so the column has to be added explicitly. Simulated by
    dropping it and re-running init_db, which is what an upgrade looks like.

    Asserts the FOREIGN KEY, not merely that a column of the right name turned
    up: the name is all PRAGMA table_info reports, and a column present without
    its REFERENCES clause is one where ON DELETE SET NULL never fires and a
    dangling id is accepted. A test that checked only the name would pass
    against exactly that broken state."""
    conn = db.connect()
    conn.execute("ALTER TABLE scores DROP COLUMN instrument_id")
    conn.commit()
    assert "instrument_id" not in {r["name"] for r in conn.execute("PRAGMA table_info(scores)")}
    assert _instrument_foreign_key(conn) is None

    db.init_db()

    assert "instrument_id" in {r["name"] for r in conn.execute("PRAGMA table_info(scores)")}
    key = _instrument_foreign_key(conn)
    assert key is not None, "the column came back without its foreign key"
    assert key["table"] == "instruments"
    assert key["to"] == "id"
    assert key["on_delete"] == "SET NULL"


def test_a_dangling_instrument_id_is_refused_by_the_database(app_env, insert_score):
    """What the foreign key is actually for. If it were missing, this write
    would succeed and the row would point at nothing."""
    import sqlite3

    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE scores SET instrument_id = 9999 WHERE id = ?", (score_id,))


def test_a_column_present_without_its_foreign_key_stops_startup(app_env):
    """The one hole in matching columns by name. SQLite cannot add a constraint
    to an existing column, so this is not repairable at startup - and carrying
    on would mean running with an integrity guarantee the code assumes and the
    database does not provide."""
    conn = db.connect()
    conn.execute("ALTER TABLE scores DROP COLUMN instrument_id")
    conn.execute("ALTER TABLE scores ADD COLUMN instrument_id TEXT")
    conn.commit()
    with pytest.raises(RuntimeError) as exc_info:
        db.init_db()
    message = str(exc_info.value)
    assert "missing the link" in message
    # The message is the only thing the person hitting this has to go on, and it
    # used to offer a non-administrator three dead ends - one of which silently
    # destroyed every score's instrument association. So the wording is part of
    # the behaviour: it has to say this is not their fault, invite a report, and
    # reach the backup BEFORE it mentions anything that loses data.
    assert "cannot happen through normal use" in message
    assert "open an issue" in message
    assert message.index("backup") < message.index("PERMANENTLY DISCARDS")
    assert "sheet music is not affected" in message


def test_the_schema_version_is_stamped(app_env):
    conn = db.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 1


def test_a_database_from_a_newer_release_stops_startup(app_env):
    """Its schema may hold columns and constraints this code knows nothing
    about; writing to it blind is how a downgrade loses data."""
    conn = db.connect()
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    conn.commit()
    with pytest.raises(RuntimeError) as exc_info:
        db.init_db()
    assert "newer release" in str(exc_info.value)


def test_a_database_from_a_newer_release_is_not_written_to_at_all(app_env, tmp_path, monkeypatch):
    """The refusal has to come BEFORE the schema script, not after it.
    executescript() commits as it goes, so a check that ran afterwards had
    already created tables in a database this code does not understand - while
    the guard's whole justification is that writing blind is how a downgrade
    loses data, and SCHEMA is written blind."""
    fresh = tmp_path / "from_the_future.db"
    monkeypatch.setattr(db, "DB_PATH", fresh)
    db._local.conn = None
    conn = db.connect()
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    conn.commit()

    with pytest.raises(RuntimeError):
        db.init_db()

    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert tables == set(), f"the refused startup still created {sorted(tables)}"


def test_running_init_db_again_changes_nothing(app_env):
    """Idempotence is this mechanism's only safety property - it runs on every
    startup."""
    conn = db.connect()
    before = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    db.init_db()
    db.init_db()
    after = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    assert [r["sql"] for r in before] == [r["sql"] for r in after]


# ---------------------------------------------------------------- kind


def test_kind_defaults_to_string(app_env):
    created = api.create_instrument(make())
    assert created["kind"] == "string"


def test_every_preset_is_a_string_instrument(app_env):
    assert {p["kind"] for p in instruments.presets()} == {"string"}


def test_an_unimplemented_kind_is_rejected(app_env):
    """`fretted: False` means an unfretted STRING instrument, not "not a string
    instrument" - this column is what a piano will use instead of it."""
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(kind="keyboard"))
    assert exc_info.value.status_code == 422
    assert "kind" in exc_info.value.detail


# ------------------------------------------------- stored spelling


@pytest.mark.parametrize(
    "typed,stored",
    [
        ("e2", "E2"),
        ("f#2", "F#2"),
        ("eb3", "Eb3"),
        (" g3 ", "G3"),
    ],
)
def test_a_pitch_is_stored_in_one_spelling(app_env, typed, stored):
    """"e2" and "E2" are the same string. Storing it as typed renders lowercase
    in the editor and the summary, and makes any later comparison by name -
    against a preset, against tabextract.DEFAULT_TUNING - miss."""
    created = api.create_instrument(
        make(string_count=1, string_pitches=[typed], fret_count=12)
    )
    assert created["string_pitches"] == [stored]
    assert created["strings"][0]["pitch"] == stored


def test_the_choice_of_accidental_is_kept_as_written(app_env):
    """E flat and D sharp are the same pitch and a real distinction to whoever
    typed one, so canonicalising case must not canonicalise spelling."""
    flat = api.create_instrument(
        make(name="Flat", string_count=1, string_pitches=["Eb3"], fret_count=12)
    )
    sharp = api.create_instrument(
        make(name="Sharp", string_count=1, string_pitches=["D#3"], fret_count=12)
    )
    assert flat["string_pitches"] == ["Eb3"]
    assert sharp["string_pitches"] == ["D#3"]
    assert flat["strings"][0]["midi"] == sharp["strings"][0]["midi"]
